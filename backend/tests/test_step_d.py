"""Step D: source adapters, the extended feedback workflow and the review queue."""

import httpx
import pytest
from PIL import Image

from app.api.routes.packaged import get_off_client
from app.main import app
from app.packaged.off import OpenFoodFactsClient
from app.seed.seed import DEMO_ADMIN_EMAILS
from app.sources.base import AdapterStatus, SourceAdapter, adapter_health
from app.sources.open_food_facts import OpenFoodFactsAdapter
from tests.helpers import add_item, login, new_customer, new_restaurant
from tests.test_packaged import BARCODE, OFF_PRODUCT, FakeOff, png, post


def off_with(handler) -> OpenFoodFactsClient:
    return OpenFoodFactsClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def admin(client):
    return login(client, DEMO_ADMIN_EMAILS[0])


def queue(client, admin, **params):
    r = client.get("/api/review-queue", headers=admin, params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json()


# ---- 13. source adapters ----


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(429, headers={"Retry-After": "30"}), AdapterStatus.rate_limited),
    (httpx.Response(200, text="<html>not json</html>"), AdapterStatus.malformed),
    (httpx.Response(200, json=["unexpected"]), AdapterStatus.malformed),
    (httpx.Response(200, json={"status": 1, "product": "oops"}), AdapterStatus.malformed),
    (httpx.Response(503), AdapterStatus.unavailable),
    (httpx.Response(200, json={"status": 0}), AdapterStatus.not_found),
    (httpx.Response(200, json={"status": 1, "product": OFF_PRODUCT}), AdapterStatus.ok),
])
def test_off_adapter_statuses(response, expected):
    r = OpenFoodFactsAdapter(off_with(lambda req: response)).lookup(BARCODE)
    assert r.status == expected
    assert r.retrieved_at is not None and r.latency_ms >= 0
    if expected == AdapterStatus.ok:
        src = r.sources[0]
        assert src.reference == f"barcode {BARCODE}" and src.verification == "community"
        assert {c.kind for c in src.cross_contact} == {"precautionary_label"}
    else:
        assert r.sources == []


def test_off_adapter_network_error_and_disabled():
    def boom(req):
        raise httpx.ConnectTimeout("timeout", request=req)
    assert OpenFoodFactsAdapter(off_with(boom)).lookup(BARCODE).status == AdapterStatus.unavailable
    assert OpenFoodFactsAdapter(None).lookup(BARCODE).status == AdapterStatus.skipped


def test_adapter_bug_becomes_failed_source():
    class Broken(SourceAdapter):
        name = "broken_test_adapter"

        def fetch(self):
            return self.run(lambda: 1 / 0)

    r = Broken().fetch()
    assert r.status == AdapterStatus.unavailable and "ZeroDivisionError" in r.detail
    assert adapter_health()["broken_test_adapter"]["failures"] >= 1


def test_rate_limit_is_reported_in_analysis(client):
    cust = new_customer(client, codes=("milk",))
    app.dependency_overrides[get_off_client] = lambda: off_with(lambda req: httpx.Response(429))
    try:
        body = post(client, cust, barcode=BARCODE).json()
    finally:
        app.dependency_overrides.pop(get_off_client, None)
    assert any("too many requests" in lim for lim in body["result"]["limitations"])
    step = next(s for s in body["steps"] if s["name"] == "product_lookup")
    assert step["status"] == "failed" and "rate limited" in step["detail"]
    assert body["result"]["overall_outcome"] == "UNKNOWN"


def test_sources_endpoint_is_admin_only(client, admin):
    rows = client.get("/api/sources", headers=admin).json()
    names = {r["name"] for r in rows}
    assert {"open_food_facts", "label_photo", "label_text", "restaurant_records", "customer_reports",
            "public_recipes"} <= names
    assert all("health" in r for r in rows)
    assert client.get("/api/sources", headers=new_customer(client)).status_code == 403


# ---- 16. ambiguous input: ask, don't trust, queue for review ----


def test_ambiguous_product_search(client, admin):
    a = {**OFF_PRODUCT, "code": "4000000000001", "product_name": "Oat Crunch Biscuits Original", "brands": "Acme"}
    b = {**OFF_PRODUCT, "code": "4000000000002", "product_name": "Oat Crunch Biscuits Chocolate", "brands": "Acme"}
    fake = FakeOff(products={}, search=[a, b])
    app.dependency_overrides[get_off_client] = fake.client
    cust = new_customer(client, codes=("sesame",))
    try:
        body = post(client, cust, product_name="Acme Oat Crunch Biscuits").json()
        post(client, cust, product_name="Acme Oat Crunch Biscuits")
    finally:
        app.dependency_overrides.pop(get_off_client, None)
    assert body["needs_confirmation"] is True
    assert {c["barcode"] for c in body["product_candidates"]} == {"4000000000001", "4000000000002"}
    assert body["product"]["confident"] is False
    # The database entry can't rule sesame out while we don't know which product it is.
    assert {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]} == {"sesame": "UNKNOWN"}
    assert any("Several products match" in lim for lim in body["result"]["limitations"])
    item = next(i for i in queue(client, admin, kind="multiple_product_matches") if i["key"] == "acme oat crunch biscuits")
    assert item["occurrences"] == 2 and len(item["details"]["candidates"]) == 2
    assert "customer" not in str(item["details"]).lower()


def test_unreadable_label_is_queued(client, admin):
    cust = new_customer(client)
    blank = png(Image.new("RGB", (800, 600), "white"))
    app.dependency_overrides[get_off_client] = FakeOff().client
    try:
        post(client, cust, images=[blank], product_name="Mystery Snack Bar")
    finally:
        app.dependency_overrides.pop(get_off_client, None)
    assert any(i["key"] == "mystery snack bar" for i in queue(client, admin, kind="unreadable_label"))


def test_ambiguous_dish_and_unmatched_dish(client):
    h, rid, bid = new_restaurant(client)
    add_item(client, h, rid, bid, name="Chicken Curry Mild", ingredients=[{"name": "chicken"}, {"name": "cream", "allergen_codes": ["milk"]}])
    add_item(client, h, rid, bid, name="Chicken Curry Hot", ingredients=[{"name": "chicken"}, {"name": "chilli"}])
    cust = new_customer(client, codes=("milk",))

    body = client.post("/api/analyze/restaurant", headers=cust,
                       data={"restaurant_id": rid, "branch_id": bid, "dish_name": "Chicken Curry"}).json()
    assert body["needs_confirmation"] is True and not body["dish"]["confident"]
    assert {c["name"] for c in body["candidates"]} >= {"Chicken Curry Mild", "Chicken Curry Hot"}
    # Whichever curry it is, the records can't rule milk out until the customer confirms the dish.
    assert body["result"]["your_allergens"][0]["outcome"] != "NOT_LISTED_IN_VERIFIED_RECORDS"

    client.post("/api/analyze/restaurant", headers=cust,
                data={"restaurant_id": rid, "branch_id": bid, "dish_name": "Zebra Pie"})
    items = client.get(f"/api/restaurants/{rid}/review-queue", headers=h).json()
    kinds = {i["kind"]: i for i in items}
    assert set(kinds) == {"multiple_dish_matches", "unmatched_dish"}
    assert kinds["unmatched_dish"]["key"] == "zebra pie"

    # Other restaurants and customers can't see or resolve these.
    h2, rid2, _ = new_restaurant(client)
    assert client.get(f"/api/restaurants/{rid2}/review-queue", headers=h2).json() == []
    iid = kinds["unmatched_dish"]["id"]
    assert client.post(f"/api/restaurants/{rid2}/review-queue/{iid}/decision", headers=h2,
                       json={"status": "dismissed"}).status_code == 404
    assert client.get(f"/api/restaurants/{rid}/review-queue", headers=cust).status_code in (403, 404)

    r = client.post(f"/api/restaurants/{rid}/review-queue/{iid}/decision", headers=h,
                    json={"status": "resolved", "note": "Added to the menu as a special"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert client.post(f"/api/restaurants/{rid}/review-queue/{iid}/decision", headers=h,
                       json={"status": "dismissed"}).status_code == 409
    audit = client.get(f"/api/restaurants/{rid}/audit", headers=h).json()
    assert any(a["action"] == "review.resolved" for a in audit)


def test_review_summary(client, admin):
    s = client.get("/api/review-queue/summary", headers=admin).json()
    assert s["open_total"] == sum(s["open_by_kind"].values()) and "open_unresolved_ingredients" in s
    assert client.get("/api/review-queue/summary", headers=new_customer(client)).status_code == 403


# ---- 15. feedback: clarification, new categories, impact ----


def _analyze(client, cust, rid, bid, item_id):
    r = client.post("/api/analyze/restaurant", headers=cust,
                    data={"restaurant_id": rid, "branch_id": bid, "menu_item_id": item_id})
    assert r.status_code == 200, r.text
    return r.json()


def _decide(client, h, rid, sid, decision, note=""):
    return client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                       json={"decision": decision, "note": note})


def test_clarification_round_trip(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Pesto Pasta", ingredients=[{"name": "pasta"}, {"name": "basil"}])
    cust = new_customer(client, codes=("tree_nuts",))
    sid = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "missing_ingredient", "ingredient_name": "pine nuts",
        "message": "Tasted nutty"}).json()["id"]

    assert _decide(client, h, rid, sid, "clarification_requested").status_code == 422  # question required
    r = _decide(client, h, rid, sid, "clarification_requested", "Was it the green or red pesto?")
    assert r.status_code == 200 and r.json()["status"] == "clarification_requested"
    assert _decide(client, h, rid, sid, "clarification_requested", "again?").status_code == 409

    # Still pending, so still shown as an unreviewed report.
    body = _analyze(client, cust, rid, bid, item["id"])
    assert any(s["type"] == "customer_suggestion" for s in body["result"]["sources"])

    mine = next(f for f in client.get("/api/me/feedback", headers=cust).json() if f["id"] == sid)
    assert mine["clarification_request"] == "Was it the green or red pesto?"
    other = new_customer(client)
    assert client.post(f"/api/me/feedback/{sid}/clarification", headers=other, json={"message": "x"}).status_code == 404
    r = client.post(f"/api/me/feedback/{sid}/clarification", headers=cust, json={"message": "The green one"})
    assert r.status_code == 200 and r.json()["status"] == "unverified"
    assert r.json()["message"] == "Tasted nutty"  # original kept
    assert client.post(f"/api/me/feedback/{sid}/clarification", headers=cust, json={"message": "again"}).status_code == 409

    r = _decide(client, h, rid, sid, "confirmed", "Green pesto has pine nuts")
    out = r.json()
    assert out["status"] == "confirmed" and out["clarification_response"] == "The green one"
    assert [e["action"] for e in out["history"]] == ["submitted", "clarification_requested", "clarified", "confirmed"]
    assert out["impact"]["resulting_version_number"] == 2 and out["impact"]["changes"]["ingredients_added"] == ["pine nuts"]
    assert out["impact"]["assessments_since"] == 0

    body = _analyze(client, cust, rid, bid, item["id"])
    assert body["result"]["your_allergens"][0]["outcome"] == "PRESENT"
    out = next(f for f in client.get(f"/api/restaurants/{rid}/feedback", headers=h).json() if f["id"] == sid)
    assert out["impact"]["assessments_since"] == 1


def test_outdated_recipe_unverifies_records(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="House Salad", ingredients=[{"name": "lettuce"}, {"name": "tomato"}])
    cust = new_customer(client, codes=("mustard",))
    assert _analyze(client, cust, rid, bid, item["id"])["result"]["your_allergens"][0]["outcome"] == \
        "NOT_LISTED_IN_VERIFIED_RECORDS"
    assert client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "outdated_recipe"}).status_code == 422  # message needed
    sid = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "outdated_recipe",
        "message": "It now comes with a honey-mustard dressing"}).json()["id"]
    out = _decide(client, h, rid, sid, "confirmed").json()
    assert out["resulting_version_number"] == 2
    body = _analyze(client, cust, rid, bid, item["id"])
    assert body["dish"]["verification_status"] == "unverified"
    assert body["result"]["your_allergens"][0]["outcome"] == "UNKNOWN"


def test_incorrect_allergen_is_never_applied_automatically(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Garlic Bread",
                    ingredients=[{"name": "bread", "allergen_codes": ["gluten"]}, {"name": "garlic"}])
    cust = new_customer(client, codes=("gluten",))
    sid = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "incorrect_allergen", "allergen_code": "gluten",
        "message": "They use gluten-free bread now"}).json()["id"]
    out = _decide(client, h, rid, sid, "confirmed", "Checked").json()
    assert out["status"] == "confirmed" and out["resulting_version_number"] is None and out["impact"] is None
    body = _analyze(client, cust, rid, bid, item["id"])
    assert body["dish"]["recipe_version"] == 1
    assert body["result"]["your_allergens"][0]["outcome"] == "PRESENT"
