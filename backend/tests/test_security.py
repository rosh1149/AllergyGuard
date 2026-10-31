"""Security validation: role permissions on every dashboard route, token handling, upload abuse,
what unauthenticated and public endpoints expose, and personal data in logs."""

import io
import logging
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from PIL import Image

from app.api.deps import RestaurantAccess
from app.core.config import get_settings
from app.main import app
from app.models import MembershipRole
from tests.helpers import add_item, auth, login, new_customer, new_restaurant


class _R:
    """A route with its full path (routers are included lazily, so app.routes holds router stubs)."""

    def __init__(self, rc):
        self.path, self.methods, self.dependant = rc.path, set(rc.methods or ()), rc.route.dependant


def _routes() -> list[_R]:
    return [_R(rc) for rc in iter_route_contexts(app.routes) if isinstance(rc.route, APIRoute)]


def _dashboard_routes() -> list[_R]:
    return [r for r in _routes() if r.path.startswith("/api/restaurants/{restaurant_id}")]


def _fill(path: str, rid: int) -> str:
    return re.sub(r"\{[^}]+\}", "1", path.replace("{restaurant_id}", str(rid)))


def _access(route: _R) -> RestaurantAccess | None:
    """The RestaurantAccess dependency guarding a route (searched through nested dependencies)."""
    stack = [route.dependant]
    while stack:
        d = stack.pop()
        if isinstance(d.call, RestaurantAccess):
            return d.call
        stack.extend(d.dependencies)
    return None


def _member(client, owner, rid, role: str) -> dict:
    n = abs(hash((rid, role))) % 10**8
    email = f"{role}{n}@example.com"
    r = client.post(f"/api/restaurants/{rid}/team", headers=owner,
                    json={"email": email, "role": role, "initial_password": "Passw0rd!", "full_name": role})
    assert r.status_code == 201, r.text
    return login(client, email, "Passw0rd!")


# ---------- roles ----------


def test_every_dashboard_route_declares_its_roles():
    routes = _dashboard_routes()
    assert len(routes) >= 15
    missing = [f"{sorted(r.methods)} {r.path}" for r in routes if _access(r) is None]
    assert missing == [], f"dashboard routes without a restaurant role check: {missing}"


def test_role_sweep_staff_and_managers(client):
    """Staff may only use routes open to every member; managers everything except owner-only routes.
    Read from the route definitions, so a new route is covered automatically."""
    owner, rid, bid = new_restaurant(client)
    add_item(client, owner, rid, bid, name="Sweep Dish")
    staff = _member(client, owner, rid, "staff")
    manager = _member(client, owner, rid, "manager")
    checked = refused = 0
    for route in _dashboard_routes():
        allowed = _access(route).allowed
        path = _fill(route.path, rid)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            for who, headers, role in (("staff", staff, MembershipRole.staff),
                                       ("manager", manager, MembershipRole.manager)):
                if method != "GET" and role in allowed:
                    continue  # don't actually change data as an allowed role; the reads cover access
                r = client.request(method, path, headers=headers, json={})
                if role in allowed:
                    assert r.status_code != 403, f"{who} refused on {method} {route.path}"
                else:
                    assert r.status_code == 403, f"{who} {method} {route.path} -> {r.status_code}"
                    refused += 1
                checked += 1
    assert checked >= 30 and refused >= 20
    # The restaurant still has its one owner and the dish is untouched.
    team = client.get(f"/api/restaurants/{rid}/team", headers=owner).json()
    assert sorted(m["role"] for m in team) == ["manager", "owner", "staff"]


def test_staff_cannot_change_roles_or_promote_themselves(client):
    owner, rid, _ = new_restaurant(client)
    staff = _member(client, owner, rid, "staff")
    me = client.get("/api/auth/me", headers=staff).json()
    r = client.patch(f"/api/restaurants/{rid}/team/{me['id']}", headers=staff, json={"role": "owner"})
    assert r.status_code == 403


def test_customer_and_admin_routes_refuse_other_account_types(client):
    owner, rid, _ = new_restaurant(client)
    cust = new_customer(client)
    admin = login(client, "admin@demo.allergyguard.app")
    for route in _routes():
        if "GET" not in route.methods or "{" in route.path:
            continue
        if route.path.startswith("/api/admin") or route.path.startswith("/api/knowledge/proposals"):
            for h in (owner, cust):
                assert client.get(route.path, headers=h).status_code == 403, route.path
        if route.path.startswith("/api/me"):
            for h in (owner, admin):
                assert client.get(route.path, headers=h).status_code == 403, route.path


# ---------- tokens ----------


def _token(**claims) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": "1", "role": "customer", "iat": now, "exp": now + timedelta(minutes=5)} | claims
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, claims.pop("_key", None) or s.jwt_secret, algorithm=s.jwt_algorithm)


def test_tokens_expired_forged_or_malformed_are_refused(client):
    cust = new_customer(client)
    uid = client.get("/api/auth/me", headers=cust).json()["id"]
    s = get_settings()
    now = datetime.now(timezone.utc)
    good = jwt.encode({"sub": str(uid), "exp": now + timedelta(minutes=5)}, s.jwt_secret, algorithm=s.jwt_algorithm)
    assert client.get("/api/auth/me", headers=auth(good)).status_code == 200
    bad = {
        "expired": jwt.encode({"sub": str(uid), "exp": now - timedelta(seconds=1)}, s.jwt_secret, algorithm=s.jwt_algorithm),
        "no expiry": jwt.encode({"sub": str(uid)}, s.jwt_secret, algorithm=s.jwt_algorithm),
        "wrong key": jwt.encode({"sub": str(uid), "exp": now + timedelta(minutes=5)}, "x" * 48, algorithm="HS256"),
        "alg none": jwt.encode({"sub": str(uid), "exp": now + timedelta(minutes=5)}, None, algorithm="none"),
        "no subject": jwt.encode({"exp": now + timedelta(minutes=5)}, s.jwt_secret, algorithm=s.jwt_algorithm),
        "unknown user": jwt.encode({"sub": "99999999", "exp": now + timedelta(minutes=5)}, s.jwt_secret, algorithm=s.jwt_algorithm),
        "garbage": "not.a.token",
    }
    for name, tok in bad.items():
        assert client.get("/api/auth/me", headers=auth(tok)).status_code == 401, name


def test_token_lifetime_is_bounded(client):
    r = client.post("/api/auth/login", json={"email": "customer@demo.allergyguard.app", "password": "Demo1234!"})
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert 0 < claims["exp"] - claims["iat"] <= get_settings().jwt_expire_minutes * 60


# ---------- uploads ----------


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (w, h), 255).save(buf, "PNG", optimize=True)
    return buf.getvalue()


@pytest.mark.parametrize("files,expect", [
    ([("images", ("x.png", b"%PDF-1.4 not an image", "image/png"))], "not a readable image"),
    ([("images", ("x.gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif"))], "not a readable image|Only JPEG"),
    ([("images", ("x.png", b"", "image/png"))], "empty|at least one"),
    ([("images", (f"{i}.png", b"x", "image/png")) for i in range(4)], "at most 3"),
])
def test_upload_abuse_is_refused(client, files, expect):
    cust = new_customer(client)
    for url in ("/api/analyze/packaged", "/api/analyze/restaurant"):
        r = client.post(url, headers=cust, files=files, data={"restaurant_id": "1", "branch_id": "1"})
        assert r.status_code == 422, (url, r.status_code, r.text)
        assert re.search(expect, r.text, re.I), (url, r.text)


def test_oversized_upload_is_refused(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    cust = new_customer(client)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024 + 10)
    r = client.post("/api/analyze/packaged", headers=cust, files=[("images", ("big.png", big, "image/png"))])
    assert r.status_code == 422 and "larger than" in r.text


def test_decompression_bomb_is_refused_before_decoding(client):
    data = _png(9000, 9000)  # 81 megapixels, but only a few hundred kilobytes compressed
    assert len(data) < 2 * 1024 * 1024
    cust = new_customer(client)
    r = client.post("/api/analyze/packaged", headers=cust, files=[("images", ("bomb.png", data, "image/png"))])
    assert r.status_code == 422 and "too large" in r.text


def test_uploads_need_a_customer_account(client):
    owner, _, _ = new_restaurant(client)
    files = [("images", ("x.png", _png(400, 400), "image/png"))]
    assert client.post("/api/analyze/packaged", files=files).status_code == 401
    assert client.post("/api/analyze/packaged", headers=owner, files=files).status_code == 403


# ---------- what is public ----------

PRIVATE_KEYS = {"email", "password", "password_hash", "allergy_profile", "allergen_codes", "custom_allergies",
                "recipe", "ingredients", "cross_contacts", "customer_id", "full_name", "audit", "members"}


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_only_auth_and_health_work_without_signing_in(client):
    open_paths = []
    for route in _routes():
        if route.path.startswith("/api") and "GET" in route.methods:
            if client.get(re.sub(r"\{[^}]+\}", "1", route.path)).status_code not in (401, 403):
                open_paths.append(route.path)
    assert set(open_paths) <= {"/api/health", "/api/allergens", "/api/knowledge/decision-matrix"}, open_paths


def test_public_directory_and_menus_expose_no_private_data(client):
    owner, rid, bid = new_restaurant(client)
    add_item(client, owner, rid, bid, name="Visible Dish")
    cust = new_customer(client)
    directory = client.get("/api/public/restaurants", headers=cust)
    menu = client.get(f"/api/public/restaurants/{rid}/branches/{bid}/menu", headers=cust)
    assert directory.status_code == 200 and menu.status_code == 200
    assert any(i["name"] == "Visible Dish" for i in menu.json())
    for body in (directory.json(), menu.json()):
        assert not (_keys(body) & PRIVATE_KEYS), _keys(body) & PRIVATE_KEYS
    assert "paneer" not in menu.text.lower()  # the recipe's ingredients are never on the public menu


def test_staff_view_of_feedback_hides_the_customer(client):
    owner, rid, bid = new_restaurant(client)
    item = add_item(client, owner, rid, bid, name="Feedback Dish")
    cust = new_customer(client)
    client.post("/api/feedback", headers=cust, json={"menu_item_id": item["id"], "suggestion_type": "missing_ingredient",
                                                     "ingredient_name": "ghee"})
    body = client.get(f"/api/restaurants/{rid}/feedback", headers=owner).json()
    assert body and not (_keys(body) & {"email", "customer_id", "full_name", "allergy_profile"})


# ---------- logs ----------


def test_logs_contain_no_personal_data(client, caplog):
    caplog.set_level(logging.DEBUG)
    email, name, custom, secret_ing = "pii.probe@example.com", "Probe Personname", "zzqprobeallergy", "zzqprobeingredient"
    r = client.post("/api/auth/register/customer", json={
        "email": email, "password": "Passw0rd!", "full_name": name,
        "allergy_profile": {"allergen_codes": ["sesame"], "custom_allergies": [custom]}})
    h = auth(r.json()["access_token"])
    client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    client.post("/api/analyze/text", headers=h, json={"ingredients_text": f"rice, {secret_ing}, sesame oil"})
    client.get("/api/me/export", headers=h)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records, "expected some log output"
    for needle in (email, name, custom, secret_ing, "Passw0rd!", "wrong-password", "sesame oil"):
        assert needle.lower() not in text.lower(), f"{needle!r} appeared in the logs"
