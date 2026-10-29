import itertools

_n = itertools.count(1)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client, email, password="Demo1234!") -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return auth(r.json()["access_token"])


def new_customer(client, codes=("milk",), custom=()) -> dict:
    email = f"cust{next(_n)}@example.com"
    r = client.post(
        "/api/auth/register/customer",
        json={"email": email, "password": "Passw0rd!", "full_name": "Cust",
              "allergy_profile": {"allergen_codes": list(codes), "custom_allergies": list(custom)}},
    )
    assert r.status_code == 201, r.text
    return auth(r.json()["access_token"])


def new_restaurant(client, name=None) -> tuple[dict, int, int]:
    """Returns (owner headers, restaurant_id, first branch_id)."""
    n = next(_n)
    r = client.post(
        "/api/auth/register/restaurant",
        json={"email": f"rowner{n}@example.com", "password": "Passw0rd!", "full_name": f"Owner {n}",
              "restaurant_name": name or f"Test Kitchen {n}", "branch_name": "Central", "branch_city": "Springfield"},
    )
    assert r.status_code == 201, r.text
    h = auth(r.json()["access_token"])
    rid = r.json()["user"]["memberships"][0]["restaurant_id"]
    bid = client.get(f"/api/restaurants/{rid}", headers=h).json()["branches"][0]["id"]
    return h, rid, bid


def add_item(client, h, rid, bid, name="Paneer Tikka", ingredients=None, cross=None, verified=True) -> dict:
    body = {
        "branch_id": bid,
        "name": name,
        "category": "Main",
        "recipe": {
            "ingredients": ingredients if ingredients is not None else [
                {"name": "paneer", "allergen_codes": ["milk"]},
                {"name": "yogurt marinade", "allergen_codes": ["milk"]},
                {"name": "bell pepper"},
            ],
            "cross_contacts": cross or [],
            "mark_verified": verified,
        },
    }
    r = client.post(f"/api/restaurants/{rid}/menu", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()
