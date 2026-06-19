"""Smoke tests covering health, auth, and a protected end-to-end flow."""


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "models" in body


def test_login_bad_credentials(client):
    resp = client.post("/auth/login", json={"workerId": "nope", "pin": "0000"})
    assert resp.status_code == 401


def test_protected_requires_auth(client):
    assert client.get("/risk-scores").status_code == 401


def test_me(client, auth_headers):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["workerId"] == "CHW-T01"


def test_child_vaccination_and_risk_flow(client, auth_headers):
    # Register a child
    resp = client.post("/children", headers=auth_headers, json={
        "name": "Test Child", "gender": "F", "distanceKm": 2.5,
    })
    assert resp.status_code == 201, resp.text
    child = resp.json()
    child_id = child["id"]
    assert "riskScore" in child

    # Record a vaccination
    resp = client.post("/vaccinations", headers=auth_headers, json={
        "childId": child_id, "vaccine": "BCG", "status": "given",
    })
    assert resp.status_code == 201, resp.text

    # Risk score now available
    resp = client.get(f"/risk-scores/child/{child_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert 0.0 <= body["riskScore"] <= 1.0
    assert body["riskLabel"] in {"High", "Medium", "Watch", "Low"}


def test_climate_facilities(client, auth_headers):
    resp = client.get("/climate/facilities", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
