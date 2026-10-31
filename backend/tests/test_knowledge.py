"""Step A: ontology-backed knowledge layer, relationship reasoning, hierarchy, regional lists,
knowledge maintenance and unknown-ingredient handling."""

import pytest

from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.seed.allergen_data import ALLERGENS
from app.seed.kb_regression import KB_REGRESSION_CASES
from app.seed.seed import DEMO_ADMIN_EMAILS, DEMO_CUSTOMER_EMAIL, DEMO_OWNER_EMAIL
from tests.helpers import login
from tests.kb_fixture import build_kb

P, X, U, N = (
    Outcome.PRESENT,
    Outcome.POSSIBLE_CROSS_CONTACT,
    Outcome.UNKNOWN,
    Outcome.NOT_LISTED_IN_VERIFIED_RECORDS,
)


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def label(text):
    return EvidenceSource(id="label", type=SourceType.label_ocr, name="Label", ingredients_text=text)


def run(kb, text, codes, custom=(), sources=None):
    r = assess(kb, sources or [label(text)], AllergyProfile(set(codes), list(custom)))
    return {a.code: a for a in r.your_allergens}, r


def definite(kb, text):
    res = kb.resolve_item(text)
    return {t.allergen_code for t in res.terms} | {p.allergen_code for _, p in res.paths if p.certainty == "definite"}


# ---- 1/3: concepts and relationship reasoning ----


@pytest.mark.parametrize("text, expected", KB_REGRESSION_CASES)
def test_regression_cases(kb, text, expected):
    assert definite(kb, text) == expected


@pytest.mark.parametrize(
    "text, allergen, chain",
    [
        ("casein", "milk", "casein, is a milk protein, derived from milk"),
        ("sodium caseinate", "milk", "sodium caseinate, derived from casein, is a milk protein, derived from milk"),
        ("almond flour", "tree_nuts", "almond flour, derived from almond, is a tree nut"),
        ("soy lecithin", "soy", "soy lecithin, derived from soybean"),
        ("marzipan", "tree_nuts", "marzipan, contains almond, is a tree nut"),
        ("ghee", "milk", "ghee, derived from butter, derived from milk"),
    ],
)
def test_relationship_paths_explain_findings(kb, text, allergen, chain):
    paths = {p.allergen_code: p for _, p in kb.resolve_item(text).paths}
    assert paths[allergen].certainty == "definite"
    assert paths[allergen].text == chain


def test_reason_shows_the_chain(kb):
    mine, _ = run(kb, "Ingredients: sugar, sodium caseinate, salt", {"milk"})
    assert mine["milk"].outcome == P
    assert "derived from casein, is a milk protein, derived from milk" in mine["milk"].reasons[0]
    ev = mine["milk"].evidence[0]
    assert ev.concept == "sodium_caseinate" and ev.certainty == "definite"


def test_cocoa_does_not_imply_milk(kb):
    mine, _ = run(kb, "Ingredients: wheat flour, sugar, cocoa powder, cocoa butter, salt", {"milk"})
    assert mine["milk"].outcome == N


def test_chocolate_cookie_milk_only_with_evidence(kb):
    no_milk, _ = run(kb, "Ingredients: wheat flour, sugar, chocolate (cocoa mass, sugar, cocoa butter)", {"milk"})
    assert no_milk["milk"].outcome == N
    with_milk, _ = run(kb, "Ingredients: wheat flour, sugar, chocolate (cocoa mass, sugar, milk powder)", {"milk"})
    assert with_milk["milk"].outcome == P


def test_possible_source_is_unknown_not_present(kb):
    mine, _ = run(kb, "Ingredients: sugar, palm oil, emulsifier (lecithin), salt", {"soy", "eggs"})
    assert mine["soy"].outcome == U and mine["eggs"].outcome == U
    assert "may contain Soybeans" in mine["soy"].reasons[0] and "The source is not stated" in mine["soy"].reasons[0]
    # A named source is definite.
    mine, _ = run(kb, "Ingredients: sugar, emulsifier (soy lecithin)", {"soy", "eggs"})
    assert mine["soy"].outcome == P and mine["eggs"].outcome == N


@pytest.mark.parametrize("term, code", [(t, a["code"]) for a in ALLERGENS for t, _ in a["exclusions"]])
def test_lookalike_exclusions_hold_through_concepts(kb, term, code):
    # e.g. "cream of tartar" is not milk although it contains the concept "cream".
    assert code not in definite(kb, term)


def test_concept_suppresses_lookalike_terms(kb):
    # "cocoa butter" is a recognised cocoa product with no milk relationship, so "butter" does not count.
    res = kb.resolve_item("cocoa butter")
    assert res.status == "no_known_allergen" and not res.terms


# ---- 11: allergen hierarchy and regional lists ----


def test_wheat_implies_gluten(kb):
    mine, _ = run(kb, "Ingredients: semolina, water", {"wheat", "gluten"})
    assert mine["wheat"].outcome == P and mine["gluten"].outcome == P


def test_barley_is_gluten_but_not_wheat(kb):
    mine, _ = run(kb, "Ingredients: barley malt, water", {"wheat", "gluten"})
    assert mine["gluten"].outcome == P
    assert mine["wheat"].outcome == N


def test_unspecified_gluten_leaves_wheat_possible(kb):
    src = EvidenceSource(id="off", type=SourceType.product_database, name="DB", ingredients_text="rice, sugar",
                         declared_allergens=["gluten"])
    mine, _ = run(kb, "", {"wheat", "gluten"}, sources=[src])
    assert mine["gluten"].outcome == P
    assert mine["wheat"].outcome == U
    assert "without naming the source" in mine["wheat"].reasons[0]


def test_regional_lists(kb):
    _, r = run(kb, "Ingredients: wheat flour, squid", {"wheat", "molluscs", "sulphites"})
    regs = {a.code: a.regulated_in for a in r.your_allergens}
    assert regs["wheat"] == ["EU", "IN", "US"]
    assert regs["molluscs"] == ["EU"]  # not a US or Indian major allergen
    assert regs["sulphites"] == ["EU", "IN"]


# ---- 17: unknown ingredients ----


def test_ingredient_statuses(kb):
    _, r = run(kb, "Ingredients: organic cane sugar, sodium caseinate, emulsifier (lecithin), spices, konjac", {"milk"})
    st = {i.text: i for i in r.ingredients}
    assert st["organic cane sugar"].status == "no_known_allergen" and st["organic cane sugar"].concept == "sugar"
    assert st["sodium caseinate"].status == "allergen_linked" and st["sodium caseinate"].allergens == ["milk"]
    assert st["sodium caseinate"].foodon_id == "FOODON:03317011"
    assert st["lecithin"].status == "possible_allergen" and set(st["lecithin"].possible_allergens) == {"soy", "eggs"}
    assert st["spices"].status == "ambiguous_source"
    assert st["konjac"].status == "unresolved"
    assert any("Not recognised by the ingredient knowledge base" in lim and "konjac" in lim for lim in r.limitations)
    assert r.knowledge_version == 1


def test_custom_allergy_through_concepts(kb):
    mine, _ = run(kb, "Ingredients: sugar, marzipan", set(), custom=["almond"])
    assert mine["custom:almond"].outcome == P
    assert "marzipan" in mine["custom:almond"].reasons[0]


def test_foodon_ids_only_where_verified(kb):
    assert kb.concepts["casein"].foodon_id == "FOODON:03420180"
    assert kb.concepts["kiwi"].foodon_id is None  # FoodOn's "kiwi" is the bird
    assert kb.concepts["whey"].foodon_id is None  # no exact label match


# ---- API: lookup, regulatory lists, maintenance workflow, unresolved queue ----


def test_allergen_list_by_jurisdiction(client):
    us = client.get("/api/allergens?jurisdiction=US").json()
    assert {a["code"] for a in us} == {"milk", "eggs", "fish", "crustaceans", "tree_nuts", "peanuts", "wheat", "soy", "sesame"}
    all_ = client.get("/api/allergens").json()
    assert len(all_) == 15 and next(a for a in all_ if a["code"] == "lupin")["regulated_in"] == ["EU"]


def test_resolve_and_regulatory_endpoints(client):
    h = login(client, DEMO_CUSTOMER_EMAIL)
    r = client.post("/api/knowledge/resolve", headers=h, json={"text": "sodium caseinate, cocoa butter, konjac"}).json()
    assert [x["status"] for x in r] == ["allergen_linked", "no_known_allergen", "unresolved"]
    regs = {x["jurisdiction"]: x for x in client.get("/api/knowledge/regulatory", headers=h).json()}
    assert regs["IN"]["status"] == "unverified" and regs["EU"]["status"] == "curated"


def test_admin_only(client):
    for email in (DEMO_CUSTOMER_EMAIL, DEMO_OWNER_EMAIL):
        assert client.get("/api/knowledge/summary", headers=login(client, email)).status_code == 403


def test_proposal_workflow_four_eyes_and_versioning(client):
    admin, reviewer = (login(client, e) for e in DEMO_ADMIN_EMAILS)
    v0 = client.get("/api/knowledge/summary", headers=admin).json()["version"]

    # Invalid proposals are refused immediately.
    assert client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "relation", "payload": {"subject": "nope", "relation": "is_a", "object": "tree_nut"}}).status_code == 422

    p = client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "concept", "reason": "Common in Indian sweets", "source": "Team review",
        "payload": {"slug": "chironji", "name": "chironji", "aliases": ["charoli"]}}).json()
    # The proposer cannot approve their own change.
    assert client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=admin, json={"approve": True}).status_code == 403
    r = client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=reviewer, json={"approve": True})
    assert r.status_code == 200 and r.json()["resulting_version"] == v0 + 1

    rel = client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "relation", "payload": {"subject": "chironji", "relation": "is_a", "object": "tree_nut"}}).json()
    client.post(f"/api/knowledge/proposals/{rel['id']}/decision", headers=reviewer, json={"approve": True})

    # The new knowledge is used immediately, with its version recorded.
    cust = login(client, DEMO_CUSTOMER_EMAIL)
    res = client.post("/api/knowledge/resolve", headers=cust, json={"text": "charoli"}).json()[0]
    assert res["allergens"] == ["tree_nuts"] and res["concept"] == "chironji"
    summary = client.get("/api/knowledge/summary", headers=admin).json()
    assert summary["version"] == v0 + 2
    changes = client.get("/api/knowledge/changes", headers=admin).json()
    assert changes[0]["action"] == "relation.approved" and changes[0]["version"] == v0 + 2
    detail = client.get("/api/knowledge/concepts/chironji", headers=admin).json()
    assert detail["allergen_paths"][0]["path"] == "chironji, is a tree nut" and detail["status"] == "verified"


def test_change_that_breaks_regression_cases_is_not_published(client):
    admin, reviewer = (login(client, e) for e in DEMO_ADMIN_EMAILS)
    v0 = client.get("/api/knowledge/summary", headers=admin).json()["version"]
    # Wrong: claims cocoa contains milk. "cocoa powder" must stay milk-free.
    p = client.post("/api/knowledge/proposals", headers=admin, json={
        "kind": "relation", "payload": {"subject": "cocoa", "relation": "contains_component", "object": "milk"}}).json()
    r = client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=reviewer, json={"approve": True})
    assert r.status_code == 409
    assert any("cocoa powder" in f for f in r.json()["detail"]["failures"])
    assert client.get("/api/knowledge/summary", headers=admin).json()["version"] == v0
    pending = client.get("/api/knowledge/proposals?status=pending", headers=admin).json()
    assert any(x["id"] == p["id"] and "regression cases failed" in x["decision_note"] for x in pending)
    # It can still be rejected.
    assert client.post(f"/api/knowledge/proposals/{p['id']}/decision", headers=reviewer,
                       json={"approve": False, "note": "Cocoa is not a milk product"}).json()["status"] == "rejected"


def test_unresolved_queue(client):
    cust = login(client, DEMO_CUSTOMER_EMAIL)
    for _ in range(2):
        client.post("/api/analyze/text", headers=cust, json={"ingredients_text": "rice, quinoa, gellan gum"})
    # (gellan gum is deliberately absent from the knowledge base)
    admin = login(client, DEMO_ADMIN_EMAILS[0])
    rows = {u["text"]: u for u in client.get("/api/knowledge/unresolved", headers=admin).json()}
    assert rows["gellan gum"]["occurrences"] >= 2 and rows["gellan gum"]["source_types"] == ["label_manual"]
    assert "rice" not in rows and "quinoa" not in rows
    r = client.post(f"/api/knowledge/unresolved/{rows['gellan gum']['id']}", headers=admin,
                    json={"status": "ignored", "note": "Thickener; to be added as a concept later"})
    assert r.json()["status"] == "ignored"
