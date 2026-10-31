"""Step F: evaluation benchmark and release gate."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.evaluation import gate
from app.evaluation.__main__ import main
from app.evaluation.runner import evaluate, load
from app.evaluation.seed_kb import build_kb
from app.models import ConceptRelation, IngredientConcept
from app.seed.concept_data import RELATIONS
from app.engine.knowledge import KnowledgeBase
from app.seed.seed import DEMO_ADMIN_EMAILS
from tests.helpers import login


@pytest.fixture(scope="module")
def results():
    return evaluate(build_kb())


def test_benchmark_passes_release_gate(results):
    g = gate.check(results)
    assert g.passed, [(c.metric, c.value, c.rule) for c in g.failed]
    assert results["safety"].metrics["false_negative_count"] == 0
    assert results["provenance"].metrics["traceability_coverage"] == 1.0


def test_benchmark_covers_required_cases():
    reasoning = {c["input"] for c in load("reasoning")}
    assert {"casein", "sodium caseinate", "whey powder", "almond flour", "soy lecithin", "cocoa powder"} <= reasoning
    assert any(c["input"] == "caseln" for c in load("ocr_correction"))
    assert any("conflicts" in c for c in load("fusion"))
    tags = {t for c in load("safety") for t in c["tags"]}
    assert {"direct", "derived", "compound", "cross_contact", "ocr", "conflict", "regression", "negative"} <= tags


def _kb_without(*subjects) -> KnowledgeBase:
    """The seed knowledge base with some relations removed: a simulated bad knowledge change."""
    import app.evaluation.seed_kb as sk

    kb = build_kb()
    return KnowledgeBase(
        list(kb.allergens.values()), [(t, c) for t, c, _ in kb._synonyms], [(t, c) for t, c, _ in kb._exclusions],
        concepts=list(kb.concepts.values()), aliases=list(kb.alias_to_concept.items()),
        relations=[r for r in RELATIONS if r[0] not in subjects], hierarchy=sk.ALLERGEN_HIERARCHY,
        regulated={}, modifiers=kb.modifiers, version=99,
    )


def test_gate_blocks_lost_allergen_links():
    bad = evaluate(_kb_without("sodium_caseinate", "casein", "whey"))
    g = gate.check(bad)
    assert not g.passed
    critical = {c.metric for c in g.failed if c.level == "critical"}
    assert "safety.false_negative_count" in critical and "semantic_reasoning.missed_allergen_count" in critical
    assert any("FALSE NEGATIVE" in f and "milk" in f for f in bad["safety"].failures)
    assert gate.critical_failures(bad)


def test_gate_blocks_regression_against_baseline(results):
    baseline = {"metrics": {**gate.flatten(results), "normalization.exact_match_accuracy": 1.0}, "cases": {}}
    g = gate.check(results, baseline=baseline)
    assert [c.metric for c in g.failed] == ["normalization.exact_match_accuracy"]
    assert g.failed[0].level == "baseline"


def test_gate_blocks_shrinking_datasets(results):
    shrunk = dict(results)
    shrunk["safety"] = replace(results["safety"], cases=results["safety"].cases - 5)
    g = gate.check(shrunk)
    assert any(c.metric == "safety.cases" and not c.passed for c in g.checks)


def test_cli_report_and_exit_code(tmp_path):
    report, out = tmp_path / "r.md", tmp_path / "r.json"
    assert main(["--report", str(report), "--json", str(out)]) == 0
    text = report.read_text()
    assert "Release gate: PASSED" in text and "Safety (end-to-end allergen dataset)" in text
    data = json.loads(out.read_text())
    assert data["passed"] and data["metrics"]["safety.false_negative_rate"] == 0.0


def test_knowledge_change_blocked_by_safety_benchmark(client):
    """Retiring lysozyme's egg link isn't covered by the original regression list, but the benchmark catches it."""
    admin, reviewer = (login(client, e) for e in DEMO_ADMIN_EMAILS)
    with SessionLocal() as db:
        lys = db.scalar(select(IngredientConcept).where(IngredientConcept.slug == "lysozyme"))
        rel = db.scalar(select(ConceptRelation).where(ConceptRelation.subject_id == lys.id))
    p = client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "retire_relation", "payload": {"relation_id": rel.id}, "reason": "test"}).json()
    r = client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=reviewer, json={"approve": True})
    assert r.status_code == 409, r.text
    assert any("Safety benchmark" in f and "lysozyme" in f for f in r.json()["detail"]["failures"])
