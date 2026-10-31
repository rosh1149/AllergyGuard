"""Step C: provenance, freshness, conflicts, cross-contact detail, snapshots and stored assessments."""

from datetime import UTC, datetime, timedelta

import pytest

from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.types import AnalysisContext, CrossContactDecl
from tests.helpers import add_item, new_customer, new_restaurant
from tests.kb_fixture import build_kb
from tests.test_packaged import BARCODE, OFF_PRODUCT, FakeOff, label_image, off, post  # noqa: F401
from app.api.routes.packaged import get_off_client
from app.main import app

P, X, U, N = (Outcome.PRESENT, Outcome.POSSIBLE_CROSS_CONTACT, Outcome.UNKNOWN,
              Outcome.NOT_LISTED_IN_VERIFIED_RECORDS)
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def run(kb, sources, codes, **ctx):
    r = assess(kb, sources, AllergyProfile(set(codes)), AnalysisContext(now=NOW, **ctx))
    return {a.code: a for a in r.your_allergens}, r


def recipe(ingredients, **kw):
    return EvidenceSource(id=kw.pop("id", "r"), type=kw.pop("type", SourceType.restaurant_verified), name="Recipe",
                          ingredients=ingredients, **kw)


# ---- freshness ----


def test_stale_record_cannot_rule_out(kb):
    old = recipe(["rice", "salt"], as_of=NOW - timedelta(days=400), max_age_days=365, verification="verified")
    mine, r = run(kb, [old], {"milk"})
    assert mine["milk"].outcome == U
    s = r.sources[0]
    assert (s.fresh, s.age_days, s.used_for_absence) == (False, 400, False)
    assert "400 days old" in s.not_used_for_absence_reason


def test_stale_record_still_reports_presence(kb):
    old = recipe(["paneer"], as_of=NOW - timedelta(days=900), max_age_days=365)
    mine, _ = run(kb, [old], {"milk"})
    assert mine["milk"].outcome == P


def test_fresh_record_rules_out(kb):
    new = recipe(["rice", "salt"], as_of=NOW - timedelta(days=10), max_age_days=365)
    mine, r = run(kb, [new], {"milk"})
    assert mine["milk"].outcome == N and r.sources[0].fresh and r.sources[0].age_days == 10
    assert "not_found_in_verified_records" in mine["milk"].basis


# ---- unresolved items block absence ----


def test_unresolved_item_blocks_not_listed(kb):
    mine, r = run(kb, [recipe(["rice", "zzqx paste"])], {"milk"})
    assert mine["milk"].outcome == U
    assert r.sources[0].unresolved_items == ["zzqx paste"]
    assert r.sources[0].completeness == "partial"


# ---- provenance on each finding ----


def test_evidence_carries_provenance(kb):
    src = recipe(["butter"], reference="Spice / Central / Dal / recipe v3", verification="verified",
                 as_of=NOW - timedelta(days=5))
    mine, _ = run(kb, [src], {"milk"})
    e = mine["milk"].evidence[0]
    assert e.source_reference.endswith("recipe v3") and e.source_verification == "verified"
    assert e.source_as_of == NOW - timedelta(days=5) and e.collected_at == NOW
    assert mine["milk"].basis and mine["milk"].basis[0] in {"direct_ingredient", "derived_ingredient"}


# ---- conflicts get ids and resolutions ----


def test_conflict_ids_and_resolution(kb):
    a = recipe(["paneer", "rice"], id="a", as_of=NOW - timedelta(days=3))
    b = recipe(["rice"], id="b", type=SourceType.manufacturer, as_of=NOW - timedelta(days=30))
    mine, r = run(kb, [a, b], {"milk"})
    assert mine["milk"].outcome == P
    c = next(c for c in r.conflicts if c.kind == "allergen_disagreement")
    assert c.id and c.resolution
    assert any(c.id in e.conflict_ids for e in mine["milk"].evidence)


def test_cross_contact_disagreement(kb):
    a = recipe(["rice"], id="a", cross_contact=[CrossContactDecl("peanuts", "possible", kind="shared_fryer")],
               cross_contact_assessed=True)
    b = recipe(["rice"], id="b", type=SourceType.manufacturer, cross_contact_assessed=True)
    mine, r = run(kb, [a, b], {"peanuts"})
    assert mine["peanuts"].outcome == X
    assert any(c.kind == "cross_contact_disagreement" for c in r.conflicts)
    assert "shared fryer" in " ".join(e.detail for e in mine["peanuts"].evidence).lower()


def test_unassessed_cross_contact_is_a_limitation(kb):
    _, r = run(kb, [recipe(["rice"], cross_contact_assessed=False)], {"milk"})
    assert any("cross-contact" in lim.lower() for lim in r.limitations)


# ---- restaurant pipeline: structured cross-contact, review flag, version diffs, suggestions ----


def _analyze(client, cust, rid, bid, item_id):
    r = client.post("/api/analyze/restaurant", headers=cust,
                    data={"restaurant_id": rid, "branch_id": bid, "menu_item_id": item_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_restaurant_cross_contact_kind_and_versions(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Fries", ingredients=[{"name": "potato"}, {"name": "salt"}],
                    cross=[{"allergen_code": "peanuts", "level": "possible", "kind": "shared_fryer",
                            "description": "Fryer also used for satay"}])
    v1 = item["versions"][0]
    assert v1["cross_contacts"][0]["kind"] == "shared_fryer" and v1["cross_contacts"][0]["status"] == "verified"
    assert v1["changes"] is None

    cust = new_customer(client, codes=("peanuts", "milk"))
    body = _analyze(client, cust, rid, bid, item["id"])
    pea = next(a for a in body["result"]["your_allergens"] if a["code"] == "peanuts")
    assert pea["outcome"] == "POSSIBLE_CROSS_CONTACT"
    src = body["result"]["sources"][0]
    assert src["reference"].endswith("Fries / recipe v1") and src["verification"] == "verified" and src["fresh"]
    assert body["result"]["assessment_id"]

    # New version: butter added, fryer issue removed, cross-contact reviewed.
    r = client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions", headers=h, json={
        "ingredients": [{"name": "potato"}, {"name": "salt"}, {"name": "butter", "allergen_codes": ["milk"]}],
        "cross_contacts": [], "cross_contact_reviewed": True, "mark_verified": True})
    assert r.status_code == 201, r.text
    v2 = max(r.json()["versions"], key=lambda v: v["version_number"])
    ch = v2["changes"]
    assert ch["previous_version"] == 1 and ch["ingredients_added"] == ["butter"] and ch["allergens_added"] == ["milk"]
    assert ch["cross_contact_removed"] == ["peanuts (shared_fryer)"] and v2["cross_contact_reviewed"] is True

    body2 = _analyze(client, cust, rid, bid, item["id"])
    outs = {a["code"]: a["outcome"] for a in body2["result"]["your_allergens"]}
    assert outs == {"milk": "PRESENT", "peanuts": "NOT_LISTED_IN_VERIFIED_RECORDS"}

    # The first assessment is now outdated (recipe changed); the second is current.
    hist = {a["id"]: a for a in client.get("/api/me/assessments", headers=cust).json()}
    first, second = hist[body["result"]["assessment_id"]], hist[body2["result"]["assessment_id"]]
    assert first["outdated"] and any("changed this recipe" in x for x in first["outdated_reasons"])
    assert not second["outdated"] and second["kind"] == "restaurant"


def test_old_version_suggestions_not_used(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Soup", ingredients=[{"name": "tomato"}, {"name": "salt"}])
    cust = new_customer(client, codes=("celery",))
    r = client.post("/api/feedback", headers=cust, json={"menu_item_id": item["id"],
                                                        "suggestion_type": "missing_allergen", "allergen_code": "celery"})
    assert r.status_code == 201, r.text
    body = _analyze(client, cust, rid, bid, item["id"])
    assert any(s["type"] == "customer_suggestion" for s in body["result"]["sources"])

    client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions", headers=h, json={
        "ingredients": [{"name": "tomato"}, {"name": "salt"}, {"name": "basil"}], "mark_verified": True})
    body = _analyze(client, cust, rid, bid, item["id"])
    assert not any(s["type"] == "customer_suggestion" for s in body["result"]["sources"])
    assert any("earlier version of this recipe" in lim for lim in body["result"]["limitations"])


# ---- packaged: provenance, snapshots and formulation changes ----


def test_product_snapshot_and_change_detection(client):
    cust = new_customer(client, codes=("milk",))
    fake = FakeOff(products={BARCODE: {**OFF_PRODUCT, "last_modified_t": int((datetime.now(UTC) - timedelta(days=20)).timestamp())}})
    app.dependency_overrides[get_off_client] = fake.client
    try:
        r1 = post(client, cust, barcode=BARCODE).json()
        src = next(s for s in r1["result"]["sources"] if s["type"] == "product_database")
        assert src["reference"] == f"barcode {BARCODE}" and src["verification"] == "community"
        assert src["age_days"] in (19, 20, 21) and src["fresh"]
        assert not any("record has changed" in lim for lim in r1["result"]["limitations"])

        fake.products[BARCODE] = {**fake.products[BARCODE],
                                  "ingredients_text": OFF_PRODUCT["ingredients_text"] + ", hazelnuts",
                                  "allergens_tags": ["en:gluten", "en:milk", "en:nuts"]}
        r2 = post(client, cust, barcode=BARCODE).json()
        lim = " ".join(r2["result"]["limitations"])
        assert "record has changed" in lim and "hazelnuts" in lim and "tree_nuts" in lim

        hist = {a["id"]: a for a in client.get("/api/me/assessments", headers=cust).json()}
        assert hist[r1["result"]["assessment_id"]]["outdated"]
        assert not hist[r2["result"]["assessment_id"]]["outdated"]
    finally:
        app.dependency_overrides.pop(get_off_client, None)


def test_old_product_record_cannot_rule_out(client):
    cust = new_customer(client, codes=("sesame",))
    old = int((datetime.now(UTC) - timedelta(days=2000)).timestamp())
    fake = FakeOff(products={BARCODE: {**OFF_PRODUCT, "last_modified_t": old}})
    app.dependency_overrides[get_off_client] = fake.client
    try:
        body = post(client, cust, barcode=BARCODE).json()
        assert {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]} == {"sesame": "UNKNOWN"}
        src = next(s for s in body["result"]["sources"] if s["type"] == "product_database")
        assert not src["fresh"]
    finally:
        app.dependency_overrides.pop(get_off_client, None)


def test_label_photo_provenance(client, off):
    cust = new_customer(client, codes=("milk",))
    body = post(client, cust, images=[label_image()]).json()
    src = next(s for s in body["result"]["sources"] if s["type"] == "label_ocr")
    assert src["verification"] == "label" and src["reference"] == "label photo 1"
    milk = next(a for a in body["result"]["your_allergens"] if a["code"] == "milk")
    assert milk["evidence"][0]["source_reference"] == "label photo 1"


# ---- stored assessments ----


def test_assessment_history_is_private(client):
    a = new_customer(client, codes=("milk",))
    b = new_customer(client, codes=("milk",))
    r = client.post("/api/analyze/text", headers=a, json={"ingredients_text": "rice, butter", "product_name": "Rice mix"})
    aid = r.json()["assessment_id"]
    assert aid
    rows = client.get("/api/me/assessments", headers=a).json()
    assert rows[0]["id"] == aid and rows[0]["kind"] == "text" and rows[0]["outcomes"]["milk"] == "PRESENT"
    detail = client.get(f"/api/me/assessments/{aid}", headers=a).json()
    assert detail["result"]["assessment_id"] == aid and detail["sources"][0]["verification"] == "label"
    assert client.get(f"/api/me/assessments/{aid}", headers=b).status_code == 404
    assert client.get("/api/me/assessments", headers=b).json() == []
    assert client.delete(f"/api/me/assessments/{aid}", headers=b).status_code == 404
    assert client.delete(f"/api/me/assessments/{aid}", headers=a).status_code == 204
    assert client.get(f"/api/me/assessments/{aid}", headers=a).status_code == 404
