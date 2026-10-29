from app.seed.seed import DEMO_CUSTOMER_EMAIL, DEMO_OWNER_EMAIL, DEMO_PASSWORD


def login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_analyze_text_uses_saved_profile(client):
    # Demo customer is allergic to milk and peanuts (set at sign-up, never sent with the request).
    r = client.post(
        "/api/analyze/text",
        headers=login(client, DEMO_CUSTOMER_EMAIL),
        json={
            "product_name": "Oat cookies",
            "ingredients_text": "Ingredients: oat flakes, wheat flour, sugar, butter (milk), salt.",
            "may_contain_text": "May contain peanuts.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall_outcome"] == "PRESENT"
    assert {a["code"]: a["outcome"] for a in body["your_allergens"]} == {
        "milk": "PRESENT",
        "peanuts": "POSSIBLE_CROSS_CONTACT",
    }
    assert {a["code"] for a in body["other_allergens"]} == {"gluten"}
    assert body["explanation"] and body["explanation_source"] in ("template", "llm")
    assert "not medical advice" in body["disclaimer"]


def test_analyze_text_customers_only(client):
    r = client.post(
        "/api/analyze/text", headers=login(client, DEMO_OWNER_EMAIL), json={"ingredients_text": "rice"}
    )
    assert r.status_code == 403
    assert client.post("/api/analyze/text", json={"ingredients_text": "rice"}).status_code == 401
