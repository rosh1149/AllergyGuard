"""Compute benchmark metrics for each layer: normalization, OCR correction, synonyms, ontology mapping,
semantic reasoning, compound ingredients, evidence fusion, provenance, uncertainty and safety."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.assess import assess, resolution_out
from app.engine.knowledge import KnowledgeBase
from app.engine.normalize import clean, normalize_ingredients, parse_ingredients
from app.engine.types import (
    AllergyProfile,
    AnalysisContext,
    AnalysisResult,
    CrossContactDecl,
    EvidenceSource,
    Outcome,
    SourceType,
)

DATASETS = Path(__file__).parent / "datasets"
NOW = datetime(2026, 10, 1, tzinfo=UTC)
WARNING = {Outcome.PRESENT.value, Outcome.POSSIBLE_CROSS_CONTACT.value}
NOT_LISTED = Outcome.NOT_LISTED_IN_VERIFIED_RECORDS.value


def load(name: str) -> list[dict]:
    return json.loads((DATASETS / f"{name}.json").read_text())["cases"]


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


@dataclass
class AreaResult:
    metrics: dict[str, float | int | None]
    failures: list[str] = field(default_factory=list)
    cases: int = 0


def _resolve(kb: KnowledgeBase, text: str, ocr: bool = False):
    """The head (first top-level) item of `text`, as the engine resolves it."""
    items = normalize_ingredients(kb, text, ocr=ocr)
    head = next((n for n in items if n.role in ("ingredient", "compound", "qualified")), items[0])
    return head, resolution_out(kb, "eval", head)


# ---- 1. normalization: written text -> normalized text ----


def eval_normalization(kb) -> AreaResult:
    cases = load("normalization")
    exact = tp = fp = fn = 0
    failures = []
    for c in cases:
        head, _ = _resolve(kb, c["input"], c.get("ocr", False))
        got, want = head.text, c["expected"]
        changed, should_change = got != clean(c["input"]), want != clean(c["input"])
        if got == want:
            exact += 1
            tp += changed
        else:
            failures.append(f"{c['input']!r}: expected {want!r}, got {got!r}")
            fp += changed
            fn += should_change and not changed
    return AreaResult({"exact_match_accuracy": _ratio(exact, len(cases)), "precision": _ratio(tp, tp + fp),
                       "recall": _ratio(tp, tp + fn)}, failures, len(cases))


# ---- 2. OCR correction ----


def eval_ocr(kb) -> AreaResult:
    cases = load("ocr_correction")
    should = [c for c in cases if c["expected"]]
    should_not = [c for c in cases if not c["expected"]]
    correct = false_corrections = 0
    failures = []
    for c in should:
        head, _ = _resolve(kb, c["input"], ocr=True)
        if head.text == c["expected"]:
            correct += 1
        else:
            failures.append(f"{c['input']!r}: expected correction to {c['expected']!r}, got {head.text!r}")
    for c in should_not:
        head, _ = _resolve(kb, c["input"], ocr=True)
        if head.method == "ocr_correction":
            false_corrections += 1
            failures.append(f"{c['input']!r}: must not be corrected, got {head.text!r}")
    return AreaResult({"correction_accuracy": _ratio(correct, len(should)),
                       "false_correction_rate": _ratio(false_corrections, len(should_not))}, failures, len(cases))


# ---- 3./4. synonyms and ontology mapping: text -> canonical concept ----


def _concept_pr(kb, name: str) -> AreaResult:
    cases = load(name)
    tp = fp = fn = 0
    failures = []
    for c in cases:
        _, r = _resolve(kb, c["input"])
        got, want = r.concept, c["concept"]
        if got == want:
            tp += got is not None
            continue
        failures.append(f"{c['input']!r}: expected {want}, got {got}")
        fp += got is not None
        fn += want is not None
    return AreaResult({"precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn)}, failures, len(cases))


def eval_synonyms(kb) -> AreaResult:
    return _concept_pr(kb, "synonyms")


def eval_ontology(kb) -> AreaResult:
    res = _concept_pr(kb, "ontology")
    mapped = [c for c in load("ontology") if c["concept"]]
    with_id = sum(1 for c in mapped if kb.concepts[c["concept"]].foodon_id) if mapped else 0
    res.metrics["foodon_id_coverage"] = _ratio(with_id, len(mapped))
    return res


# ---- 5. semantic reasoning: ingredient -> definite / possible allergens ----


def eval_reasoning(kb) -> AreaResult:
    cases = load("reasoning")
    correct = missed_definite = 0
    failures = []
    for c in cases:
        _, r = _resolve(kb, c["input"])
        definite, possible = set(r.allergens), set(r.possible_allergens)
        want_d, want_p = set(c["definite"]), set(c.get("possible", []))
        if (definite, possible) == (want_d, want_p):
            correct += 1
        else:
            failures.append(f"{c['input']!r}: expected definite {sorted(want_d)} possible {sorted(want_p)}, "
                            f"got {sorted(definite)} / {sorted(possible)}")
        missed_definite += bool(want_d - definite - possible)  # an allergen not reported at all
    return AreaResult({"relationship_accuracy": _ratio(correct, len(cases)),
                       "missed_allergen_count": missed_definite}, failures, len(cases))


# ---- 6. compound ingredients ----


def eval_compound(kb) -> AreaResult:
    cases = load("compound")
    exact = item_tp = item_fp = item_fn = 0
    failures = []
    for c in cases:
        got = {(p.role, p.original.lower(), (p.parent or "").lower()) for p in parse_ingredients(c["input"])}
        want = {(i[0], i[1].lower(), (i[2] or "").lower()) for i in c["items"]}
        item_tp += len(got & want)
        item_fp += len(got - want)
        item_fn += len(want - got)
        if got == want:
            exact += 1
        else:
            failures.append(f"{c['input']!r}: missing {sorted(want - got)}, unexpected {sorted(got - want)}")
    return AreaResult({"extraction_accuracy": _ratio(exact, len(cases)), "item_precision": _ratio(item_tp, item_tp + item_fp),
                       "item_recall": _ratio(item_tp, item_tp + item_fn)}, failures, len(cases))


# ---- scenario helpers (fusion, uncertainty, safety) ----


def build_source(case_id: str, i: int, spec: dict) -> EvidenceSource:
    t = SourceType(spec["type"])
    age = spec.get("age_days")
    return EvidenceSource(
        id=f"{case_id}:{i}",
        type=t,
        name=spec.get("name", f"{t.value} {i}"),
        ingredients_text=spec.get("text"),
        ingredients=spec.get("items", []),
        declared_allergens=spec.get("declared"),
        cross_contact=[CrossContactDecl(x["allergen"], x.get("level", "possible"), x.get("description", ""),
                                        kind=x.get("kind", "other")) for x in spec.get("cross_contact", [])],
        cross_contact_text=spec.get("may_contain"),
        complete=spec.get("complete", True),
        confidence=spec.get("confidence", 1.0),
        reference=f"benchmark {case_id} source {i}",
        verification=spec.get("verification", "benchmark"),
        as_of=NOW - timedelta(days=age) if age is not None else NOW,
        max_age_days=spec.get("max_age_days", 365 if t == SourceType.restaurant_verified else None),
        cross_contact_assessed=spec.get("cross_contact_assessed"),
    )


def run_scenario(kb, case: dict) -> AnalysisResult:
    sources = [build_source(case["id"], i, s) for i, s in enumerate(case["sources"])]
    ctx = AnalysisContext(now=NOW, **case.get("context", {}))
    return assess(kb, sources, AllergyProfile(set(case["expect"])), ctx)


def outcomes(result: AnalysisResult) -> dict[str, str]:
    return {a.code: a.outcome.value for a in result.your_allergens}


@dataclass
class ScenarioRun:
    case: dict
    result: AnalysisResult
    got: dict[str, str]


def run_all(kb, name: str) -> list[ScenarioRun]:
    out = []
    for c in load(name):
        r = run_scenario(kb, c)
        out.append(ScenarioRun(c, r, outcomes(r)))
    return out


def _mismatches(run: ScenarioRun) -> list[str]:
    return [f"{run.case['id']} {code}: expected {want}, got {run.got.get(code)}"
            for code, want in run.case["expect"].items() if run.got.get(code) != want]


def _unsafe(run: ScenarioRun) -> list[str]:
    """NOT LISTED reported where the expected answer is anything else."""
    return [code for code, want in run.case["expect"].items() if want != NOT_LISTED and run.got.get(code) == NOT_LISTED]


# ---- 7. evidence fusion ----


def eval_fusion(kb, runs: list[ScenarioRun]) -> AreaResult:
    agree_ok = agree_n = conflict_ok = conflict_n = 0
    failures = []
    for run in runs:
        bad = _mismatches(run)
        kinds = {c.kind for c in run.result.conflicts}
        want = set(run.case.get("conflicts", []))
        if want:
            conflict_n += 1
            missing = want - kinds
            if not bad and not missing and all(c.resolution for c in run.result.conflicts):
                conflict_ok += 1
            elif missing:
                bad.append(f"{run.case['id']}: conflict(s) not detected: {sorted(missing)}")
        else:
            agree_n += 1
            if not bad and not kinds:
                agree_ok += 1
            elif kinds:
                bad.append(f"{run.case['id']}: unexpected conflict(s) {sorted(kinds)}")
        failures += bad
    return AreaResult({"agreement_accuracy": _ratio(agree_ok, agree_n),
                       "conflict_resolution_accuracy": _ratio(conflict_ok, conflict_n),
                       "unsafe_absence_count": sum(len(_unsafe(r)) for r in runs)}, failures, len(runs))


# ---- 8. provenance ----


def _traceable(result: AnalysisResult) -> tuple[int, int, list[str]]:
    ok = total = 0
    problems = []
    for a in result.your_allergens:
        total += 1
        if a.outcome.value in WARNING:
            good = bool(a.reasons) and bool(a.evidence) and bool(a.rules) and all(
                e.source_id and e.source_reference and e.kind and (e.ingredient or e.detail or e.matched_term)
                and (not e.concept or e.relationship_path) for e in a.evidence)
        elif a.outcome.value == NOT_LISTED:
            good = bool(a.rules) and any(s.used_for_absence and s.reference for s in result.sources)
        else:
            good = bool(a.reasons) and bool(a.rules)
        ok += good
        if not good:
            problems.append(a.code)
    return ok, total, problems


def eval_provenance(runs: list[ScenarioRun]) -> AreaResult:
    ok = total = 0
    failures = []
    for run in runs:
        o, t, problems = _traceable(run.result)
        ok, total = ok + o, total + t
        failures += [f"{run.case['id']} {p}: finding is not fully traceable" for p in problems]
    return AreaResult({"traceability_coverage": _ratio(ok, total)}, failures, total)


# ---- 9. uncertainty ----


def eval_uncertainty(kb, runs: list[ScenarioRun]) -> AreaResult:
    correct = sum(1 for r in runs if not _mismatches(r))
    failures = [m for r in runs for m in _mismatches(r)]
    return AreaResult({"handling_accuracy": _ratio(correct, len(runs)),
                       "unsafe_absence_count": sum(len(_unsafe(r)) for r in runs)}, failures, len(runs))


# ---- 10. safety (curated end-to-end allergen dataset) ----


def eval_safety(kb, runs: list[ScenarioRun]) -> AreaResult:
    pos = neg = fn = fp = correct = total = 0
    failures = []
    by_tag: dict[str, list[int]] = {}
    for run in runs:
        for code, want in run.case["expect"].items():
            got = run.got.get(code)
            total += 1
            correct += got == want
            if want != NOT_LISTED:
                pos += 1
                if got == NOT_LISTED:
                    fn += 1
                    failures.append(f"FALSE NEGATIVE {run.case['id']} {code}: expected {want}, got {got}")
            else:
                neg += 1
                if got == Outcome.PRESENT.value:
                    fp += 1
            if got != want and got != NOT_LISTED:
                failures.append(f"{run.case['id']} {code}: expected {want}, got {got}")
            for tag in run.case.get("tags", []):
                stats = by_tag.setdefault(tag, [0, 0])
                stats[0] += got == want
                stats[1] += 1
    metrics = {"false_negative_rate": _ratio(fn, pos), "false_negative_count": fn,
               "false_positive_rate": _ratio(fp, neg), "outcome_accuracy": _ratio(correct, total),
               "positive_checks": pos, "negative_checks": neg}
    metrics.update({f"accuracy_{t}": _ratio(a, n) for t, (a, n) in sorted(by_tag.items())})
    return AreaResult(metrics, failures, len(runs))


def evaluate(kb: KnowledgeBase) -> dict[str, AreaResult]:
    fusion, uncertainty, safety = run_all(kb, "fusion"), run_all(kb, "uncertainty"), run_all(kb, "safety")
    return {
        "normalization": eval_normalization(kb),
        "ocr_correction": eval_ocr(kb),
        "synonyms": eval_synonyms(kb),
        "ontology_mapping": eval_ontology(kb),
        "semantic_reasoning": eval_reasoning(kb),
        "compound_ingredients": eval_compound(kb),
        "evidence_fusion": eval_fusion(kb, fusion),
        "provenance": eval_provenance(fusion + uncertainty + safety),
        "uncertainty": eval_uncertainty(kb, uncertainty),
        "safety": eval_safety(kb, safety),
    }
