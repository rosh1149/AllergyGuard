"""Stage 2: decision matrix, unverified knowledge, concept-level conflicts, provenance, cross-contact,
determinism and profile independence."""

from datetime import UTC, datetime

import pytest

from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.knowledge import KnowledgeBase
from app.engine.types import ABSENCE_CAPABLE, AUTHORITATIVE, SOURCE_POLICY, AnalysisContext, CrossContactDecl
from tests.helpers import add_item, new_customer, new_restaurant
from tests.kb_fixture import build_kb

P, X, U, NL = (Outcome.PRESENT, Outcome.POSSIBLE_CROSS_CONTACT, Outcome.UNKNOWN,
               Outcome.NOT_LISTED_IN_VERIFIED_RECORDS)
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def lab(text, id="l", **kw):
    return EvidenceSource(id=id, type=SourceType.label_manual, name=f"Label {id}", ingredients_text=text, **kw)


def db(text, id="d", **kw):
    return EvidenceSource(id=id, type=SourceType.product_database, name=f"DB {id}", ingredients_text=text, **kw)


def run(kb, sources, codes, **ctx):
    return assess(kb, sources, AllergyProfile(set(codes)), AnalysisContext(now=NOW, **ctx))


# ---- decision matrix ----


def test_engine_sets_derive_from_policy_table():
    assert AUTHORITATIVE == {t for t, p in SOURCE_POLICY.items() if p.shows_present}
    assert ABSENCE_CAPABLE == {t for t, p in SOURCE_POLICY.items() if p.supports_absence}
    assert SourceType.public_recipe not in AUTHORITATIVE and SourceType.restaurant_unverified not in ABSENCE_CAPABLE


def test_decision_matrix_endpoint(client):
    m = client.get("/api/knowledge/decision-matrix", headers=new_customer(client)).json()
    types = {s["type"]: s for s in m["sources"]}
    assert types["restaurant_verified"]["supports_absence"] and not types["customer_suggestion"]["shows_present"]
    assert "R5.not_listed_verified" in m["outcome_rules"] and len(m["absence_conditions"]) >= 9
    assert m["thresholds"]["min_identity_confidence"] == 0.7 and "safe" in m["never"]


# ---- unverified knowledge only raises possibilities ----


def test_unverified_relation_is_possible_only(kb):
    rels = [r for r in kb.relations] + [("cardamom", "contains_component", "milk", "possible")]
    k2 = KnowledgeBase(list(kb.allergens.values()), [], [], concepts=list(kb.concepts.values()),
                       aliases=list(kb.alias_to_concept.items()), relations=rels, modifiers=kb.modifiers)
    r = run(k2, [lab("rice, cardamom")], {"milk"})
    assert r.your_allergens[0].outcome == U  # possible link: never PRESENT, but blocks NOT LISTED


def test_unverified_relation_loaded_from_db_is_possible(client):
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.engine.service import load_knowledge_base
    from app.models import Certainty, ConceptRelation, IngredientConcept, KnowledgeStatus, RelationType
    with SessionLocal() as s:
        c = {x.slug: x for x in s.scalars(select(IngredientConcept).where(IngredientConcept.slug.in_(["clove", "egg"])))}
        s.add(ConceptRelation(subject_id=c["clove"].id, relation=RelationType.contains_component, object_concept_id=c["egg"].id,
                              certainty=Certainty.definite, status=KnowledgeStatus.unverified, source="test"))
        s.flush()
        kb_db = load_knowledge_base(s)
        assert ("clove", "contains_component", "egg", "possible") in kb_db.relations
        s.rollback()


# ---- conflicts compare what ingredients are ----


def test_same_ingredients_written_differently_do_not_conflict(kb):
    r = run(kb, [lab("wholegrain oat flakes, raw cane sugar, sea salt"), db("oat flakes, sugar, salt")], {"milk"},
            identity_confidence=0.9)
    assert r.conflicts == [] and r.your_allergens[0].outcome == NL


def test_minor_difference_is_recorded_without_blocking(kb):
    r = run(kb, [lab("rice, salt, sugar, paprika"), db("rice, salt, sugar")], {"milk"}, identity_confidence=0.9)
    c = next(c for c in r.conflicts if c.kind == "ingredient_difference")
    assert c.handling == "no_allergen_effect" and c.status == "resolved" and c.only_in == {"l": ["paprika"]}
    assert r.your_allergens[0].outcome == NL


def test_large_difference_is_unresolved_and_blocks_absence(kb):
    r = run(kb, [lab("rice, salt, sugar, vinegar"), db("potato, sunflower oil, paprika, onion")], {"milk"},
            identity_confidence=0.9)
    c = next(c for c in r.conflicts if c.kind == "ingredient_list_mismatch")
    assert c.status == "unresolved" and c.handling == "absence_blocked"
    assert set(c.only_in) == {"l", "d"} and r.your_allergens[0].outcome == U


def test_allergen_disagreement_keeps_cautious_finding(kb):
    r = run(kb, [lab("rice, salt, sugar"), db("rice, salt, sugar, whey powder")], {"milk"}, identity_confidence=0.9)
    kinds = {c.kind: c for c in r.conflicts}
    assert kinds["allergen_disagreement"].handling == "cautious_finding_kept" and r.your_allergens[0].outcome == P
    assert kinds["ingredient_difference"].handling == "cautious_finding_kept"


# ---- provenance ----


def test_finding_carries_full_provenance(kb):
    r = run(kb, [lab("rice, sodium caseinate", reference="label photo 1", verification="label", as_of=NOW)], {"milk"})
    e = r.your_allergens[0].evidence[0]
    assert e.path_nodes == ["sodium_caseinate", "casein", "milk"] and e.foodon_id == "FOODON:03317011"
    assert e.allergen_category == "Milk" and e.source_reference == "label photo 1" and e.collected_at == NOW
    assert r.your_allergens[0].rules == ["R1.present_ingredient"]


# ---- cross-contact kept apart from ingredients ----


def test_cross_contact_status_per_allergen(kb):
    src = EvidenceSource(id="r", type=SourceType.restaurant_verified, name="R", ingredients=["rice", "salt"],
                         cross_contact=[CrossContactDecl("peanuts", "possible", kind="shared_fryer", ingredient="rice")],
                         cross_contact_assessed=True)
    r = run(kb, [src], {"peanuts", "milk"})
    a = {x.code: x for x in r.your_allergens}
    assert a["peanuts"].outcome == X and a["peanuts"].cross_contact == "reported"
    assert "through the ingredient 'rice'" in a["peanuts"].reasons[0]
    assert a["milk"].outcome == NL and a["milk"].cross_contact == "none_reported"
    unassessed = run(kb, [lab("rice, salt")], {"milk"})
    assert unassessed.your_allergens[0].cross_contact == "not_assessed"


def test_ingredient_level_cross_contact_in_dashboard(client):
    h, rid, bid = new_restaurant(client)
    ok = client.post(f"/api/restaurants/{rid}/menu", headers=h, json={
        "branch_id": bid, "name": "Paneer Bowl", "category": "Main",
        "recipe": {"ingredients": [{"name": "paneer", "allergen_codes": ["milk"]}, {"name": "rice"}],
                   "cross_contacts": [{"allergen_code": "tree_nuts", "kind": "supplier", "ingredient": "paneer",
                                       "description": "Supplier also handles cashews"}],
                   "mark_verified": True}})
    assert ok.status_code == 201, ok.text
    cc = ok.json()["versions"][0]["cross_contacts"][0]
    assert cc["ingredient"] == "paneer" and cc["kind"] == "supplier"
    bad = client.post(f"/api/restaurants/{rid}/menu", headers=h, json={
        "branch_id": bid, "name": "Bad Bowl", "category": "Main",
        "recipe": {"ingredients": [{"name": "rice"}],
                   "cross_contacts": [{"allergen_code": "fish", "ingredient": "anchovy"}], "mark_verified": True}})
    assert bad.status_code == 422 and "not an ingredient" in bad.text
    cust = new_customer(client, codes=("tree_nuts",))
    body = client.post("/api/analyze/restaurant", headers=cust,
                       data={"restaurant_id": rid, "branch_id": bid, "menu_item_id": ok.json()["id"]}).json()
    a = body["result"]["your_allergens"][0]
    assert a["outcome"] == "POSSIBLE_CROSS_CONTACT" and "through the ingredient 'paneer'" in a["reasons"][0]


# ---- determinism and profile independence ----


def test_same_evidence_gives_same_result(kb):
    srcs = [lab("chocolate (sugar, cocoa mass, milk powder), hazelnuts, curd"), db("sugar, cocoa, milk, hazelnuts")]
    a = run(kb, srcs, {"milk", "tree_nuts", "soy"}, identity_confidence=0.9).model_dump(exclude={"trace", "duration_ms"})
    b = run(kb, srcs, {"milk", "tree_nuts", "soy"}, identity_confidence=0.9).model_dump(exclude={"trace", "duration_ms"})
    assert a == b


def test_profile_selects_findings_but_never_changes_them(kb):
    srcs = [lab("rice, butter, peanuts, curd", cross_contact_text="May contain sesame.")]
    one = {a.code: a for a in run(kb, srcs, {"milk"}).your_allergens}
    many = {a.code: a for a in run(kb, srcs, {"milk", "peanuts", "sesame", "soy"}).your_allergens}
    assert one["milk"].model_dump() == many["milk"].model_dump()
    # Allergens outside the profile are still assessed the same way and reported when found.
    others = {a.code: a for a in run(kb, srcs, {"milk"}).other_allergens}
    assert others["peanuts"].model_dump() == many["peanuts"].model_dump()
