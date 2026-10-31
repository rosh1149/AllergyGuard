"""Held-out test set: run once, at the evaluation stage, never while changing the engine.

The cases in `heldout/` were written on 2026-10-31, before the Stage 1 knowledge changes, and are
frozen by `heldout/MANIFEST.json` (SHA-256 per file). Labels are ground truth about the food:

  ingredients / ocr: `must` = allergens the ingredient truly carries; `must_not` = tempting allergens it
                     does not carry.
  scenarios:         per allergen `present`, `cross_contact`, `absent` (not in the food AND the evidence
                     is complete and verified) or `undeterminable` (the evidence cannot settle it).

    python -m app.evaluation.heldout_runner --final      # prints metrics; refuses without --final
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent / "heldout"
NOT_LISTED = "NOT_LISTED_IN_VERIFIED_RECORDS"


def load(name: str) -> list[dict]:
    return json.loads((HERE / f"{name}.json").read_text())["cases"]


def digest(name: str) -> str:
    return hashlib.sha256((HERE / f"{name}.json").read_bytes()).hexdigest()


def verify_manifest() -> list[str]:
    manifest = json.loads((HERE / "MANIFEST.json").read_text())
    return [f"{name}.json changed after it was frozen" for name, h in manifest["sha256"].items() if digest(name) != h]


def _flagged(kb, text: str, ocr: bool) -> set[str]:
    from app.engine.assess import resolution_out
    from app.engine.normalize import normalize_ingredients

    out: set[str] = set()
    for n in normalize_ingredients(kb, text, ocr=ocr):
        r = resolution_out(kb, "heldout", n)
        out |= set(r.allergens) | set(r.possible_allergens)
    return out


def _ingredient_metrics(kb, cases, ocr) -> dict:
    hit = need = false = guarded = 0
    misses, wrong = [], []
    for c in cases:
        got = _flagged(kb, c["input"], ocr)
        for a in c["must"]:
            need += 1
            hit += a in got
            if a not in got:
                misses.append(f"{c['id']} {c['input']!r}: {a} not linked")
        for a in c["must_not"]:
            guarded += 1
            false += a in got
            if a in got:
                wrong.append(f"{c['id']} {c['input']!r}: {a} wrongly linked")
    return {"cases": len(cases), "link_recall": round(hit / need, 4) if need else None,
            "false_link_rate": round(false / guarded, 4) if guarded else None, "misses": misses, "wrong": wrong}


def _scenario_metrics(kb) -> dict:
    from app.evaluation.runner import build_source
    from app.engine import AllergyProfile, assess
    from app.engine.types import AnalysisContext
    from app.evaluation.runner import NOW

    fn = unsafe_den = det = present = cc_ok = cc = over = absent = 0
    failures = []
    for c in load("scenarios"):
        sources = [build_source(c["id"], i, s) for i, s in enumerate(c["sources"])]
        r = assess(kb, sources, AllergyProfile(set(c["truth"])), AnalysisContext(now=NOW, **c.get("context", {})))
        got = {a.code: a.outcome.value for a in r.your_allergens}
        for code, truth in c["truth"].items():
            o = got.get(code)
            if truth != "absent":
                unsafe_den += 1
                if o == NOT_LISTED:
                    fn += 1
                    failures.append(f"FALSE NEGATIVE {c['id']} {code}: truth {truth}, got {o}")
            if truth == "present":
                present += 1
                det += o == "PRESENT"
            elif truth == "cross_contact":
                cc += 1
                cc_ok += o in ("POSSIBLE_CROSS_CONTACT", "PRESENT")
            elif truth == "absent":
                absent += 1
                over += o != NOT_LISTED
            if (truth == "present" and o != "PRESENT") or (truth == "absent" and o != NOT_LISTED):
                failures.append(f"{c['id']} {code}: truth {truth}, got {o}")
    return {"scenarios": len(load("scenarios")), "false_negatives": fn,
            "false_negative_rate": round(fn / unsafe_den, 4) if unsafe_den else None,
            "present_detected_as_present": round(det / present, 4) if present else None,
            "cross_contact_flagged": round(cc_ok / cc, 4) if cc else None,
            "absent_reported_not_listed": round((absent - over) / absent, 4) if absent else None,
            "failures": failures}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="confirm this is the one-off final evaluation")
    args = ap.parse_args(argv)
    problems = verify_manifest()
    if problems:
        print("Held-out set was modified after freezing:", *problems, sep="\n  ")
        return 2
    if not args.final:
        print("Refusing: the held-out set is for the final evaluation only. Re-run with --final.")
        return 1
    from app.evaluation.seed_kb import build_kb

    kb = build_kb()
    print(json.dumps({"ingredients": _ingredient_metrics(kb, load("ingredients"), False),
                      "ocr": _ingredient_metrics(kb, load("ocr"), True),
                      "scenarios": _scenario_metrics(kb)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
