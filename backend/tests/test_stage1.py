"""Stage 1: ontology types and rules, regulatory separation, ambiguity, incomplete lists, unknown text."""

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.assess import resolution_out
from app.engine.normalize import normalize_ingredients, parse_ingredients
from app.knowledge.validation import validate_graph, validate_kb
from app.models import ConceptRelation, IngredientConcept, KnowledgeStatus, RelationType
from app.seed.concept_data import CONCEPT_TYPES, CONCEPTS
from app.seed.seed import DEMO_ADMIN_EMAILS
from tests.helpers import login, new_customer
from tests.kb_fixture import build_kb

U, NL, P = Outcome.UNKNOWN, Outcome.NOT_LISTED_IN_VERIFIED_RECORDS, Outcome.PRESENT


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def item(kb, text, ocr=False):
    return resolution_out(kb, "t", normalize_ingredients(kb, text, ocr=ocr)[0])


def label(text, **kw):
    return EvidenceSource(id="l", type=SourceType.label_manual, name="Label", ingredients_text=text, **kw)


# ---- ontology: types, relations, rules ----


def test_every_concept_has_one_type_and_seed_passes_rules(kb):
    typed = [s for slugs in CONCEPT_TYPES.values() for s in slugs]
    assert sorted(typed) == sorted(CONCEPTS) and len(typed) == len(set(typed))
    assert validate_kb(kb) == []


@pytest.mark.parametrize("relation, message", [
    (("milk", "derived_from", "butter", "definite"), "direction reversed"),
    (("butter", "is_regulated_as", "allergen:milk", "definite"), "only a food source"),
    (("spices", "contains_component", "milk", "definite"), "general term"),
    (("casein", "contains_protein", "milk", "definite"), "must be a protein"),
    (("butter", "is_a", "milk", "definite"), "same type"),
    (("lecithin", "derived_from", "egg", "definite"), "unstated-source"),
    (("milk", "derived_from", "milk", "definite"), "itself"),
])
def test_rules_catch_mistakes(kb, relation, message):
    concepts = {s: (c.concept_type, c.ambiguous_source) for s, c in kb.concepts.items()}
    errors = validate_graph(concepts, kb.relations + [relation], set(kb.allergens))
    assert any(message in e for e in errors), errors


def test_cycles_are_caught(kb):
    concepts = {s: (c.concept_type, c.ambiguous_source) for s, c in kb.concepts.items()}
    rels = kb.relations + [("milk", "derived_from", "milk_protein", "definite")]  # milk_protein is derived from milk
    assert any(e.startswith("cycle") for e in validate_graph(concepts, rels, set(kb.allergens)))


def test_protein_links(kb):
    casein = item(kb, "casein")
    assert casein.allergens == ["milk"] and casein.relationship_path == "casein, is a protein of milk"
    # Gluten comes from several cereals: the category is certain, the cereal is not.
    gluten = item(kb, "gluten")
    assert gluten.allergens == ["gluten"] and gluten.possible_allergens == ["wheat"]
    wg = item(kb, "vital wheat gluten")
    assert set(wg.allergens) == {"gluten", "wheat"}


def test_regulatory_category_is_separate_from_science(kb):
    # Oats are regulated as a cereal containing gluten (EU, India), not described as a kind of wheat.
    assert ("oats", "is_regulated_as", "allergen:gluten", "definite") in kb.relations
    assert "wheat" not in item(kb, "rolled oats").allergens
    assert kb.regulated_in("gluten") == ["EU", "IN"] and "US" in kb.regulated_in("wheat")
    assert not any(r[1] == "associated_with_allergen" for r in kb.relations)


# ---- ambiguity ----


def test_homonym_keeps_both_meanings(kb):
    curd = item(kb, "curd")
    assert curd.allergens == [] and set(curd.possible_allergens) == {"milk", "soy"}
    assert set(curd.interpretations) == {"yogurt", "tofu"}
    assert item(kb, "bean curd").allergens == ["soy"]
    mine = {a.code: a.outcome for a in assess(kb, [label("rice, curd")], AllergyProfile({"milk"})).your_allergens}
    assert mine["milk"] == U


def test_made_food_without_components_cannot_be_ruled_out(kb):
    r = assess(kb, [label("rice, dark chocolate")], AllergyProfile({"milk"}))
    assert r.your_allergens[0].outcome == U
    listed = assess(kb, [label("rice, chocolate (sugar, cocoa mass, cocoa butter)")], AllergyProfile({"milk"}))
    assert listed.your_allergens[0].outcome == NL
    roles = {i.text: i.role for i in listed.ingredients}
    assert roles["cocoa butter"] == "component"  # components, not sources


# ---- incomplete component lists ----


@pytest.mark.parametrize("text", ["rice, sugar, etc.", "rice, salt, ...", "rice, spices (cumin, and others)",
                                  "rice, water, colour (", "rice, colour (), salt"])
def test_incomplete_lists_block_absence(kb, text):
    r = assess(kb, [label(text)], AllergyProfile({"milk"}))
    assert r.your_allergens[0].outcome == U
    assert r.sources[0].completeness == "partial"
    assert "looks incomplete" in r.sources[0].not_used_for_absence_reason
    assert any("looks incomplete" in lim for lim in r.limitations)


def test_complete_list_still_supports_absence(kb):
    r = assess(kb, [label("rice, spices (cumin, coriander), salt")], AllergyProfile({"mustard"}))
    assert r.your_allergens[0].outcome == NL


def test_parse_marks_incomplete_items():
    items = parse_ingredients("chocolate (sugar, cocoa, etc), salt")
    assert items[0].incomplete and any(i.role == "list_note" for i in items)


# ---- unknown ingredients: candidates and reasons ----


def test_unresolved_reasons_and_candidates(kb):
    r = item(kb, "zzqx paste")
    assert r.status == "unresolved" and any("unknown word" in x for x in r.unresolved_reasons)
    guess = item(kb, "sunflwer oil")
    assert guess.candidates == ["sunflower oil"]
    assert any("would mean no allergen" in x for x in guess.unresolved_reasons)


def test_unresolved_queue_keeps_candidates(client):
    cust = new_customer(client)
    client.post("/api/analyze/text", headers=cust, json={"ingredients_text": "rice, sunflwer oil"})
    admin = login(client, DEMO_ADMIN_EMAILS[0])
    rows = {u["text"]: u for u in client.get("/api/knowledge/unresolved", headers=admin).json()}
    u = rows["sunflwer oil"]
    assert u["candidates"] == ["sunflower oil"] and u["reasons"]


# ---- database: types seeded, FoodOn mapping changes reviewed and logged ----


def test_concept_types_in_database_and_summary(client):
    admin = login(client, DEMO_ADMIN_EMAILS[0])
    s = client.get("/api/knowledge/summary", headers=admin).json()
    assert s["graph_rule_violations"] == [] and s["concept_types"]["food_source"] > 50
    assert s["ontology"]["release_commit"].startswith("cd73540")
    with SessionLocal() as db:
        assert db.scalar(select(IngredientConcept.concept_type).where(IngredientConcept.slug == "casein")) == "protein"
        regulated = db.scalars(select(ConceptRelation).where(ConceptRelation.relation == RelationType.is_regulated_as,
                                                              ConceptRelation.status != KnowledgeStatus.retired)).all()
        assert len(regulated) >= 18


def test_foodon_mapping_change_is_reviewed_and_logged(client):
    admin, reviewer = (login(client, e) for e in DEMO_ADMIN_EMAILS)
    assert client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "foodon_mapping", "payload": {"concept": "ghee", "foodon_id": "FOODON:123"}}).status_code == 422
    before = client.get("/api/knowledge/concepts/ghee", headers=admin).json()["foodon_id"]
    p = client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "foodon_mapping", "reason": "test",
        "payload": {"concept": "ghee", "foodon_id": "FOODON:03304040", "foodon_label": "ghee",
                    "evidence": "Label 'ghee' in FoodOn release cd73540 (test value)"}}).json()
    r = client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=reviewer, json={"approve": True})
    assert r.status_code == 200, r.text
    after = client.get("/api/knowledge/concepts/ghee", headers=admin).json()
    assert after["foodon_id"] == "FOODON:03304040" and "proposal" in after["foodon_note"]
    change = next(c for c in client.get("/api/knowledge/changes", headers=admin).json() if c["proposal_id"] == p["id"])
    assert change["details"]["payload"]["previous_foodon_id"] == before
