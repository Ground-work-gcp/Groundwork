"""Authentication, roles, CSRF, and tenant isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import WEB, add_member, new_case, sign_up, upload


def test_health_is_public_and_reports_the_database(anon):
    body = anon.get("/api/v1/health").json()
    assert body["ok"] is True and body["database"]["ok"] is True and body["service"] == "groundwork"


def test_everything_else_requires_a_session(anon):
    for path in ("/api/v1/cases", "/api/v1/overview", "/api/v1/audit", "/api/v1/approvals", "/api/v1/risks"):
        assert anon.get(path).status_code == 401
    assert anon.get("/api/v1/cases").json() == {"error": "Sign in required"}


def test_sign_in_rejects_bad_passwords_without_revealing_usernames(app):
    _client, user = sign_up(app)
    fresh = TestClient(app)
    wrong = fresh.post("/api/v1/auth/sign-in", json={"username": user["username"], "password": "nope-nope-nope"})
    missing = fresh.post("/api/v1/auth/sign-in", json={"username": "no-such-user", "password": "nope-nope-nope"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_bearer_tokens_work_for_api_clients(app):
    client, user = sign_up(app)
    token = TestClient(app).post("/api/v1/auth/token", json={"username": user["username"], "password": "correct-horse-battery"}).json()["access_token"]
    api = TestClient(app, headers={"authorization": f"Bearer {token}"})
    assert api.get("/api/v1/auth/me").json()["user"]["username"] == user["username"]
    assert api.post("/api/v1/cases", json={"title": "Via token"}).status_code == 201  # no CSRF header needed


def test_cookie_writes_need_the_client_header(app):
    client, _ = sign_up(app)
    client.headers.pop("x-groundwork-client")
    assert client.post("/api/v1/cases", json={"title": "No header"}).status_code == 403
    assert client.post("/api/v1/cases", json={"title": "With header"}, headers=WEB).status_code == 201


def test_viewer_can_read_but_not_write(app):
    admin, _ = sign_up(app)
    case = new_case(admin)
    viewer, _ = add_member(admin, app, "viewer")
    assert viewer.get(f"/api/v1/cases/{case['case']['id']}").status_code == 200
    assert viewer.post("/api/v1/cases", json={"title": "Nope"}).status_code == 403
    assert viewer.post(f"/api/v1/cases/{case['case']['id']}/documents", files={"file": ("a.txt", b"Grantor: A B", "text/plain")}).status_code == 403
    assert viewer.post("/api/v1/settings/users", json={}).status_code in (403, 422)


def test_other_organisation_cannot_read_or_write(app):
    owner, _ = sign_up(app, "Org A")
    case = new_case(owner)
    case_id = case["case"]["id"]
    document = upload(owner, case_id, "01-warranty-deed-1978.txt")
    intruder, _ = sign_up(app, "Org B")
    assert intruder.get(f"/api/v1/cases/{case_id}").status_code == 404
    assert intruder.get(f"/api/v1/documents/{document['id']}").status_code == 404
    assert intruder.get(f"/api/v1/documents/{document['id']}/content").status_code == 404
    assert intruder.post(f"/api/v1/cases/{case_id}/questions", json={"question": "who is the grantor?"}).status_code == 404
    facts = owner.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    assert intruder.patch(f"/api/v1/facts/{facts[0]['id']}", json={"status": "rejected"}).status_code == 404
    assert intruder.get("/api/v1/cases").json()["cases"] == []
    assert all(e["case_id"] != case_id for e in intruder.get("/api/v1/audit").json()["events"])


def test_internal_admin_boundary_is_hidden_from_customers(app):
    admin, _ = sign_up(app)
    response = admin.post("/api/v1/admin/reconciliation/settlement-batch", files=[("files", ("bank.csv", b"x", "text/csv"))])
    assert response.status_code == 404
