"""The centralized workflow graph, assistant, memory, health and ops endpoints through HTTP.

Every count asserted here is produced by real API calls on a synthetic case — the graph is
proven to move when the underlying records move.
"""

from __future__ import annotations

import json
from datetime import timedelta

from app.infrastructure.external_integrations import sign
from tests.helpers import add_member, new_case, sign_up, upload

DOCS = ["01-warranty-deed-1978.txt", "02-death-certificate-ernest-parker.txt", "05-tax-bill-2023.txt", "06-tax-bill-2024.txt"]


def by_id(flow):
    return {n["id"]: n for n in flow["nodes"]}


def test_case_flow_is_computed_live_and_moves_with_the_records(app):
    admin, _ = sign_up(app)
    case_id = new_case(admin)["case"]["id"]

    empty = admin.get(f"/api/v1/cases/{case_id}/flow").json()
    assert by_id(empty)["sources"]["metric"] == 0 and empty["next_action"]["node"] == "sources"
    assert empty["critical_path"][-1] == "resolution" and empty["shortest_path"]["nodes"][0] == "sources"

    for name in DOCS:
        upload(admin, case_id, name)
    flow = admin.get(f"/api/v1/cases/{case_id}/flow").json()
    nodes = by_id(flow)
    facts = admin.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    assert nodes["sources"]["metric"] == 4
    assert nodes["extraction"]["metric"] == len(facts) and nodes["extraction"]["remaining"] == sum(f["status"] == "unreviewed" for f in facts)
    assert nodes["pii"]["metric"] >= 1  # the death certificate carries an SSN
    review_items = [i for i in flow["needs_you"] if i["kind"] == "fact_review"]
    assert sum(i["count"] for i in review_items) == nodes["extraction"]["remaining"]
    assert all(i["href"].startswith(f"#case/{case_id}/evidence/") for i in review_items)

    # Confirming a fact lowers the remaining work by exactly one.
    admin.patch(f"/api/v1/facts/{facts[0]['id']}", json={"status": "confirmed"})
    after = by_id(admin.get(f"/api/v1/cases/{case_id}/flow").json())
    assert after["extraction"]["remaining"] == nodes["extraction"]["remaining"] - 1

    # Finance layer: derived obligations + reconciliation show up with real amounts.
    admin.post(f"/api/v1/cases/{case_id}/obligations/derive")
    admin.post(f"/api/v1/cases/{case_id}/reconciliation/runs")
    fin = by_id(admin.get(f"/api/v1/cases/{case_id}/flow").json())
    obligations = admin.get(f"/api/v1/cases/{case_id}/financials").json()["obligations"]
    assert fin["obligations"]["metric"] == len(obligations) > 0
    assert fin["obligations"]["stats"]["total_cents"] == sum(o["amount_cents"] for o in obligations)
    assert fin["reconciliation"]["status"] in ("attention", "done")


def test_org_flow_aggregates_and_ranks_cases(app):
    admin, _ = sign_up(app)
    a = new_case(admin, "Case A — 1 Elm St")["case"]["id"]
    new_case(admin, "Case B — 2 Oak St")
    upload(admin, a, DOCS[0])
    flow = admin.get("/api/v1/flow").json()
    assert flow["scope"]["open_cases"] == 2 and by_id(flow)["sources"]["metric"] == 1
    assert {c["id"] for c in flow["cases"]} >= {a}
    assert flow["algorithms"]["critical_path"].startswith("Critical Path Method")


def test_needs_you_respects_roles_and_two_person_rule(app):
    admin, _ = sign_up(app)
    paralegal, _ = add_member(admin, app, "paralegal", "Pat")
    viewer, _ = add_member(admin, app, "viewer", "Val")
    case_id = new_case(paralegal)["case"]["id"]
    upload(paralegal, case_id, DOCS[0])
    assert any(i["kind"] == "fact_review" for i in paralegal.get("/api/v1/flow").json()["needs_you"])
    assert viewer.get("/api/v1/flow").json()["needs_you"] == []  # viewers cannot review, so nothing is asked of them
    proposed = paralegal.post(f"/api/v1/cases/{case_id}/actions", json={"action": "case.close_unresolved", "params": {"reason": "client withdrew"}, "rationale": "test"})
    assert proposed.status_code == 201, proposed.text
    mine = [i for i in paralegal.get("/api/v1/flow").json()["needs_you"] if i["kind"] == "approval"]
    assert mine and "waiting on another approver" in mine[0]["title"]
    theirs = [i for i in admin.get("/api/v1/flow").json()["needs_you"] if i["kind"] == "approval"]
    assert theirs and "approve action" in theirs[0]["suggests"]
    assert [i for i in viewer.get("/api/v1/flow").json()["needs_you"] if i["kind"] == "approval"] == []


def test_flow_assistant_and_memory_are_tenant_isolated(app):
    a_admin, _ = sign_up(app)
    b_admin, _ = sign_up(app)
    case_a = new_case(a_admin, "Org A secret case — 9 Pine")["case"]["id"]
    upload(a_admin, case_a, DOCS[0])
    a_admin.post("/api/v1/assistant", json={"message": "who is the grantee?", "case_id": case_a})

    assert b_admin.get(f"/api/v1/cases/{case_a}/flow").status_code == 404
    assert b_admin.post("/api/v1/assistant", json={"message": "who is the grantee?", "case_id": case_a}).status_code == 404
    found = b_admin.post("/api/v1/assistant", json={"message": "find Pine"}).json()
    assert found["items"] == [] and found["intent"] == "find"
    b_memory = b_admin.get("/api/v1/memory/graph").json()
    assert all(case_a not in n["id"] for n in b_memory["nodes"])
    b_flow = b_admin.get("/api/v1/flow").json()
    assert b_flow["scope"]["open_cases"] == 0 and b_flow["needs_you"] == []


def test_assistant_routes_to_grounded_sources_and_never_logs_message_text(app):
    admin, _ = sign_up(app)
    case_id = new_case(admin, "Magnolia heirs — 418 Magnolia Road")["case"]["id"]
    upload(admin, case_id, DOCS[0])

    in_case = admin.post("/api/v1/assistant", json={"message": "Who are the grantees on the deed?", "case_id": case_id}).json()
    assert in_case["intent"] == "case_question" and in_case["citations"]
    assert {c["evidence_status"] for c in in_case["citations"]} <= {"verified", "awaiting_review", "conflicting", "unextracted"}
    assert admin.get(f"/api/v1/cases/{case_id}/questions").json()["questions"][-1]["question"] == "Who are the grantees on the deed?"

    needs = admin.post("/api/v1/assistant", json={"message": "what needs me?"}).json()
    assert needs["intent"] == "needs" and needs["items"] and needs["items"][0]["href"].startswith("#")
    status = admin.post("/api/v1/assistant", json={"message": "give me the status"}).json()
    assert status["intent"] == "status" and "open case" in status["reply"]
    found = admin.post("/api/v1/assistant", json={"message": "find Magnolia"}).json()
    assert found["items"][0]["href"] == f"#case/{case_id}"
    general = admin.post("/api/v1/assistant", json={"message": "hello there"}).json()
    assert general["intent"] == "general" and "live data" in general["reply"]  # AI off: help, no invented text

    log = admin.get("/api/v1/audit").json()["events"]
    queries = [e for e in log if e["event"] == "assistant.query"]
    assert len(queries) == 4 and all(set(e["detail"]) == {"intent", "chars"} for e in queries)

    memory = admin.get("/api/v1/memory/graph").json()
    kinds = {n["kind"] for n in memory["nodes"]}
    assert "user" in kinds and "case" in kinds and memory["events_considered"] > 0
    assert any(t in memory["topics"] for t in ("grantees", "deed"))
    assert memory["algorithm"].startswith("personalized PageRank")
    assert any(s.get("href", "").startswith(f"#case/{case_id}") for s in memory["suggestions"])


def test_assistant_is_rate_limited(app, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ASSISTANT_RATE_PER_MINUTE", "2")
    get_settings.cache_clear()
    try:
        admin, _ = sign_up(app)
        codes = [admin.post("/api/v1/assistant", json={"message": "status"}).status_code for _ in range(3)]
    finally:
        get_settings.cache_clear()
    assert codes == [200, 200, 429]


def test_every_route_commits_before_the_response_is_sent(app):
    """A write must be committed before the client is told it succeeded (FastAPI's default
    "request" scope commits after the response, so a failed commit would look like a 200)."""
    from fastapi.routing import APIRoute

    from app.infrastructure.database.session import get_db

    def walk(dependant):
        for dep in dependant.dependencies:
            if dep.call is get_db:
                yield dep
            yield from walk(dep)

    offenders = [
        route.path for route in app.routes if isinstance(route, APIRoute)
        for dep in walk(route.dependant) if getattr(dep, "scope", None) != "function"
    ]
    assert offenders == []


def test_health_ready_and_request_ids(app, anon):
    live = anon.get("/health")
    assert live.status_code == 200 and live.json()["ok"] is True and "database" not in live.json()
    ready = anon.get("/ready")
    body = ready.json()
    assert ready.status_code == 200 and body["checks"]["database"]["ok"] and body["checks"]["db_pool"]["ok"]
    assert "secret" not in json.dumps(body).lower() and "password" not in json.dumps(body).lower()
    echoed = anon.get("/api/v1/health", headers={"x-request-id": "trace-me-1234"})
    assert echoed.headers["x-request-id"] == "trace-me-1234"
    assert len(anon.get("/health").headers["x-request-id"]) == 32


def test_ops_metrics_need_admin_and_jobs_need_a_signature(app, monkeypatch):
    from app.core.config import get_settings

    admin, _ = sign_up(app)
    paralegal, _ = add_member(admin, app, "paralegal", "Pat")
    assert admin.get("/api/v1/ops/metrics").status_code == 200
    assert paralegal.get("/api/v1/ops/metrics").status_code == 403

    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "s" * 40)
    get_settings.cache_clear()
    try:
        body = b"{}"
        assert admin.post("/api/v1/ops/jobs/verify-audit", content=body).status_code == 401
        bad = admin.post("/api/v1/ops/jobs/verify-audit", content=body, headers={"x-groundwork-signature": sign(body, "wrong")})
        assert bad.status_code == 401
        good = admin.post("/api/v1/ops/jobs/verify-audit", content=body, headers={"x-groundwork-signature": sign(body, "s" * 40)})
        assert good.status_code == 200 and good.json()["ok"] is True
        unknown = admin.post("/api/v1/ops/jobs/drop-tables", content=body, headers={"x-groundwork-signature": sign(body, "s" * 40)})
        assert unknown.status_code == 404
    finally:
        get_settings.cache_clear()


def test_expire_actions_job_is_idempotent_and_audited(app, db):
    from app import jobs
    from app.infrastructure.database.models import WorkflowAction, utcnow

    admin, _ = sign_up(app)
    case_id = new_case(admin)["case"]["id"]
    response = admin.post(f"/api/v1/cases/{case_id}/actions", json={"action": "case.close_unresolved", "params": {"reason": "unreachable"}, "rationale": "r"})
    assert response.status_code == 201, response.text
    rows = db.query(WorkflowAction).filter(WorkflowAction.case_id == case_id).all()
    assert len(rows) == 1
    rows[0].expires_at = utcnow() - timedelta(days=1)
    db.commit()
    first = jobs.run("expire-actions")
    second = jobs.run("expire-actions")
    assert first["expired"] >= 1 and second["expired"] == 0
    events = [e["event"] for e in admin.get(f"/api/v1/audit?case_id={case_id}").json()["events"]]
    assert "action.expired" in events
    assert admin.get("/api/v1/audit/verify").json()["ok"] is True
