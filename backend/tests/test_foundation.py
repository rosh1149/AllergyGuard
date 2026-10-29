from app.seed.seed import DEMO_CUSTOMER_EMAIL, DEMO_OWNER_EMAIL, DEMO_PASSWORD


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register_customer(client, email, codes=("milk",), custom=()):
    return client.post(
        "/api/auth/register/customer",
        json={
            "email": email,
            "password": "Passw0rd!",
            "full_name": "Test User",
            "allergy_profile": {"allergen_codes": list(codes), "custom_allergies": list(custom)},
        },
    )


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "database": "ok"}


def test_allergen_knowledge_base_seeded(client):
    codes = {a["code"] for a in client.get("/api/allergens").json()}
    assert len(codes) == 14
    assert {"milk", "peanuts", "tree_nuts", "gluten", "sesame", "sulphites"} <= codes


def test_customer_signup_stores_allergies(client):
    r = register_customer(client, "a@example.com", codes=("peanuts", "milk"), custom=(" Kiwi ",))
    assert r.status_code == 201, r.text
    profile = r.json()["user"]["allergy_profile"]
    assert {a["code"] for a in profile["allergens"]} == {"milk", "peanuts"}
    assert profile["custom_allergies"] == ["kiwi"]

    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers=auth(token)).json()
    assert {a["code"] for a in me["allergy_profile"]["allergens"]} == {"milk", "peanuts"}


def test_signup_requires_at_least_one_allergy(client):
    assert register_customer(client, "b@example.com", codes=()).status_code == 422


def test_signup_rejects_unknown_allergen(client):
    assert register_customer(client, "c@example.com", codes=("dragonfruit",)).status_code == 422


def test_duplicate_email_and_bad_login(client):
    assert register_customer(client, "d@example.com").status_code == 201
    assert register_customer(client, "D@example.com").status_code == 409
    r = client.post("/api/auth/login", json={"email": "d@example.com", "password": "wrong-pass"})
    assert r.status_code == 401


def test_update_allergy_profile(client):
    token = register_customer(client, "e@example.com").json()["access_token"]
    r = client.put(
        "/api/me/allergy-profile",
        headers=auth(token),
        json={"allergen_codes": ["sesame", "eggs"], "custom_allergies": []},
    )
    assert r.status_code == 200
    assert {a["code"] for a in r.json()["allergens"]} == {"sesame", "eggs"}


def test_unauthenticated_and_bad_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=auth("garbage")).status_code == 401


def test_demo_accounts_login(client):
    for email in (DEMO_OWNER_EMAIL, DEMO_CUSTOMER_EMAIL):
        r = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert r.status_code == 200, email


def test_restaurant_signup_and_tenant_isolation(client):
    r = client.post(
        "/api/auth/register/restaurant",
        json={
            "email": "owner2@example.com",
            "password": "Passw0rd!",
            "full_name": "Owner Two",
            "restaurant_name": "Green Bowl",
            "branch_name": "Riverside",
        },
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    own_id = r.json()["user"]["memberships"][0]["restaurant_id"]

    mine = client.get(f"/api/restaurants/{own_id}", headers=auth(token))
    assert mine.status_code == 200
    assert mine.json()["your_role"] == "owner"
    assert mine.json()["branches"][0]["name"] == "Riverside"

    # The demo restaurant belongs to another tenant: must look like it doesn't exist.
    demo_owner = client.post(
        "/api/auth/login", json={"email": DEMO_OWNER_EMAIL, "password": DEMO_PASSWORD}
    ).json()
    demo_id = demo_owner["user"]["memberships"][0]["restaurant_id"]
    assert client.get(f"/api/restaurants/{demo_id}", headers=auth(token)).status_code == 404

    # Customers have no dashboard access.
    cust = client.post(
        "/api/auth/login", json={"email": DEMO_CUSTOMER_EMAIL, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    assert client.get(f"/api/restaurants/{demo_id}", headers=auth(cust)).status_code == 404


def test_restaurant_user_cannot_use_customer_profile(client):
    token = client.post(
        "/api/auth/login", json={"email": DEMO_OWNER_EMAIL, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    assert client.get("/api/me/allergy-profile", headers=auth(token)).status_code == 403
