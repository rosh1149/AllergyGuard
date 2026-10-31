"""Step E: observability (rules, traces, metrics), caching and performance limits, security re-check."""

import json
import re
import threading

import httpx
import pytest

from app.api.routes.packaged import get_off_client
from app.core import background, limits, throttle
from app.core.cache import TTLCache
from app.core.config import get_settings
from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.normalize import normalization_cache_stats, normalize_ingredients
from app.main import app
from app.packaged.off import OpenFoodFactsClient
from app.seed.seed import DEMO_ADMIN_EMAILS, DEMO_CUSTOMER_EMAIL
from tests.helpers import add_item, login, new_customer, new_restaurant
from tests.kb_fixture import build_kb
from tests.test_packaged import OFF_PRODUCT, FakeOff, label_image, post


@pytest.fixture(scope="module")
def kb():
    return build_kb()


@pytest.fixture
def admin(client):
    return login(client, DEMO_ADMIN_EMAILS[0])


def ean13(body12: str) -> str:
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - s % 10) % 10)


def src(id, items, type=SourceType.restaurant_verified):
    return EvidenceSource(id=id, type=type, name=id, ingredients=items)


# ---- 18. decision rules and traces ----


def test_decision_rules(kb):
    def rules(sources, code):
        r = assess(kb, sources, AllergyProfile({code}))
        return r.your_allergens[0].outcome, r.your_allergens[0].rules

    assert rules([src("a", ["butter"])], "milk") == (Outcome.PRESENT, ["R1.present_ingredient"])
    assert rules([src("a", ["rice", "salt"])], "milk") == (Outcome.NOT_LISTED_IN_VERIFIED_RECORDS, ["R5.not_listed_verified"])
    assert rules([], "milk") == (Outcome.UNKNOWN, ["R6.absence_not_supported", "B.no_eligible_source"])
    label = EvidenceSource(id="l", type=SourceType.label_ocr, name="l", ingredients_text="rice, salt, sugar, vinegar")
    off = EvidenceSource(id="o", type=SourceType.product_database, name="o",
                         ingredients_text="potato, sunflower oil, paprika, onion powder")
    assert rules([label, off], "milk") == (Outcome.UNKNOWN, ["R6.absence_not_supported", "B.list_mismatch"])
    assert rules([src("a", ["rice"], SourceType.public_recipe), src("b", ["paneer"], SourceType.public_recipe)],
                 "milk")[1] == ["R3.unverified_candidate"]


def test_trace_is_recorded_without_personal_data(client):
    cust = new_customer(client, codes=("milk",))
    r = client.post("/api/analyze/text", headers=cust,
                    json={"ingredients_text": "rice, butter, hazelnutt", "product_name": "Secret Family Mix"})
    body = r.json()
    names = [s["name"] for s in body["trace"]]
    assert names == ["normalization", "concept_mapping", "fusion", "decision", "explanation"]
    assert body["duration_ms"] is not None and all(s["duration_ms"] is not None for s in body["trace"])
    assert body["trace"][3]["counts"]["rule_R1.present_ingredient"] == 1
    assert body["trace"][0]["counts"]["method_fuzzy"] == 1
    text = json.dumps(body["trace"])
    for private in ("butter", "hazelnut", "Secret", "milk", "@"):
        assert private not in text

    stored = client.get(f"/api/me/assessments/{body['assessment_id']}", headers=cust).json()
    assert stored["result"]["trace"] == body["trace"]


def test_packaged_trace_has_pipeline_stages(client):
    cust = new_customer(client)
    app.dependency_overrides[get_off_client] = FakeOff().client
    try:
        body = post(client, cust, images=[label_image()]).json()
    finally:
        app.dependency_overrides.pop(get_off_client, None)
    stages = {s["name"]: s for s in body["result"]["trace"]}
    assert {"barcode", "ocr", "product_lookup", "normalization", "decision"} <= set(stages)
    assert stages["ocr"]["duration_ms"] > 0
    assert all(s["duration_ms"] is not None for s in body["steps"] if s["name"] in ("barcode", "ocr"))


def test_request_id_and_security_headers(client):
    r = client.get("/api/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    bad = client.get("/api/health", headers={"X-Request-ID": "x" * 100 + "\n<script>"})
    assert re.fullmatch(r"[0-9a-f]{16}", bad.headers["X-Request-ID"])


def test_metrics_endpoint(client, admin):
    cust = new_customer(client)
    client.post("/api/analyze/text", headers=cust, json={"ingredients_text": "rice, zzqx paste"})
    m = client.get("/api/admin/metrics", headers=admin).json()
    a = m["assessments"]
    assert a["total"] >= 1 and a["by_kind"]["text"] >= 1
    assert a["latency_ms"]["text"]["p50"] is not None and "normalization" in a["stage_latency_ms"]
    assert a["with_unresolved_ingredients"] >= 1 and "R6.absence_not_supported" in a["decision_rules"]
    assert {"normalization", "open_food_facts"} <= set(m["caches"])
    assert m["background_jobs"]["mode"] == "inline" and "max_concurrent" in m["image_jobs"]
    assert "@" not in json.dumps(m)
    assert client.get("/api/admin/metrics", headers=cust).status_code == 403


# ---- 19. caching and performance ----


def test_normalization_cache(kb):
    text = "flour (wheat), sugar, sesami seeds, cocoa mas"
    first = normalize_ingredients(kb, text)
    before = normalization_cache_stats()["hits"]
    second = normalize_ingredients(kb, text)
    assert normalization_cache_stats()["hits"] == before + 1
    assert [(n.text, n.method, n.resolution.status) for n in first] == [(n.text, n.method, n.resolution.status) for n in second]
    second[0].text = "changed"  # callers get copies
    assert normalize_ingredients(kb, text)[0].text != "changed"


def test_off_cache():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/0000000000000.json"):
            return httpx.Response(200, json={"status": 0})
        if req.url.path == "/cgi/search.pl":
            return httpx.Response(200, json={"products": [OFF_PRODUCT]})
        if "fail" in req.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": 1, "product": OFF_PRODUCT})

    cache = TTLCache("test")
    c = OpenFoodFactsClient(http=httpx.Client(transport=httpx.MockTransport(handler)), cache=cache)
    assert c.get_product("5000159407236") and c.get_product("5000159407236")
    assert c.get_product("0000000000000") is None and c.get_product("0000000000000") is None
    c.search("oat biscuits"), c.search("Oat  Biscuits")
    assert len(calls) == 3
    for _ in range(2):
        with pytest.raises(Exception):
            c.get_product("fail")
    assert len(calls) == 5  # failures are not cached
    assert cache.stats()["hits"] == 3


def test_saved_copy_when_database_fails(client):
    code = ean13("501234567890")
    cust = new_customer(client, codes=("milk",))
    fake = FakeOff(products={code: {**OFF_PRODUCT, "code": code}})
    app.dependency_overrides[get_off_client] = fake.client
    try:
        post(client, cust, barcode=code)
        fake.fail = True
        body = post(client, cust, barcode=code).json()
    finally:
        app.dependency_overrides.pop(get_off_client, None)
    s = next(x for x in body["result"]["sources"] if x["type"] == "product_database")
    assert "saved copy" in s["reference"]
    assert any("saved on" in lim for lim in body["result"]["limitations"])
    assert {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]} == {"milk": "PRESENT"}


def test_background_job_failure_is_contained():
    before = background.stats()["failed"]

    def broken(db):
        raise RuntimeError("boom")

    background.submit("broken", broken)
    assert background.stats()["failed"] == before + 1


def test_image_jobs_are_limited(client, monkeypatch):
    cust = new_customer(client)
    sem = threading.BoundedSemaphore(1)
    sem.acquire()  # the only slot is busy
    monkeypatch.setattr(limits, "_slots", sem)
    monkeypatch.setattr(get_settings(), "image_job_wait_seconds", 0)
    r = post(client, cust, images=[label_image()])
    assert r.status_code == 503 and r.headers["Retry-After"] == "10"
    sem.release()
    assert post(client, cust, product_name="anything").status_code == 200  # no photos: no slot needed


# ---- 20. security ----


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_tenant_isolation_sweep(client):
    """Every restaurant dashboard route refuses other restaurants, customers and anonymous users."""
    h, rid, bid = new_restaurant(client)
    add_item(client, h, rid, bid, name="Secret Dish")
    outsider, _, _ = new_restaurant(client)
    cust = new_customer(client)
    paths = {p: ops for p, ops in app.openapi()["paths"].items() if p.startswith("/api/restaurants/{restaurant_id}")}
    assert len(paths) >= 15
    checked = 0
    for route_path, ops in paths.items():
        path = _fill(route_path.replace("{restaurant_id}", str(rid)))
        for method in (m.upper() for m in ops):
            for who, headers, ok in (("outsider", outsider, (403, 404)), ("customer", cust, (403, 404)),
                                     ("anonymous", {}, (401,))):
                r = client.request(method, path, headers=headers, json={})
                assert r.status_code in ok, f"{who} {method} {route_path} -> {r.status_code}"
                checked += 1
    assert checked >= 60


def test_role_boundaries(client):
    owner, rid, _ = new_restaurant(client)
    cust = new_customer(client)
    for path in ("/api/me/assessments", "/api/me/export", "/api/me/feedback", "/api/me/allergy-profile"):
        assert client.get(path, headers=owner).status_code == 403
    for path in ("/api/admin/metrics", "/api/admin/audit", "/api/review-queue", "/api/sources",
                 "/api/knowledge/unresolved"):
        assert client.get(path, headers=owner).status_code == 403
        assert client.get(path, headers=cust).status_code == 403
    # Public menus never include recipe data.
    menu = client.get(f"/api/public/restaurants/{rid}/branches/1/menu", headers=cust)
    assert menu.status_code in (200, 404)


def test_login_throttling(client):
    throttle.reset_all()
    email = DEMO_CUSTOMER_EMAIL
    try:
        for _ in range(get_settings().login_max_failures):
            assert client.post("/api/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
        r = client.post("/api/auth/login", json={"email": email, "password": "Demo1234!"})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        assert client.post("/api/auth/login", json={"email": DEMO_ADMIN_EMAILS[0], "password": "Demo1234!"}).status_code == 200
    finally:
        throttle.reset_all()
    assert client.post("/api/auth/login", json={"email": email, "password": "Demo1234!"}).status_code == 200


def test_export_and_delete_account(client):
    owner, rid, bid = new_restaurant(client)
    item = add_item(client, owner, rid, bid, name="Dal")
    email_resp = client.post("/api/auth/register/customer", json={
        "email": "leaving@example.com", "password": "Passw0rd!", "full_name": "Leaving",
        "allergy_profile": {"allergen_codes": ["milk"], "custom_allergies": ["saffron"]}})
    cust = {"Authorization": f"Bearer {email_resp.json()['access_token']}"}
    client.post("/api/analyze/text", headers=cust, json={"ingredients_text": "rice, butter"})
    client.post("/api/feedback", headers=cust, json={"menu_item_id": item["id"], "suggestion_type": "missing_ingredient",
                                                      "ingredient_name": "ghee"})
    data = client.get("/api/me/export", headers=cust).json()
    assert data["account"]["email"] == "leaving@example.com"
    assert [a["code"] for a in data["allergy_profile"]["allergens"]] == ["milk"]
    assert data["allergy_profile"]["custom_allergies"] == ["saffron"] and len(data["assessments"]) == 1
    assert len(data["feedback"]) == 1

    assert client.request("DELETE", "/api/me", headers=cust, json={"password": "nope"}).status_code == 403
    assert client.request("DELETE", "/api/me", headers=owner, json={"password": "Passw0rd!"}).status_code == 403
    assert client.request("DELETE", "/api/me", headers=cust, json={"password": "Passw0rd!"}).status_code == 204
    assert client.get("/api/me/assessments", headers=cust).status_code == 401
    assert client.post("/api/auth/login", json={"email": "leaving@example.com", "password": "Passw0rd!"}).status_code == 401
    # The restaurant keeps the suggestion, without the customer's identity.
    fb = client.get(f"/api/restaurants/{rid}/feedback", headers=owner).json()
    assert len(fb) == 1 and fb[0]["ingredient_name"] == "ghee"


def test_admin_actions_are_audited(client, admin):
    owner, rid, bid = new_restaurant(client)
    add_item(client, owner, rid, bid, name="Fish Tacos")
    cust = new_customer(client)
    client.post("/api/analyze/restaurant", headers=cust, data={"restaurant_id": rid, "branch_id": bid, "dish_name": "Unicorn Stew"})
    item = next(i for i in client.get("/api/review-queue", headers=admin, params={"restaurant_id": rid}).json()
                if i["key"] == "unicorn stew")
    assert client.post(f"/api/review-queue/{item['id']}/decision", headers=admin,
                       json={"status": "dismissed", "note": "test"}).status_code == 200
    log = client.get("/api/admin/audit", headers=admin).json()
    assert any(e["action"] == "review.dismissed" and e["entity_id"] == item["id"] for e in log)
