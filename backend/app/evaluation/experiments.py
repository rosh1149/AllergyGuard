"""Research experiments (Stage 4): held-out evaluation, baselines, ablations, component metrics and
performance, recorded with everything needed to reproduce them (plan points 17-20, 28).

    python -m app.evaluation.experiments --dev-only        # develop/check on the development set only
    python -m app.evaluation.experiments --final OUT_DIR   # the one-off run that includes the held-out set
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.engine import AllergyProfile, assess
from app.engine.ablation import Ablation, ablated
from app.engine.types import AnalysisContext
from app.evaluation import heldout_runner as H
from app.evaluation.baselines import BASELINES, ingredient_links
from app.evaluation.runner import DATASETS, NOW, build_source, evaluate, load
from app.evaluation.seed_kb import build_kb

NOT_LISTED = "NOT_LISTED_IN_VERIFIED_RECORDS"
TRUTH_FROM_EXPECTED = {"PRESENT": "present", "POSSIBLE_CROSS_CONTACT": "cross_contact", "UNKNOWN": "undeterminable",
                       NOT_LISTED: "absent"}
ABLATIONS = [("full", Ablation())] + [(f.name, Ablation(**{f.name: True})) for f in Ablation.__dataclass_fields__.values()]


# ---- reproducibility record ----


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _kb_hash(kb) -> str:
    body = json.dumps([sorted((s, c.name, c.concept_type, c.ambiguous_source) for s, c in kb.concepts.items()),
                       sorted(kb.alias_to_concept.items()), sorted(kb.relations),
                       sorted((t, c) for t, c, _ in kb._synonyms), sorted((t, c) for t, c, _ in kb._exclusions)])
    return hashlib.sha256(body.encode()).hexdigest()


def _git() -> dict:
    def run(*a):
        try:
            return subprocess.run(["git", *a], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--", "app"))}


def manifest(kb, include_heldout: bool) -> dict:
    from app.core.config import get_settings

    s = get_settings()
    try:
        tess = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
    except Exception:
        tess = "unavailable"
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": _git(),
        "python": sys.version.split()[0], "platform": platform.platform(), "tesseract": tess,
        "knowledge_base": {"version": kb.version, "sha256": _kb_hash(kb), "concepts": len(kb.concepts),
                           "relations": len(kb.relations)},
        "datasets": {"development": {p.stem: _sha(p) for p in sorted(DATASETS.glob("*.json"))},
                     "heldout": ({p.stem: _sha(p) for p in sorted(H.HERE.glob("*.json"))} if include_heldout else "not used")},
        "settings": {k: getattr(s, k) for k in ("fuzzy_phrase_threshold", "fuzzy_word_threshold", "fuzzy_min_length",
                                                "fuzzy_margin", "restaurant_record_max_age_days",
                                                "product_record_max_age_days")},
        "evaluation_time": NOW.isoformat(),
    }


# ---- scenario metrics (truth labels) ----


def _scenarios(include_heldout: bool) -> list[dict]:
    if include_heldout:
        return H.load("scenarios")
    return [{**c, "truth": {a: TRUTH_FROM_EXPECTED[o] for a, o in c["expect"].items()}} for c in load("safety")]


def _proposed(kb, case) -> dict[str, str]:
    sources = [build_source(case["id"], i, s) for i, s in enumerate(case["sources"])]
    r = assess(kb, sources, AllergyProfile(set(case["truth"])), AnalysisContext(now=NOW, **case.get("context", {})))
    return {a.code: a.outcome.value for a in r.your_allergens}


def _baseline(fn):
    def run(kb, case):
        sources = [build_source(case["id"], i, s) for i, s in enumerate(case["sources"])]
        return fn(kb, sources, set(case["truth"]))
    return run


def score(kb, cases, method) -> dict:
    tp = fp = tn = fn = unknown = exact = n = 0
    errors, times = [], []
    for c in cases:
        t = time.perf_counter()
        got = method(kb, c)
        times.append((time.perf_counter() - t) * 1000)
        for code, truth in c["truth"].items():
            o = got.get(code)
            warn, should = o != NOT_LISTED, truth != "absent"
            n += 1
            unknown += o == "UNKNOWN"
            tp += warn and should
            fp += warn and not should
            tn += not warn and not should
            fn += not warn and should
            exact += ((truth == "present" and o == "PRESENT") or (truth == "absent" and o == NOT_LISTED)
                      or (truth == "cross_contact" and o in ("POSSIBLE_CROSS_CONTACT", "PRESENT"))
                      or (truth == "undeterminable" and o == "UNKNOWN"))
            if warn != should:
                errors.append({"case": c["id"], "allergen": code, "truth": truth, "got": o,
                               "kind": "FALSE NEGATIVE (unsafe)" if should else "false positive (over-warning)",
                               "tags": c.get("tags", []), "why": c.get("justification") or c.get("note", "")})
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"checks": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "accuracy": round((tp + tn) / n, 4) if n else None,
            "precision": round(p, 4) if p is not None else None, "recall": round(r, 4) if r is not None else None,
            "f1": round(2 * p * r / (p + r), 4) if p and r else None,
            "unknown_share": round(unknown / n, 4) if n else None,
            "exact_outcome_accuracy": round(exact / n, 4) if n else None,
            "ms_per_case_mean": round(statistics.mean(times), 3), "errors": errors}


def ingredient_scores(kb, method, include_heldout: bool) -> dict:
    if include_heldout:
        sets = [("ingredients", H.load("ingredients"), False), ("ocr", H.load("ocr"), True)]
    else:  # development reasoning set, as must / must-not
        dev = [{"id": c["input"], "input": c["input"], "must": c["definite"] + c.get("possible", []),
                "must_not": []} for c in load("reasoning")]
        sets = [("reasoning (dev)", dev, False)]
    out = {}
    for name, cases, ocr in sets:
        hit = need = false = guarded = 0
        for c in cases:
            got = ingredient_links(method, kb, c["input"], ocr)
            need += len(c["must"])
            hit += len(set(c["must"]) & got)
            guarded += len(c["must_not"])
            false += len(set(c["must_not"]) & got)
        out[name] = {"link_recall": round(hit / need, 4) if need else None,
                     "false_link_rate": round(false / guarded, 4) if guarded else None}
    return out


# ---- performance ----


def performance(kb, cases, repeats: int = 20) -> dict:
    times = []
    tracemalloc.start()
    t0 = time.perf_counter()
    for _ in range(repeats):
        for c in cases:
            t = time.perf_counter()
            _proposed(kb, c)
            times.append((time.perf_counter() - t) * 1000)
    total = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    times.sort()
    return {"engine_runs": len(times), "ms_mean": round(statistics.mean(times), 3), "ms_p50": round(times[len(times) // 2], 3),
            "ms_p95": round(times[int(len(times) * 0.95)], 3), "ms_max": round(times[-1], 3),
            "throughput_per_s": round(len(times) / total, 1), "peak_traced_memory_mb": round(peak / 1e6, 2),
            "note": "Engine only (no OCR, no network), single process, warm caches after the first repeat."}


def api_load(db_url: str, port: int = 8030, clients: int = 8, per_client: int = 25) -> dict:
    """Start the API on a copy of the configuration, then send concurrent text analyses."""
    import os
    import threading

    import httpx

    env = {**os.environ, "DATABASE_URL": db_url, "SEED_DEMO_DATA": "true", "BACKGROUND_INLINE": "false"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/api"
    try:
        for _ in range(120):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        email = f"load{int(time.time())}@example.com"
        tok = httpx.post(base + "/auth/register/customer", json={
            "email": email, "password": "Passw0rd!", "full_name": "Load", "allergy_profile": {
                "allergen_codes": ["milk", "peanuts", "gluten"], "custom_allergies": []}}).json()["access_token"]
        texts = ["wheat flour, sugar, butter (milk), salt", "rice, sunflower oil, paprika, onion",
                 "chocolate (sugar, cocoa mass, milk powder), hazelnuts", "chickpeas, tahini, lemon, garlic"]
        lat: list[float] = []
        errors = 0
        lock = threading.Lock()

        def worker(i):
            nonlocal errors
            with httpx.Client(timeout=30) as c:
                for j in range(per_client):
                    t = time.perf_counter()
                    r = c.post(base + "/analyze/text", headers={"Authorization": f"Bearer {tok}"},
                               json={"ingredients_text": texts[(i + j) % len(texts)]})
                    with lock:
                        lat.append((time.perf_counter() - t) * 1000)
                        errors += r.status_code != 200

        t0 = time.perf_counter()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        total = time.perf_counter() - t0
        rss = 0
        for line in Path(f"/proc/{proc.pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) / 1024
        lat.sort()
        return {"requests": len(lat), "concurrent_clients": clients, "errors": errors,
                "ms_p50": round(lat[len(lat) // 2], 1), "ms_p95": round(lat[int(len(lat) * 0.95)], 1),
                "ms_max": round(lat[-1], 1), "throughput_per_s": round(len(lat) / total, 1),
                "server_rss_mb": round(rss, 1),
                "note": "POST /api/analyze/text incl. auth, database writes (stored assessment) and JSON; one uvicorn worker."}
    finally:
        proc.terminate()
        proc.wait(timeout=20)


# ---- run ----


def run(include_heldout: bool, api_db: str | None = None) -> tuple[dict, dict]:
    kb = build_kb()
    if include_heldout:
        problems = H.verify_manifest()
        if problems:
            raise SystemExit("Held-out set modified: " + "; ".join(problems))
    cases = _scenarios(include_heldout)
    results: dict = {"set": "held-out test set" if include_heldout else "development safety set"}

    methods = {"proposed": _proposed, **{k: _baseline(f) for k, f in BASELINES.items()}}
    results["comparison"] = {k: score(kb, cases, m) for k, m in methods.items()}
    results["ingredient_level"] = {k: ingredient_scores(kb, k, include_heldout) for k in methods}

    results["ablations"] = {}
    for name, ab in ABLATIONS:
        with ablated(**{k: v for k, v in asdict(ab).items()}):
            s = score(kb, cases, _proposed)
            dev = evaluate(kb)
        results["ablations"][name] = {
            **{k: s[k] for k in ("accuracy", "precision", "recall", "f1", "fn", "fp", "unknown_share", "exact_outcome_accuracy")},
            "dev_safety_false_negatives": dev["safety"].metrics["false_negative_count"],
            "dev_unsafe_absences": dev["uncertainty"].metrics["unsafe_absence_count"] + dev["evidence_fusion"].metrics["unsafe_absence_count"],
            "dev_reasoning_accuracy": dev["semantic_reasoning"].metrics["relationship_accuracy"],
            "dev_normalization_accuracy": dev["normalization"].metrics["exact_match_accuracy"],
            "errors": s["errors"],
        }

    dev = evaluate(kb)
    results["components_dev"] = {area: r.metrics for area, r in dev.items()}
    if include_heldout:
        results["heldout_ingredients"] = H._ingredient_metrics(kb, H.load("ingredients"), False)
        results["heldout_ocr"] = H._ingredient_metrics(kb, H.load("ocr"), True)
    results["performance"] = performance(kb, cases)
    import resource

    results["performance"]["process_max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    if api_db:
        results["api_load"] = api_load(api_db)
    return manifest(kb, include_heldout), results


def _fmt(v):
    return "–" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def render(man: dict, res: dict) -> str:
    L = [f"# Experiment results ({res['set']})", "",
         f"Run {man['date']} at commit `{man['git']['commit'][:10]}`{' (uncommitted changes)' if man['git']['dirty'] else ''}; "
         f"knowledge base sha256 `{man['knowledge_base']['sha256'][:12]}`; {man['tesseract']}.", "",
         "Positive = the allergen is present, may be present by cross-contact, or cannot be determined (the system "
         "must warn). Negative = absent and the evidence is complete and verified. A method *warns* unless it says "
         "NOT LISTED. A false negative is a missed warning (unsafe).", "",
         "## Proposed system vs baselines", "",
         "| Method | Accuracy | Precision | Recall | F1 | False negatives | False positives | Unknown share | Exact outcome | ms/case |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in res["comparison"].items():
        L.append(f"| {k} | {_fmt(m['accuracy'])} | {_fmt(m['precision'])} | {_fmt(m['recall'])} | {_fmt(m['f1'])} | "
                 f"{m['fn']} | {m['fp']} | {_fmt(m['unknown_share'])} | {_fmt(m['exact_outcome_accuracy'])} | {_fmt(m['ms_per_case_mean'])} |")
    L += ["", "## Ingredient level (allergen links per ingredient)", "", "| Method | Set | Link recall | False-link rate |", "|---|---|---|---|"]
    for k, sets in res["ingredient_level"].items():
        for name, m in sets.items():
            L.append(f"| {k} | {name} | {_fmt(m['link_recall'])} | {_fmt(m['false_link_rate'])} |")
    L += ["", "## Ablations (one component switched off)", "",
          "| Configuration | Accuracy | Recall | False negatives | False positives | Unknown share | Dev safety FN | Dev unsafe absences | Dev reasoning acc. | Dev normalization acc. |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in res["ablations"].items():
        L.append(f"| {k} | {_fmt(m['accuracy'])} | {_fmt(m['recall'])} | {m['fn']} | {m['fp']} | {_fmt(m['unknown_share'])} | "
                 f"{m['dev_safety_false_negatives']} | {m['dev_unsafe_absences']} | {_fmt(m['dev_reasoning_accuracy'])} | {_fmt(m['dev_normalization_accuracy'])} |")
    if "heldout_ingredients" in res:
        L += ["", "## Held-out ingredient and OCR sets (proposed)", ""]
        for name in ("heldout_ingredients", "heldout_ocr"):
            m = res[name]
            L.append(f"- {name}: {m['cases']} cases, link recall {_fmt(m['link_recall'])}, false-link rate {_fmt(m['false_link_rate'])}")
            L += [f"  - {x}" for x in m["misses"] + m["wrong"]]
    p = res["performance"]
    L += ["", "## Performance (engine)", "",
          f"{p['engine_runs']} runs: mean {p['ms_mean']} ms, P50 {p['ms_p50']} ms, P95 {p['ms_p95']} ms, max {p['ms_max']} ms; "
          f"{p['throughput_per_s']} assessments/s; peak traced memory {p['peak_traced_memory_mb']} MB; process max RSS "
          f"{p.get('process_max_rss_mb', '–')} MB. {p['note']}", ""]
    if "api_load" in res:
        a = res["api_load"]
        L += ["## Performance (API under load)", "",
              f"{a['requests']} requests from {a['concurrent_clients']} concurrent clients: P50 {a['ms_p50']} ms, P95 {a['ms_p95']} ms, "
              f"max {a['ms_max']} ms; {a['throughput_per_s']} requests/s; {a['errors']} errors; server RSS {a['server_rss_mb']} MB. {a['note']}", ""]
    L += [
          "## Errors of the proposed system", ""]
    errs = res["comparison"]["proposed"]["errors"]
    L += [f"- {e['kind']}: {e['case']} {e['allergen']} (truth {e['truth']}, got {e['got']}). {e['why']}" for e in errs] or ["None."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dev-only", action="store_true")
    g.add_argument("--final", type=Path, metavar="OUT_DIR")
    ap.add_argument("--api-db", help="database URL for the API load test (optional)")
    a = ap.parse_args(argv)
    man, res = run(include_heldout=bool(a.final), api_db=a.api_db)
    text = render(man, res)
    if a.final:
        a.final.mkdir(parents=True, exist_ok=True)
        (a.final / "manifest.json").write_text(json.dumps(man, indent=1))
        (a.final / "results.json").write_text(json.dumps(res, indent=1, default=str))
        (a.final / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
