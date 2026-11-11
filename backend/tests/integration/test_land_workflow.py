"""The rural-land workflow: lifecycle gates, office proceedings, family pathways, consent on paper, the land graph."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from tests.helpers import sign_up, upload

LAND = Path(__file__).resolve().parents[1] / "fixtures" / "land_documents"


def ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def land_case(app):
    admin, _ = sign_up(app)
    r = admin.post("/api/v1/cases", json={"title": "Ramasamy heirs — S.No. 123/4A", "kind": "rural_land"})
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["case"]["kind"] == "rural_land" and [s["key"] for s in case["stages"]][:3] == ["intake", "records", "reconciled"]
    cid = case["case"]["id"]
    admin.put(f"/api/v1/cases/{cid}/land/parcel", json={"region_code": "TN", "village": "Keezhaiyur", "taluk": "Orathanadu", "survey_number": "123/4A", "guideline_inr_per_sqm": 150})
    return admin, cid


def tree(admin, cid):
    ids: dict[str, str] = {}
    for key, body in [
        ("r", {"name": "Ramasamy", "sex": "male", "is_alive": False, "date_of_death": "1987-03-02", "is_recorded_holder": True}),
        ("l", {"name": "Lakshmi Ammal", "sex": "female", "is_alive": False, "date_of_death": "2015-06-10", "spouse_ids": ["r"]}),
        ("k", {"name": "Kannan", "sex": "male", "parent_ids": ["r", "l"], "lives_in_village": False}),
        ("s", {"name": "Selvi", "sex": "female", "parent_ids": ["r", "l"]}),
    ]:
        body = {**body, "parent_ids": [ids[x] for x in body.get("parent_ids", [])], "spouse_ids": [ids[x] for x in body.get("spouse_ids", [])]}
        ids[key] = admin.post(f"/api/v1/cases/{cid}/land/members", json=body).json()["member"]["id"]
    return ids


def gates(admin, cid):
    return {g["stage"]: g for g in admin.get(f"/api/v1/cases/{cid}").json()["checklist"]}


def test_land_lifecycle_gates_move_with_the_records(app):
    admin, cid = land_case(app)
    g = gates(admin, cid)
    assert g["intake"]["done"] is False and "0 in the family tree" in g["intake"]["detail"]
    ids = tree(admin, cid)
    assert gates(admin, cid)["intake"]["done"] is True
    assert admin.post(f"/api/v1/cases/{cid}/stage", json={"to_stage": "records"}).status_code == 200
    assert admin.post(f"/api/v1/cases/{cid}/stage", json={"to_stage": "reconciled"}).status_code == 409  # records missing
    for p in sorted(LAND.glob("*.txt")):
        upload(admin, cid, p.name, p.read_bytes())
    admin.post(f"/api/v1/cases/{cid}/land/records/derive")
    assert gates(admin, cid)["records"]["done"] is True
    admin.post(f"/api/v1/cases/{cid}/land/analyses/concordance")
    rec = gates(admin, cid)["reconciled"]
    assert rec["done"] is False and "still open" in rec["detail"]
    # Each difference is sent to its office: acknowledging the findings is what clears the gate.
    for f in admin.get(f"/api/v1/cases/{cid}/risks").json()["findings"]:
        if f["finding_type"] in ("land_name_mismatch", "land_extent_mismatch", "land_survey_mismatch", "land_boundary_conflict") and f["status"] == "open":
            assert admin.patch(f"/api/v1/risks/{f['id']}", json={"status": "acknowledged", "note": "re-survey requested"}).status_code == 200
    assert gates(admin, cid)["reconciled"]["done"] is True
    admin.post(f"/api/v1/cases/{cid}/land/analyses/succession")
    g = gates(admin, cid)
    assert g["heirs"]["done"] is True and g["consent"]["done"] is False and "0 signed" in g["consent"]["detail"]
    assert g["mutation"]["done"] is False and g["mutation"]["detail"] == "mutation not filed"

    # Two-level consent: an online "yes" is intent; the gate needs the signed letter.
    token = admin.post(f"/api/v1/land/members/{ids['k']}/consent-link").json()
    assert "Keezhaiyur" in token["message"] and token["language"] == "ta"
    reply = app_client(app).post(f"/api/v1/public/consent/{token['token']}", json={"decision": "consented"}).json()
    assert "sign the consent letter" in reply["next"]
    admin.post(f"/api/v1/cases/{cid}/land/analyses/succession")
    assert "1 agreed online" in gates(admin, cid)["consent"]["detail"]
    ready = admin.post(f"/api/v1/cases/{cid}/land/analyses/readiness", json={"lender": "bank_term_loan"}).json()["analysis"]["result"]
    assert {r["id"]: r["status"] for r in ready["requirements"]}["co_heir_consent"] == "blocked"  # Selvi has not answered
    letter = upload(admin, cid, "consent-kannan.txt", b"I, Kannan, consent ... signed")
    assert admin.post(f"/api/v1/land/members/{ids['k']}/consent-evidence", json={"consent_evidence": "signed_letter"}).status_code == 422
    for key in ("k", "s"):
        doc = letter if key == "k" else upload(admin, cid, "consent-selvi.txt", b"I, Selvi, consent ... signed")
        assert admin.post(f"/api/v1/land/members/{ids[key]}/consent-evidence", json={"consent_evidence": "signed_letter", "document_id": doc["id"]}).status_code == 200
    admin.post(f"/api/v1/cases/{cid}/land/analyses/succession")
    assert gates(admin, cid)["consent"]["done"] is True


def app_client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_proceedings_track_the_office_and_escalate_when_overdue(app):
    admin, cid = land_case(app)
    tree(admin, cid)
    kinds = admin.get(f"/api/v1/cases/{cid}/land/proceedings").json()
    assert kinds["kinds"]["mutation"]["office"] == "Taluk office (Tahsildar)" and kinds["region"]["appellate_office"] == "Revenue Divisional Officer (RDO)"

    lhc = admin.post(f"/api/v1/cases/{cid}/land/proceedings", json={"kind": "legal_heir_certificate", "application_no": "LHC/2025/0412", "filed_on": ago(50)}).json()["proceeding"]
    assert lhc["status"] == "filed" and lhc["due_on"] == ago(20) and lhc["health"]["signal"] == "overdue" and lhc["health"]["overdue_days"] == 20
    pid = lhc["id"]
    assert admin.post(f"/api/v1/land/proceedings/{pid}/events", json={"kind": "order_passed", "event_date": ago(60)}).status_code == 409  # before filing
    assert admin.post(f"/api/v1/land/proceedings/{pid}/events", json={"kind": "note", "event_date": (date.today() + timedelta(days=3)).isoformat()}).status_code == 422
    ev = admin.post(f"/api/v1/land/proceedings/{pid}/events", json={"kind": "field_inspection", "event_date": ago(41), "note": "VAO visited"}).json()["proceeding"]
    assert ev["status"] == "field_verification" and ev["health"]["flow"][2]["reached"] is True

    # The queue and the graph see the overdue file.
    queue = admin.get(f"/api/v1/cases/{cid}/flow").json()
    assert any(i["kind"] == "proceeding" and "past the 30-day timeline" in i["title"] for i in queue["needs_you"])
    nodes = {n["id"]: n for n in queue["nodes"]}
    assert nodes["proceedings"]["stats"]["overdue"] == 1 and nodes["proceedings"]["status"] == "attention"

    esc = admin.post(f"/api/v1/land/proceedings/{pid}/escalate").json()
    assert esc["authority"] == "Revenue Divisional Officer (RDO)" and "LHC/2025/0412" in esc["letter"] and "VAO visited" in esc["letter"]
    assert esc["escalation"]["kind"] == "appeal" and esc["escalation"]["parent_id"] == pid and esc["level"] == 1
    second = admin.post(f"/api/v1/land/proceedings/{pid}/escalate").json()
    assert second["authority"] == "District Collector"

    # Office asks for a paper → the file waits on the family, not the office.
    mut = admin.post(f"/api/v1/cases/{cid}/land/proceedings", json={"kind": "mutation", "filed_on": ago(10)}).json()["proceeding"]
    m = admin.post(f"/api/v1/land/proceedings/{mut['id']}/events", json={"kind": "document_requested", "event_date": ago(8), "note": "EC for 13 years"}).json()["proceeding"]
    assert m["health"]["waiting_on"] == "family"
    m = admin.post(f"/api/v1/land/proceedings/{mut['id']}/events", json={"kind": "notice_published", "event_date": ago(5)}).json()["proceeding"]
    assert m["status"] == "notice_period" and m["objection_until"] == (date.today() + timedelta(days=10)).isoformat()
    obj = admin.post(f"/api/v1/land/proceedings/{mut['id']}/events", json={"kind": "objection_received", "event_date": ago(2), "note": "S. Murugan claims an agreement of sale"}).json()
    assert obj["proceeding"]["status"] == "hearing" and "District Legal Services Authority" in obj["next"]
    assert any(i["kind"] == "objection" and i["severity"] == "critical" for i in admin.get(f"/api/v1/cases/{cid}/flow").json()["needs_you"])
    done = admin.post(f"/api/v1/land/proceedings/{mut['id']}/events", json={"kind": "order_passed", "event_date": ago(0), "note": "Patta transferred to the legal heirs"}).json()
    assert done["proceeding"]["status"] == "ordered" and "Upload the updated record" in done["next"]
    assert gates(admin, cid)["mutation"]["done"] is True
    assert admin.post(f"/api/v1/land/proceedings/{mut['id']}/events", json={"kind": "field_inspection", "event_date": ago(0)}).status_code == 409
    assert admin.delete(f"/api/v1/land/proceedings/{mut['id']}").status_code == 409
    check = admin.get(f"/api/v1/land/proceedings/{mut['id']}/filing-check").json()
    assert any("Signed consent" in i["item"] for i in check["items"])


def test_pathways_compare_joint_release_partition_and_bridge(app):
    admin, cid = land_case(app)
    for p in sorted(LAND.glob("*.txt")):
        upload(admin, cid, p.name, p.read_bytes())
    admin.post(f"/api/v1/cases/{cid}/land/records/derive")
    tree(admin, cid)
    early = admin.post(f"/api/v1/cases/{cid}/land/pathways").json()["analysis"]["result"]
    assert early["status"] in ("compared", "insufficient")
    res = admin.post(f"/api/v1/cases/{cid}/land/pathways", json={"lender": "bank_term_loan"}).json()["analysis"]["result"]
    assert res["status"] == "compared"
    routes = {r["id"]: r for r in res["routes"]}
    assert set(routes) == {"joint", "release", "partition", "bridge"}
    # Kannan farms the land (adangal), so the release route names him as the one heir who keeps it.
    assert res["beneficiary"]["name"] == "Kannan" and "cultivates" in res["beneficiary"]["why"]
    rel = routes["release"]
    assert rel["value_transfers"][0]["from"] == "Selvi" and rel["duty_inr"] > 0 and rel["fair_settlement_inr"] > 0
    assert routes["joint"]["duty_inr"] == 0 and routes["joint"]["cost_inr"] < rel["cost_inr"]
    assert routes["partition"]["days"] > routes["joint"]["days"] and len(routes["partition"]["per_heir"]) == 2
    assert routes["bridge"]["capacity_inr"] == 200_000 and routes["bridge"]["days"] < routes["joint"]["days"]
    assert sum(1 for r in res["routes"] if r.get("recommended")) == 1 and "family decides" in res["disclaimer"]
    for r in res["routes"]:
        crit = [s for s in r["steps"] if s["critical"]]
        assert crit and max(s["finish_day"] for s in r["steps"]) == r["days"]


def test_agent_drafts_applications_and_compares_pathways(app):
    admin, cid = land_case(app)
    for p in sorted(LAND.glob("*.txt")):
        upload(admin, cid, p.name, p.read_bytes())
    tree(admin, cid)
    run = admin.post(f"/api/v1/cases/{cid}/land/agent", json={"lender": "bank_term_loan"}).json()["run"]
    tools = [s["tool"] for s in run["steps"]]
    assert "proceedings" in tools and "pathways" in tools
    files = admin.get(f"/api/v1/cases/{cid}/land/proceedings").json()["proceedings"]
    assert {f["kind"] for f in files} == {"legal_heir_certificate", "mutation"} and all(f["status"] == "draft" for f in files)
    assert any(h["what"].startswith("File the legal heir certificate") for h in run["handoffs"])
    again = admin.post(f"/api/v1/cases/{cid}/land/agent", json={"lender": "bank_term_loan"}).json()["run"]
    assert len(admin.get(f"/api/v1/cases/{cid}/land/proceedings").json()["proceedings"]) == 2  # not drafted twice
    assert again["steps"]
    journey = admin.get(f"/api/v1/cases/{cid}/land/journey").json()
    assert journey["stages"][5]["key"] == "mutation" and journey["next_action"] and len(journey["proceedings"]) == 2


def test_land_flow_graph_and_org_flow(app):
    admin, cid = land_case(app)
    flow = admin.get(f"/api/v1/cases/{cid}/flow").json()
    ids = {n["id"] for n in flow["nodes"]}
    assert {"records", "concordance", "shares", "consents", "proceedings", "mutation", "readiness"} <= ids
    assert flow["critical_path"][-1] == "resolution" and flow["next_action"]["node"] in ("documents", "family", "records")
    assert any(e["alt"] and e["from"] == "consents" and e["to"] == "readiness" for e in flow["edges"])
    org = admin.get("/api/v1/flow").json()
    assert any(c["id"] == cid and c.get("kind") == "rural_land" for c in org["cases"])
    overview = admin.get("/api/v1/overview").json()
    assert overview["cases"]["by_stage"].get("intake", 0) >= 1 and overview["land_stages"][1]["key"] == "records"
