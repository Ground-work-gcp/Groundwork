"""Contract: every endpoint the frontend calls exists in the backend with that method.

The frontend's API client (``frontend/src/services/api.ts``) is the single place the UI
talks to the server. This test parses it and checks each call against the OpenAPI schema,
so a renamed route fails CI instead of failing in a user's browser.
"""

from __future__ import annotations

import re
from pathlib import Path

API_CLIENT = Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "api.ts"
CALL = re.compile(r"""\b(get|post|patch|put|del|text|upload)\b[^(\n=]*?\(\s*[`"'](/[^`"']*)[`"']""")


def _normalise(path: str) -> str:
    path = re.sub(r"\$\{q\(.*?\)\}", "", path).split("?")[0]
    return re.sub(r"\$\{[^}]+\}", "{}", path)


def test_every_frontend_call_matches_a_backend_route(app):
    spec = app.openapi()
    routes = {(method.upper(), re.sub(r"\{[^}]+\}", "{}", path[len("/api/v1"):])) for path, ops in spec["paths"].items() for method in ops}
    source = API_CLIENT.read_text(encoding="utf-8")
    calls = CALL.findall(source)
    assert len(calls) > 40, "the API client should be parsed"
    verbs = {"get": "GET", "text": "GET", "post": "POST", "upload": "POST", "patch": "PATCH", "put": "PUT", "del": "DELETE"}
    missing = sorted({(verbs[v], _normalise(p)) for v, p in calls} - routes)
    assert missing == [], f"frontend calls with no backend route: {missing}"


def test_no_legacy_service_urls_remain_in_the_frontend():
    source_dir = API_CLIENT.parents[1]
    offenders = []
    for file in source_dir.rglob("*.ts*"):
        text = file.read_text(encoding="utf-8")
        if re.search(r"amazonaws|execute-api|azurewebsites|blob\.core\.windows|cognitiveservices|lambda-url", text):
            offenders.append(str(file))
    assert offenders == []
