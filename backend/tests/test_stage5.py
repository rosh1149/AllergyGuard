"""Stage 5: source failure handling (retries, circuit breaker), resource monitoring, and the
administrators' assessment trace lookup."""

import os

import httpx
import pytest

from app.core.config import get_settings
from app.packaged.off import OpenFoodFactsClient
from app.sources.base import AdapterStatus, adapter_health, breaker_status, reset_health
from app.sources.open_food_facts import OpenFoodFactsAdapter
from tests.helpers import auth, login, new_customer

BARCODE = "3017620422003"
PRODUCT = {"status": 1, "product": {"code": BARCODE, "product_name": "Spread", "brands": "Acme",
                                    "ingredients_text": "sugar, hazelnuts, skimmed milk powder"}}


class Flaky:
    """An Open Food Facts stand-in that fails the first `fail` calls with `status`."""

    def __init__(self, fail: int, status: int = 503):
        self.fail, self.status, self.calls = fail, status, 0

    def handler(self, request):
        self.calls += 1
        if self.calls <= self.fail:
            return httpx.Response(self.status)
        return httpx.Response(200, json=PRODUCT)

    def adapter(self):
        return OpenFoodFactsAdapter(OpenFoodFactsClient(http=httpx.Client(transport=httpx.MockTransport(self.handler))))


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "source_max_attempts", 3)
    monkeypatch.setattr(s, "source_backoff_seconds", 0.0)
    monkeypatch.setattr(s, "source_breaker_threshold", 3)
    monkeypatch.setattr(s, "source_breaker_cooldown_seconds", 60)
    reset_health()
    return s


# ---------- retries ----------


def test_temporary_outage_is_retried(settings):
    f = Flaky(fail=2)
    r = f.adapter().lookup(BARCODE)
    assert r.status == AdapterStatus.ok and r.attempts == 3 and f.calls == 3
    assert "after 3 attempts" in r.detail
    assert adapter_health()["open_food_facts"]["retries"] == 2


def test_retries_are_bounded(settings):
    f = Flaky(fail=10)
    r = f.adapter().lookup(BARCODE)
    assert r.status == AdapterStatus.unavailable and f.calls == 3


@pytest.mark.parametrize("status,expected", [(429, AdapterStatus.rate_limited), (404, AdapterStatus.not_found)])
def test_rate_limits_and_not_found_are_not_retried(settings, status, expected):
    f = Flaky(fail=10, status=status)
    r = f.adapter().lookup(BARCODE)
    assert r.status == expected and f.calls == 1


def test_backoff_grows(settings, monkeypatch):
    monkeypatch.setattr(settings, "source_backoff_seconds", 0.1)
    waits = []
    monkeypatch.setattr("app.sources.base.time.sleep", waits.append)
    Flaky(fail=10).adapter().lookup(BARCODE)
    assert len(waits) == 2 and 0.1 <= waits[0] <= 0.15 and 0.2 <= waits[1] <= 0.3


# ---------- circuit breaker ----------


def test_circuit_opens_after_repeated_failures_and_skips_calls(settings):
    down = Flaky(fail=10**6)
    adapter = down.adapter()
    for _ in range(3):  # three failed calls (3 attempts each)
        assert adapter.lookup(BARCODE).status == AdapterStatus.unavailable
    assert down.calls == 9 and breaker_status()["open_food_facts"]["state"] == "open"
    r = adapter.lookup(BARCODE)  # answered at once, the source is not contacted
    assert r.status == AdapterStatus.unavailable and r.short_circuited and r.attempts == 0
    assert "tried again in about" in r.detail and down.calls == 9
    health = adapter_health()["open_food_facts"]
    assert health["circuit"]["state"] == "open" and health["circuit"]["short_circuited"] == 1


def test_half_open_trial_closes_or_reopens(settings, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.sources.base.time.monotonic", lambda: clock[0])
    f = Flaky(fail=9)  # down for exactly three calls of three attempts
    adapter = f.adapter()
    for _ in range(3):
        adapter.lookup(BARCODE)
    assert breaker_status()["open_food_facts"]["state"] == "open"
    clock[0] += 61  # cool-down over: one trial call goes through
    assert breaker_status()["open_food_facts"]["state"] == "half_open"
    assert adapter.lookup(BARCODE).status == AdapterStatus.ok
    assert breaker_status()["open_food_facts"]["state"] == "closed"

    g = Flaky(fail=10**6)
    bad = g.adapter()
    for _ in range(3):
        bad.lookup(BARCODE)
    clock[0] += 61
    assert bad.lookup(BARCODE).status == AdapterStatus.unavailable  # trial fails ...
    st = breaker_status()["open_food_facts"]
    assert st["state"] == "open" and st["times_opened"] == 3  # ... and the circuit opens again at once


def test_not_found_does_not_count_as_a_failure(settings):
    nf = Flaky(fail=10**6, status=404)
    for _ in range(5):
        assert nf.adapter().lookup(BARCODE).status == AdapterStatus.not_found
    assert breaker_status()["open_food_facts"]["state"] == "closed"


def test_local_sources_have_no_breaker(settings):
    from app.sources.label import LabelTextAdapter
    assert not LabelTextAdapter.remote and OpenFoodFactsAdapter.remote


def test_open_circuit_makes_the_product_check_unknown_not_safe(client, settings, monkeypatch):
    """With the product database circuit open, a barcode-only check reports the source as failed
    and never says the allergen is not listed."""
    from app.api.routes.packaged import get_off_client
    from app.main import app

    down = Flaky(fail=10**6)
    app.dependency_overrides[get_off_client] = lambda: OpenFoodFactsClient(
        http=httpx.Client(transport=httpx.MockTransport(down.handler)))
    try:
        cust = new_customer(client, codes=("milk",))
        for _ in range(3):
            client.post("/api/analyze/packaged", headers=cust, data={"barcode": BARCODE})
        calls = down.calls
        r = client.post("/api/analyze/packaged", headers=cust, data={"barcode": BARCODE})
        assert r.status_code == 200 and down.calls == calls  # short-circuited
        body = r.json()
        mine = body["result"]["your_allergens"]
        assert mine and all(a["outcome"] != "NOT_LISTED_IN_VERIFIED_RECORDS" for a in mine)
        assert any(st["name"] == "product_lookup" and st["status"] == "failed" for st in body["steps"])
        assert body["result"]["limitations"]
    finally:
        app.dependency_overrides.pop(get_off_client, None)


# ---------- monitoring ----------


def test_tesseract_runs_single_threaded():
    import app.packaged.ocr  # noqa: F401
    assert os.environ.get("OMP_THREAD_LIMIT") == "1"


def test_image_job_limit_defaults_to_cores(monkeypatch):
    from app.core.limits import max_concurrent
    monkeypatch.setattr(get_settings(), "max_concurrent_image_jobs", 0)
    assert max_concurrent() == (os.cpu_count() or 2)
    monkeypatch.setattr(get_settings(), "max_concurrent_image_jobs", 3)
    assert max_concurrent() == 3


def test_image_job_timings_are_recorded():
    from app.core.limits import image_job, image_job_stats
    before = image_job_stats()["jobs"]
    with image_job():
        pass
    st = image_job_stats()
    assert st["jobs"] == before + 1 and st["duration_ms"]["p50"] is not None and st["wait_ms"]["p95"] is not None


def test_metrics_report_process_resources(client):
    admin = login(client, "admin@demo.allergyguard.app")
    m = client.get("/api/admin/metrics", headers=admin).json()
    p = m["process"]
    assert p["memory_rss_mb"] > 0 and p["memory_peak_rss_mb"] >= p["memory_rss_mb"] * 0.5
    assert p["cpu_seconds"] >= 0 and p["cpu_count"] >= 1 and p["threads"] >= 1
    assert "duration_ms" in m["image_jobs"] and "wait_ms" in m["image_jobs"]


# ---------- assessment trace lookup ----------


def test_admin_trace_lookup_shows_no_personal_data(client):
    r = client.post("/api/auth/register/customer", json={
        "email": "trace.probe@example.com", "password": "Passw0rd!", "full_name": "Trace Probe",
        "allergy_profile": {"allergen_codes": ["sesame", "peanuts"], "custom_allergies": ["zzqtraceallergy"]}})
    cust = auth(r.json()["access_token"])
    res = client.post("/api/analyze/text", headers=cust, json={"ingredients_text": "rice, zzqtraceingredient, sesame oil"})
    aid = res.json()["assessment_id"]
    admin = login(client, "admin@demo.allergyguard.app")

    listing = client.get("/api/admin/assessments", headers=admin, params={"kind": "text"})
    assert listing.status_code == 200 and any(x["id"] == aid for x in listing.json())
    slow = client.get("/api/admin/assessments", headers=admin, params={"sort": "slowest", "limit": 5}).json()
    assert [x["duration_ms"] or 0 for x in slow] == sorted([x["duration_ms"] or 0 for x in slow], reverse=True)

    t = client.get(f"/api/admin/assessments/{aid}", headers=admin)
    assert t.status_code == 200
    body = t.json()
    assert {s["name"] for s in body["trace"]} >= {"normalization", "concept_mapping", "fusion", "decision"}
    assert sum(body["outcome_counts"].values()) >= 2 and body["source_types"][0]["type"] == "label_manual"
    text = t.text.lower()
    for private in ("trace.probe", "trace probe", "zzqtraceallergy", "zzqtraceingredient", "sesame", "peanut",
                    "customer_id", "\"label\":", "\"outcomes\":"):
        assert private not in text, private
    audit = client.get("/api/admin/audit", headers=admin).json()
    assert any(a["action"] == "assessment.trace_viewed" and a["entity_id"] == aid for a in audit)


def test_trace_lookup_is_admin_only(client):
    cust = new_customer(client)
    assert client.get("/api/admin/assessments", headers=cust).status_code == 403
    assert client.get("/api/admin/assessments/1", headers=cust).status_code == 403
    admin = login(client, "admin@demo.allergyguard.app")
    assert client.get("/api/admin/assessments/99999999", headers=admin).status_code == 404
