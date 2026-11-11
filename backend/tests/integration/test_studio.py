"""Engineering Studio: every tool works offline (rules) and degrades safely when Gemini fails."""

from __future__ import annotations

import ast

from app.services import ai, studio
from tests.helpers import add_member, sign_up

SAMPLE = '''
class Repo:
    def get(self, key):
        return self.load(key)
    def load(self, key):
        return key

def parse(text, items=[]):
    try:
        return int(text)
    except:
        raise ValueError("bad")

async def fetch(url):
    return url
'''


def test_pipeline_end_to_end(app):
    client, _ = sign_up(app)
    assert client.get("/api/v1/studio/status").json()["ai"]["configured"] is False
    req = client.post("/api/v1/studio/requirements", json={"idea": "Users upload invoices. An AI assistant summarises them. Admins see dashboards."}).json()
    assert req["source"] == "rules" and any("Cloud Storage" in r for r in req["functional"])
    st = client.post("/api/v1/studio/stack", json={"requirements": " ".join(req["functional"]), "language": "node"}).json()
    layers = {r["layer"] for r in st["stack"]}
    assert {"AI", "Document AI", "Analytics"} <= layers and len(st["bom"]) == len(st["stack"])
    arch = client.post("/api/v1/studio/architecture", json={"requirements": "invoices ai", "stack": st["stack"]}).json()
    assert arch["mermaid"].startswith("flowchart LR") and "api -->" in arch["mermaid"]
    sc = client.post("/api/v1/studio/scaffold", json={"name": "Invoice Hub"}).json()
    paths = {f["path"] for f in sc["files"]}
    assert {"invoice_hub/main.py", "tests/test_health.py", "Dockerfile"} <= paths
    for f in sc["files"]:
        if f["path"].endswith(".py"):
            ast.parse(f["content"])
    stories = client.post("/api/v1/studio/stories", json={"requirements": req["functional"][:4]}).json()["stories"]
    assert len(stories) == 4 and stories[0]["acceptance"]


def test_code_tools(app):
    client, _ = sign_up(app)
    health = client.post("/api/v1/studio/code-health", json={"code": SAMPLE}).json()
    rules = {i["rule"] for i in health["issues"]}
    assert {"bare-except", "mutable-default"} <= rules and health["score"] < 100
    viz = client.post("/api/v1/studio/visualize", json={"code": SAMPLE}).json()
    assert viz["kind"] == "classes" and {"from": "get", "to": "load"} in viz["calls"]
    tests = client.post("/api/v1/studio/tests", json={"code": SAMPLE, "module": "repo"}).json()
    ast.parse(tests["code"])
    assert "test_parse_rejects_invalid_input" in tests["code"] and "from repo import" in tests["code"]
    bad = client.post("/api/v1/studio/code-health", json={"code": "def ("})
    assert bad.status_code == 422 and "syntax" in bad.json()["error"]
    js = client.post("/api/v1/studio/tests", json={"code": "function add(a,b){return a+b}", "language": "javascript"}).json()
    assert "describe('add'" in js["code"]


def test_studio_requires_sign_in(app):
    from fastapi.testclient import TestClient

    assert TestClient(app).post("/api/v1/studio/requirements", json={"idea": "x y z"}).status_code == 401


def test_gemini_refinement_and_fallback(monkeypatch):
    studio.CACHE._data.clear()
    monkeypatch.setattr(ai, "enabled", lambda: True)
    monkeypatch.setattr(ai, "model_name", lambda: "gemini-test")
    monkeypatch.setattr(ai, "generate_json", lambda *a, **k: {"summary": "refined"})
    out = studio.requirements("A shop where users pay online")
    assert out["source"] == "gemini" and out["summary"] == "refined" and out["functional"]

    def boom(*a, **k):
        raise TimeoutError

    assert studio.requirements("A shop where users pay online").get("cached") is True  # identical request: no second call
    monkeypatch.setattr(ai, "generate_json", boom)
    out = studio.requirements("A different shop where users pay online")
    assert out["source"] == "rules" and "TimeoutError" in out["note"]


def test_gemini_api_key_backend(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setattr(ai, "get_settings", lambda: Settings(ai_backend="gemini_api", gemini_api_key="k"))
    assert ai.enabled()
    monkeypatch.setattr(ai, "get_settings", lambda: Settings(ai_backend="gemini_api", gemini_api_key=""))
    assert not ai.enabled()


IDEA = "Farmers upload land documents. An AI assistant summarises them. Admins see dashboards. It must be fast and secure."


def test_project_workflow(app):
    admin, me = sign_up(app)
    attorney, _ = add_member(admin, app, "attorney")
    viewer, _ = add_member(admin, app, "viewer")
    assert viewer.post("/api/v1/studio/projects", json={"name": "x", "idea": IDEA}).status_code == 403

    p = admin.post("/api/v1/studio/projects", json={"name": "Land Desk", "idea": IDEA, "persona": "farmer"}).json()
    pid = p["id"]
    assert p["progress"]["next"] == "requirements" and p["steps"]["requirements"] is None

    # gating: stack needs requirements
    r = admin.post(f"/api/v1/studio/projects/{pid}/steps/stack/run")
    assert r.status_code == 409 and "requirements" in r.json()["error"]

    p = admin.post(f"/api/v1/studio/projects/{pid}/run-all").json()
    assert p["ran"] == ["requirements", "stack", "architecture", "stories", "scaffold"]
    assert p["progress"]["percent"] == 100
    req = p["steps"]["requirements"]["content"]
    assert {a["term"] for a in req["ambiguities"]} >= {"fast", "secure"}
    assert p["traceability"]["coverage"] == 100
    files = {f["path"] for f in p["steps"]["scaffold"]["content"]["files"]}
    assert {"infra/main.tf", "cloudbuild.yaml", "docs/architecture.md", "docs/bom.csv", "tests/test_acceptance.py"} <= files
    tf = next(f["content"] for f in p["steps"]["scaffold"]["content"]["files"] if f["path"] == "infra/main.tf")
    assert "aiplatform.googleapis.com" in tf and "roles/aiplatform.user" in tf
    acc = next(f["content"] for f in p["steps"]["scaffold"]["content"]["files"] if f["path"] == "tests/test_acceptance.py")
    ast.parse(acc)
    assert p["traceability"]["rows"][0]["tests"][0] in acc

    # nothing stale → run-all is a no-op
    assert admin.post(f"/api/v1/studio/projects/{pid}/run-all").json()["ran"] == []

    # approval: separate approver, then stale detection after an upstream edit
    assert admin.post(f"/api/v1/studio/projects/{pid}/steps/requirements/approve").status_code == 403
    p = attorney.post(f"/api/v1/studio/projects/{pid}/steps/requirements/approve").json()
    assert p["progress"]["approved"] == ["requirements"]
    assert attorney.post(f"/api/v1/studio/projects/{pid}/steps/requirements/approve").status_code == 409

    edited = {**req, "functional": req["functional"] + ["FR-99: The system shall export PDFs."]}
    p = admin.put(f"/api/v1/studio/projects/{pid}/steps/requirements", json={"content": edited}).json()
    assert p["steps"]["requirements"]["version"] == 2 and p["steps"]["requirements"]["source"] == "edited"
    assert p["steps"]["requirements"]["approved"] is False
    assert p["steps"]["stack"]["stale"] == ["requirements"] and "FR-99" in p["traceability"]["uncovered"]
    assert attorney.post(f"/api/v1/studio/projects/{pid}/steps/stack/approve").status_code == 409
    assert admin.put(f"/api/v1/studio/projects/{pid}/steps/requirements", json={"content": {"x": 1}}).status_code == 422

    p = admin.post(f"/api/v1/studio/projects/{pid}/run-all").json()
    assert p["ran"] == ["stack", "architecture", "stories", "scaffold"] and p["traceability"]["coverage"] == 100

    hist = admin.get(f"/api/v1/studio/projects/{pid}/steps/requirements/versions").json()["versions"]
    assert [v["version"] for v in hist] == [2, 1] and hist[1]["approved"]
    assert admin.get(f"/api/v1/studio/projects/{pid}/steps/requirements/versions/1").json()["content"]["functional"]

    # exports
    md = admin.get(f"/api/v1/studio/projects/{pid}/export.md")
    assert md.status_code == 200 and "## Traceability" in md.text and "```mermaid" in md.text
    csv_ = admin.get(f"/api/v1/studio/projects/{pid}/stories.csv")
    assert csv_.text.startswith("Issue Type,Summary")
    z = admin.get(f"/api/v1/studio/projects/{pid}/scaffold.zip")
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "land-desk/infra/main.tf" in names and z.headers["content-type"] == "application/zip"

    # audit chain records the workflow, and stays valid
    events = {e["event"] for e in admin.get("/api/v1/audit?limit=200").json()["events"]}
    assert {"studio.project_created", "studio.requirements_generated", "studio.requirements_edited", "studio.artifact_approved"} <= events
    assert admin.get("/api/v1/audit/verify").json()["ok"] is True

    # tenancy: another organisation cannot see the project
    other, _ = sign_up(app)
    assert other.get(f"/api/v1/studio/projects/{pid}").status_code == 404
    assert other.get("/api/v1/studio/projects").json()["projects"] == []

    # archive
    admin.patch(f"/api/v1/studio/projects/{pid}", json={"status": "archived"})
    assert admin.post(f"/api/v1/studio/projects/{pid}/steps/stack/run").status_code == 409
    assert admin.get("/api/v1/studio/projects").json()["projects"] == []


def test_studio_rate_limit(app, monkeypatch):
    from app.api.v1 import studio as studio_api
    from app.core.config import get_settings

    client, _ = sign_up(app)
    monkeypatch.setattr(get_settings(), "studio_rate_per_minute", 2)
    studio_api._hits.clear()
    codes = [client.post("/api/v1/studio/code-health", json={"code": "x = 1"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
