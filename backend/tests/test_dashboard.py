from tests.helpers import add_item, auth, login, new_customer, new_restaurant


def test_menu_and_recipe_versions(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(
        client, h, rid, bid, name="Dal Makhani",
        ingredients=[{"name": "black lentils"}, {"name": "ghee"}, {"name": "cream", "allergen_codes": ["milk"]}],
        cross=[{"allergen_code": "peanuts", "level": "possible", "description": "Shared kitchen"}],
        verified=False,
    )
    v1 = item["current_version"]
    assert v1["version_number"] == 1 and v1["verification_status"] == "unverified"
    # "ghee" indicates milk; the recipe only declares milk on "cream", which still covers the dish.
    assert v1["warnings"] == []
    assert v1["cross_contacts"][0]["allergen_code"] == "peanuts"

    # Verify v1
    r = client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions/{v1['id']}/verify", headers=h)
    assert r.json()["verification_status"] == "verified" and r.json()["verified_by"]

    # New version: v1 kept, v2 current. Undeclared allergen produces a warning.
    r = client.post(
        f"/api/restaurants/{rid}/menu/{item['id']}/versions", headers=h,
        json={"ingredients": [{"name": "black lentils"}, {"name": "butter"}], "change_note": "Swapped cream for butter"},
    )
    assert r.status_code == 201, r.text
    detail = r.json()
    assert [v["version_number"] for v in detail["versions"]] == [2, 1]
    assert [v["is_current"] for v in detail["versions"]] == [True, False]
    assert detail["current_version"]["warnings"][0]["allergen_code"] == "milk"
    assert detail["versions"][1]["ingredients"][2]["name"] == "cream"  # history unchanged

    # Old versions can't be verified
    r = client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions/{v1['id']}/verify", headers=h)
    assert r.status_code == 409


def test_recipe_validation(client):
    h, rid, bid = new_restaurant(client)
    r = client.post(f"/api/restaurants/{rid}/menu", headers=h, json={
        "branch_id": bid, "name": "X", "recipe": {"ingredients": [{"name": "a", "allergen_codes": ["unicorn"]}]}})
    assert r.status_code == 422
    r = client.post(f"/api/restaurants/{rid}/menu", headers=h, json={
        "branch_id": bid, "name": "X", "recipe": {"ingredients": [{"name": "Salt"}, {"name": "salt"}]}})
    assert r.status_code == 422


def test_menu_update_archive_and_listing(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid)
    r = client.patch(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h, json={"status": "unavailable"})
    assert r.json()["status"] == "unavailable"
    assert client.delete(f"/api/restaurants/{rid}/menu/{item['id']}", headers=h).status_code == 204
    assert client.get(f"/api/restaurants/{rid}/menu", headers=h).json() == []
    archived = client.get(f"/api/restaurants/{rid}/menu?status=archived", headers=h).json()
    assert [i["id"] for i in archived] == [item["id"]]


def test_branches_and_settings(client):
    h, rid, _ = new_restaurant(client)
    r = client.post(f"/api/restaurants/{rid}/branches", headers=h, json={"name": "Airport", "city": "Springfield"})
    assert r.status_code == 201
    bid = r.json()["id"]
    assert client.patch(f"/api/restaurants/{rid}/branches/{bid}", headers=h, json={"is_active": False}).json()["is_active"] is False
    r = client.patch(f"/api/restaurants/{rid}", headers=h, json={"cuisine": "Indian", "settings": {"currency": "USD"}})
    assert r.json()["cuisine"] == "Indian" and r.json()["settings"] == {"currency": "USD"}
    dash = client.get(f"/api/restaurants/{rid}", headers=h).json()
    assert len(dash["branches"]) == 2 and dash["your_role"] == "owner"


def test_team_roles_and_permissions(client):
    h, rid, bid = new_restaurant(client)
    item = add_item(client, h, rid, bid)

    # New account needs an initial password
    r = client.post(f"/api/restaurants/{rid}/team", headers=h, json={"email": "mgr@example.com", "role": "manager"})
    assert r.status_code == 422
    r = client.post(f"/api/restaurants/{rid}/team", headers=h,
                    json={"email": "mgr@example.com", "role": "manager", "initial_password": "Passw0rd!"})
    assert r.status_code == 201
    mgr_id = r.json()["user_id"]
    client.post(f"/api/restaurants/{rid}/team", headers=h,
                json={"email": "staff@example.com", "role": "staff", "initial_password": "Passw0rd!"})
    assert client.post(f"/api/restaurants/{rid}/team", headers=h,
                       json={"email": "mgr@example.com", "role": "staff"}).status_code == 409

    # Customers can't be added to a restaurant team
    cust = new_customer(client)
    cust_email = client.get("/api/auth/me", headers=cust).json()["email"]
    assert client.post(f"/api/restaurants/{rid}/team", headers=h,
                       json={"email": cust_email, "role": "staff"}).status_code == 409

    mgr = login(client, "mgr@example.com", "Passw0rd!")
    staff = login(client, "staff@example.com", "Passw0rd!")
    # Manager: edits recipes, but no team/settings
    assert client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions", headers=mgr,
                       json={"ingredients": [{"name": "paneer", "allergen_codes": ["milk"]}]}).status_code == 201
    assert client.patch(f"/api/restaurants/{rid}", headers=mgr, json={"cuisine": "x"}).status_code == 403
    assert client.post(f"/api/restaurants/{rid}/team", headers=mgr,
                       json={"email": "z@example.com", "role": "staff", "initial_password": "Passw0rd!"}).status_code == 403
    # Staff: read-only
    assert client.get(f"/api/restaurants/{rid}/menu/{item['id']}", headers=staff).status_code == 200
    assert client.post(f"/api/restaurants/{rid}/menu/{item['id']}/versions", headers=staff,
                       json={"ingredients": []}).status_code == 403
    assert client.get(f"/api/restaurants/{rid}/audit", headers=staff).status_code == 403

    # Last-owner guard
    owner_id = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/restaurants/{rid}/team/{owner_id}", headers=h, json={"role": "manager"}).status_code == 409
    assert client.delete(f"/api/restaurants/{rid}/team/{owner_id}", headers=h).status_code == 409
    assert client.patch(f"/api/restaurants/{rid}/team/{mgr_id}", headers=h, json={"role": "staff"}).json()["role"] == "staff"
    assert client.delete(f"/api/restaurants/{rid}/team/{mgr_id}", headers=h).status_code == 204
    assert client.get(f"/api/restaurants/{rid}", headers=mgr).status_code == 404


def test_tenant_isolation(client):
    h1, rid1, bid1 = new_restaurant(client)
    h2, rid2, bid2 = new_restaurant(client)
    item1 = add_item(client, h1, rid1, bid1)
    # Restaurant 2 can't reach restaurant 1's data, even through its own restaurant id.
    assert client.get(f"/api/restaurants/{rid1}/menu", headers=h2).status_code == 404
    assert client.get(f"/api/restaurants/{rid2}/menu/{item1['id']}", headers=h2).status_code == 404
    assert client.post(f"/api/restaurants/{rid2}/menu/{item1['id']}/versions", headers=h2, json={}).status_code == 404
    assert client.patch(f"/api/restaurants/{rid2}/branches/{bid1}", headers=h2, json={"name": "x"}).status_code == 404
    r = client.post(f"/api/restaurants/{rid2}/menu", headers=h2, json={"branch_id": bid1, "name": "Sneaky"})
    assert r.status_code == 404
    assert client.get(f"/api/restaurants/{rid2}/menu", headers=h2).json() == []


def test_ingredient_check_and_audit(client):
    h, rid, bid = new_restaurant(client)
    r = client.post(f"/api/restaurants/{rid}/ingredients/check", headers=h,
                    json={"names": ["Ghee", "peanut oil", "coconut milk", "rice"]})
    assert [x["allergen_codes"] for x in r.json()] == [["milk"], ["peanuts"], [], []]
    add_item(client, h, rid, bid)
    actions = [a["action"] for a in client.get(f"/api/restaurants/{rid}/audit", headers=h).json()]
    assert "menu_item.created" in actions
