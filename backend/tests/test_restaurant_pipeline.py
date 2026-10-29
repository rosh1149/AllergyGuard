import io

import pytest
from PIL import Image, ImageDraw

from app.restaurant import pipeline as rp
from app.restaurant.fallback import RecipeCandidates, RecipeSource
from app.restaurant.vision import DishGuess
from app.seed.seed import DEMO_CUSTOMER_EMAIL, DEMO_OWNER_EMAIL
from tests.helpers import add_item, login, new_customer, new_restaurant


@pytest.fixture
def demo(client):
    owner = login(client, DEMO_OWNER_EMAIL)
    rid = client.get("/api/auth/me", headers=owner).json()["memberships"][0]["restaurant_id"]
    dash = client.get(f"/api/restaurants/{rid}", headers=owner).json()
    menu = {i["name"]: i["id"] for i in client.get(f"/api/restaurants/{rid}/menu", headers=owner).json()}
    return {"owner": owner, "rid": rid, "bid": dash["branches"][0]["id"], "menu": menu,
            "cust": login(client, DEMO_CUSTOMER_EMAIL)}


def dish_photo() -> bytes:
    img = Image.new("RGB", (800, 600), (200, 120, 60))
    d = ImageDraw.Draw(img)
    for i in range(0, 800, 40):
        d.ellipse((i, 200 + (i % 120), i + 60, 260 + (i % 120)), fill=(240, 220, 180))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def analyze(client, headers, images=(), **form):
    files = [("images", (f"d{i}.jpg", data, "image/jpeg")) for i, data in enumerate(images)]
    return client.post("/api/analyze/restaurant", headers=headers, data=form, files=files or None)


def mine(body):
    return {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]}


def test_public_directory(client, demo):
    found = client.get("/api/public/restaurants?q=spice", headers=demo["cust"]).json()
    assert [r["name"] for r in found] == ["Spice Route Kitchen"]
    menu = client.get(f"/api/public/restaurants/{demo['rid']}/branches/{demo['bid']}/menu", headers=demo["cust"]).json()
    assert {m["name"] for m in menu} >= {"Butter Chicken", "Chana Masala", "Vegetable Samosa"}
    assert set(menu[0]) == {"id", "name", "category", "description"}  # no recipe data
    assert client.get(f"/api/public/restaurants/{demo['rid']}/branches/999999/menu", headers=demo["cust"]).status_code == 404


def test_selected_menu_item_uses_verified_records(client, demo):
    r = analyze(client, demo["cust"], restaurant_id=demo["rid"], menu_item_id=demo["menu"]["Butter Chicken"])
    assert r.status_code == 200, r.text
    body = r.json()
    # Demo customer: milk + peanuts. Butter Chicken: butter/cream (milk), possible peanut cross-contact.
    assert mine(body) == {"milk": "PRESENT", "peanuts": "POSSIBLE_CROSS_CONTACT"}
    assert body["dish"]["match_method"] == "selected_from_menu" and body["dish"]["confident"]
    assert body["dish"]["verification_status"] == "verified"
    assert body["result"]["sources"][0]["type"] == "restaurant_verified"
    assert {a["code"] for a in body["result"]["other_allergens"]} == {"tree_nuts"}
    assert body["can_submit_feedback"] is True
    assert any("without it" in q for q in body["questions_for_staff"])
    assert any("cross-contact with peanuts" in q for q in body["questions_for_staff"])


def test_dish_name_fuzzy_match(client, demo):
    body = analyze(client, demo["cust"], restaurant_id=demo["rid"], branch_id=demo["bid"], dish_name="chana masla").json()
    assert body["dish"]["name"] == "Chana Masala" and body["dish"]["match_method"] == "dish_name"
    assert body["dish"]["confident"]
    assert mine(body) == {"milk": "NOT_LISTED_IN_VERIFIED_RECORDS", "peanuts": "NOT_LISTED_IN_VERIFIED_RECORDS"}
    assert any("not proof" in a["reasons"][0] for a in body["result"]["your_allergens"])


def test_weak_name_match_is_not_trusted(client):
    h, rid, bid = new_restaurant(client)
    add_item(client, h, rid, bid, name="Garden Salad Bowl", ingredients=[{"name": "lettuce"}, {"name": "tomato"}])
    cust = new_customer(client, codes=("milk",))
    body = analyze(client, cust, restaurant_id=rid, dish_name="garden plate").json()  # 0.62 similarity
    assert body["dish"] is not None and not body["dish"]["confident"]
    assert mine(body) == {"milk": "UNKNOWN"}  # records may belong to a different dish
    assert any("not sure this is" in lim for lim in body["result"]["limitations"])
    assert body["candidates"][0]["name"] == "Garden Salad Bowl"


def test_dish_not_on_menu(client, demo):
    body = analyze(client, demo["cust"], restaurant_id=demo["rid"], dish_name="Margherita Pizza").json()
    assert body["dish"] is None and body["can_submit_feedback"] is False
    assert mine(body) == {"milk": "UNKNOWN", "peanuts": "UNKNOWN"}
    assert any("could not be matched" in lim for lim in body["result"]["limitations"])
    assert any("butter, ghee, cream" in q for q in body["questions_for_staff"])


def test_unverified_recipe_cannot_establish_absence(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Lemon Rice", ingredients=[{"name": "rice"}, {"name": "lemon"}], verified=False)
    cust = new_customer(client, codes=("milk",))
    body = analyze(client, cust, restaurant_id=rid, menu_item_id=item["id"]).json()
    assert body["result"]["sources"][0]["type"] == "restaurant_unverified"
    assert mine(body) == {"milk": "UNKNOWN"}


def test_branch_selection(client):
    h, rid, bid = new_restaurant(client)
    add_item(client, h, rid, bid, name="Toast", ingredients=[{"name": "bread"}])
    client.post(f"/api/restaurants/{rid}/branches", headers=h, json={"name": "Second"})
    cust = new_customer(client)
    assert analyze(client, cust, restaurant_id=rid, dish_name="toast").status_code == 422
    assert analyze(client, cust, restaurant_id=rid, branch_id=bid, dish_name="toast").json()["dish"]["name"] == "Toast"
    h2, rid2, bid2 = new_restaurant(client)
    assert analyze(client, cust, restaurant_id=rid, branch_id=bid2, dish_name="toast").status_code == 404


def test_unregistered_restaurant_without_ai(client, demo):
    body = analyze(client, demo["cust"], restaurant_name="Corner Cafe", location="Springfield", dish_name="Pad Thai").json()
    assert body["restaurant"]["registered"] is False
    assert mine(body) == {"milk": "UNKNOWN", "peanuts": "UNKNOWN"}
    assert any("not registered" in lim for lim in body["result"]["limitations"])
    assert any("allergen menu" in q for q in body["questions_for_staff"])
    assert body["result"]["sources"] == []


def test_unregistered_with_public_recipes(client, demo, monkeypatch):
    monkeypatch.setattr(rp, "public_recipe_candidates", lambda dish, ctx: RecipeCandidates(
        dish="pad thai",
        ingredients=["rice noodles", "fish sauce", "tamarind", "egg", "peanuts", "bean sprouts"],
        sources=[RecipeSource(title="Pad Thai recipe", url="https://recipes.example/pad-thai")],
    ))
    body = analyze(client, demo["cust"], restaurant_name="Corner Cafe", dish_name="Pad Thai").json()
    # Public recipes never make anything PRESENT or NOT LISTED: candidates only.
    assert mine(body) == {"peanuts": "UNKNOWN", "milk": "UNKNOWN"}
    peanuts = next(a for a in body["result"]["your_allergens"] if a["code"] == "peanuts")
    assert "may be present" in peanuts["reasons"][0]
    assert body["result"]["sources"][0]["type"] == "public_recipe"
    assert body["public_recipe_sources"][0]["url"] == "https://recipes.example/pad-thai"
    assert any("not how this restaurant" in lim for lim in body["result"]["limitations"])


def test_photo_only_match_is_capped(client, demo, monkeypatch):
    monkeypatch.setattr(rp, "identify_dish", lambda imgs, names: DishGuess(
        is_food=True, dish_description="butter chicken", menu_choice="Butter Chicken", confidence=0.95))
    body = analyze(client, demo["cust"], [dish_photo()], restaurant_id=demo["rid"]).json()
    assert body["dish"]["match_method"] == "photo"
    assert body["dish"]["match_confidence"] == 0.6 and not body["dish"]["confident"]
    # The photo alone isn't proof of the recipe: the records' milk is only a candidate (UNKNOWN), and
    # their cross-contact note is shown as possible cross-contact, flagged as possibly another dish's.
    assert mine(body) == {"milk": "UNKNOWN", "peanuts": "POSSIBLE_CROSS_CONTACT"}
    peanuts = next(a for a in body["result"]["your_allergens"] if a["code"] == "peanuts")
    assert "may belong to a different item" in peanuts["reasons"][0]
    assert body["candidates"][0]["name"] == "Butter Chicken"


def test_photo_plus_name_agreement_is_trusted(client, demo, monkeypatch):
    monkeypatch.setattr(rp, "identify_dish", lambda imgs, names: DishGuess(
        is_food=True, dish_description="butter chicken", menu_choice="Butter Chicken", confidence=0.8))
    body = analyze(client, demo["cust"], [dish_photo()], restaurant_id=demo["rid"], dish_name="butter chicken").json()
    assert body["dish"]["match_method"] == "dish_name+photo" and body["dish"]["confident"]
    assert mine(body) == {"milk": "PRESENT", "peanuts": "POSSIBLE_CROSS_CONTACT"}


def test_input_validation(client, demo):
    assert analyze(client, demo["cust"], dish_name="x").status_code == 422  # no restaurant
    assert analyze(client, demo["cust"], restaurant_id=demo["rid"]).status_code == 422  # no dish
    assert analyze(client, demo["cust"], restaurant_id=999999, dish_name="x").status_code == 404
    assert analyze(client, demo["owner"], restaurant_id=demo["rid"], dish_name="x").status_code == 403
