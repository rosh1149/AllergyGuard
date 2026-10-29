from tests.helpers import add_item, login, new_customer, new_restaurant


def analyze(client, cust, rid, bid, item_id):
    r = client.post("/api/analyze/restaurant", headers=cust,
                    data={"restaurant_id": rid, "branch_id": bid, "menu_item_id": item_id})
    assert r.status_code == 200, r.text
    return r.json()


def outcomes(body):
    return {a["code"]: a["outcome"] for a in body["result"]["your_allergens"]}


def test_suggestion_does_not_change_trusted_records_until_confirmed(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Veg Pulao",
                    ingredients=[{"name": "basmati rice"}, {"name": "peas"}, {"name": "oil"}])
    cust = new_customer(client, codes=("milk",))
    assert outcomes(analyze(client, cust, rid, bid, item["id"])) == {"milk": "NOT_LISTED_IN_VERIFIED_RECORDS"}

    r = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "missing_ingredient", "ingredient_name": "Ghee",
        "message": "Tasted strongly of ghee."})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["status"] == "unverified" and r.json()["recipe_version_number"] == 1

    # Trusted record unchanged...
    detail = client.get(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h).json()
    assert len(detail["versions"]) == 1 and detail["pending_feedback"] == 1
    # ...but the unreviewed report means milk can no longer be reported as "not listed".
    body = analyze(client, cust, rid, bid, item["id"])
    assert outcomes(body) == {"milk": "UNKNOWN"}
    assert "customer_suggestion" in {s["type"] for s in body["result"]["sources"]}

    # Duplicate pending suggestion
    assert client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "missing_ingredient", "ingredient_name": "Ghee"}).status_code == 409

    # Staff confirm -> new verified version with ghee (+ milk from the knowledge base), audited.
    r = client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                    json={"decision": "confirmed", "note": "Chef confirmed ghee is used."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["resulting_version_number"] == 2
    cur = client.get(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h).json()["current_version"]
    assert cur["verification_status"] == "verified"
    ghee = cur["ingredients"][-1]
    assert (ghee["name"], ghee["allergen_codes"]) == ("Ghee", ["milk"])
    assert "Customer suggestion" in cur["change_note"]
    assert outcomes(analyze(client, cust, rid, bid, item["id"])) == {"milk": "PRESENT"}

    audit = client.get(f"/api/restaurants/{rid}/audit", headers=h).json()
    confirmed = next(a for a in audit if a["action"] == "feedback.confirmed")
    assert confirmed["entity_id"] == sid and confirmed["details"]["change"].startswith("Added ingredient")

    # Can't decide twice; customer sees the outcome
    assert client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                       json={"decision": "rejected"}).status_code == 409
    mine = client.get("/api/me/feedback", headers=cust).json()
    assert mine[0]["status"] == "confirmed"


def test_rejected_suggestion_changes_nothing(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Plain Rice", ingredients=[{"name": "rice"}, {"name": "salt"}])
    cust = new_customer(client, codes=("peanuts",))
    sid = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "cross_contact", "allergen_code": "peanuts",
        "message": "Same ladle as the satay"}).json()["id"]
    assert outcomes(analyze(client, cust, rid, bid, item["id"])) == {"peanuts": "POSSIBLE_CROSS_CONTACT"}
    r = client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                    json={"decision": "rejected", "note": "Separate ladles are used."})
    assert r.json()["status"] == "rejected" and r.json()["resulting_version_number"] is None
    detail = client.get(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h).json()
    assert len(detail["versions"]) == 1 and detail["pending_feedback"] == 0
    assert outcomes(analyze(client, cust, rid, bid, item["id"])) == {"peanuts": "NOT_LISTED_IN_VERIFIED_RECORDS"}


def test_confirm_other_suggestion_types(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid, name="Fries", ingredients=[{"name": "potato"}, {"name": "salt"}, {"name": "cheese"}])
    cust = new_customer(client, codes=("crustaceans",))

    def suggest(**kw):
        r = client.post("/api/feedback", headers=cust, json={"menu_item_id": item["id"], **kw})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def confirm(sid):
        r = client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h, json={"decision": "confirmed"})
        assert r.status_code == 200, r.text
        return r.json()

    assert confirm(suggest(suggestion_type="cross_contact", allergen_code="crustaceans",
                           message="Shared fryer with prawns"))["resulting_version_number"] == 2
    assert confirm(suggest(suggestion_type="missing_allergen", allergen_code="sulphites"))["resulting_version_number"] == 3
    assert confirm(suggest(suggestion_type="incorrect_ingredient", ingredient_name="cheese"))["resulting_version_number"] == 4
    cur = client.get(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h).json()["current_version"]
    assert [i["name"] for i in cur["ingredients"]] == ["potato", "salt"]
    assert cur["dish_allergen_codes"] == ["sulphites"]
    assert cur["cross_contacts"][0]["allergen_code"] == "crustaceans"
    # Removing an ingredient that isn't there must be handled by staff manually
    sid = suggest(suggestion_type="incorrect_ingredient", ingredient_name="onion")
    assert client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                       json={"decision": "confirmed"}).status_code == 422
    # "other" is reviewed and logged without changing the recipe
    sid = suggest(suggestion_type="other", message="Menu photo looks different")
    assert confirm(sid)["resulting_version_number"] is None


def test_feedback_validation_and_permissions(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid)
    cust = new_customer(client)
    assert client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "missing_ingredient"}).status_code == 422
    assert client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "missing_allergen"}).status_code == 422
    assert client.post("/api/feedback", headers=cust, json={
        "menu_item_id": 999999, "suggestion_type": "other", "message": "x"}).status_code == 404
    assert client.post("/api/feedback", headers=h, json={
        "menu_item_id": item["id"], "suggestion_type": "other", "message": "x"}).status_code == 403

    sid = client.post("/api/feedback", headers=cust, json={
        "menu_item_id": item["id"], "suggestion_type": "other", "message": "x"}).json()["id"]
    client.post(f"/api/restaurants/{rid}/team", headers=h,
                json={"email": "fbstaff@example.com", "role": "staff", "initial_password": "Passw0rd!"})
    staff = login(client, "fbstaff@example.com", "Passw0rd!")
    assert client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=staff,
                       json={"decision": "confirmed"}).status_code == 403
    # Another restaurant can't see or decide it
    h2, rid2, _ = new_restaurant(client)
    assert client.post(f"/api/restaurants/{rid2}/feedback/{sid}/decision", headers=h2,
                       json={"decision": "rejected"}).status_code == 404
    assert client.get(f"/api/restaurants/{rid2}/feedback", headers=h2).json() == []
    assert client.post(f"/api/restaurants/{rid}/feedback/{sid}/decision", headers=h,
                       json={"decision": "unverified"}).status_code == 422
