"""Engineering Studio: every tool works offline (rules) and degrades safely when Gemini fails."""

from __future__ import annotations

import ast

from app.services import ai, studio
from tests.helpers import sign_up

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
    monkeypatch.setattr(ai, "enabled", lambda: True)
    monkeypatch.setattr(ai, "model_name", lambda: "gemini-test")
    monkeypatch.setattr(ai, "generate_json", lambda *a, **k: {"summary": "refined"})
    out = studio.requirements("A shop where users pay online")
    assert out["source"] == "gemini" and out["summary"] == "refined" and out["functional"]

    def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(ai, "generate_json", boom)
    out = studio.requirements("A shop where users pay online")
    assert out["source"] == "rules" and "TimeoutError" in out["note"]


def test_gemini_api_key_backend(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setattr(ai, "get_settings", lambda: Settings(ai_backend="gemini_api", gemini_api_key="k"))
    assert ai.enabled()
    monkeypatch.setattr(ai, "get_settings", lambda: Settings(ai_backend="gemini_api", gemini_api_key=""))
    assert not ai.enabled()
