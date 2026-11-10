"""The whole property-resolution lifecycle through the HTTP API, stage by stage.

A paralegal and an attorney work one synthetic heirs'-property case from intake to a
verified, resolved history. Every assertion is about behaviour a user can see.
"""

from __future__ import annotations

from tests.helpers import DOCS, add_member, new_case, sign_up, upload

DOCUMENTS = [
    "01-warranty-deed-1978.txt",
    "02-death-certificate-ernest-parker.txt",
    "03-will-lula-parker.txt",
    "04-quitclaim-deed-2016.txt",
    "05-tax-bill-2023.txt",
    "06-tax-bill-2024.txt",
    "07-water-lien-2019.txt",
]


def test_property_resolution_case_lifecycle(app):
    admin, _ = sign_up(app, "Hollins Legal Aid")
    paralegal, _ = add_member(admin, app, "paralegal", "Pat Paralegal")
    attorney, attorney_user = add_member(admin, app, "attorney", "Alex Attorney")

    # ── Stage 1 · intake ──────────────────────────────────────────────────────
    created = new_case(paralegal)
    case_id = created["case"]["id"]
    assert created["case"]["reference"].startswith("GW-") and created["case"]["stage"] == "intake"
    assert created["checklist"][0]["done"] is True

    # ── Stage 2 · evidence ingestion (stored, hashed, extracted with provenance) ─
    documents = {name: upload(paralegal, case_id, name) for name in DOCUMENTS}
    assert all(d["status"] == "extracted" and d["page_count"] == 1 for d in documents.values())
    assert documents["01-warranty-deed-1978.txt"]["doc_type"] == "deed"
    assert documents["02-death-certificate-ernest-parker.txt"]["pii_summary"]["by_type"] == {"US_SOCIAL_SECURITY_NUMBER": 1}
    duplicate = paralegal.post(f"/api/v1/cases/{case_id}/documents", files={"file": ("again.txt", (DOCS / DOCUMENTS[0]).read_bytes(), "text/plain")})
    assert duplicate.status_code == 409

    deed_id = documents["01-warranty-deed-1978.txt"]["id"]
    detail = paralegal.get(f"/api/v1/documents/{deed_id}").json()
    grantees = [f for f in detail["facts"] if f["fact_type"] == "grantee"]
    assert {f["normalized_value"] for f in grantees} == {"ERNEST L PARKER", "LULA M PARKER"}
    assert all(f["page_number"] == 1 and f["snippet"] and f["extractor"] == "rules:v1" for f in grantees)
    download = paralegal.get(f"/api/v1/documents/{deed_id}/content")
    assert download.status_code == 200 and download.content == (DOCS / DOCUMENTS[0]).read_bytes()

    # A reviewer corrects a fact; the original extraction is preserved.
    consideration = next(f for f in detail["facts"] if f["fact_type"] == "consideration_amount")
    reviewed = paralegal.patch(f"/api/v1/facts/{consideration['id']}", json={"status": "corrected", "corrected_value": "$9,600.00", "review_note": "per closing statement"}).json()["fact"]
    assert reviewed["value"] == "$9,500.00" and reviewed["effective_value"] == "960000" and reviewed["status"] == "corrected"

    for stage in ("evidence", "ownership"):
        if stage == "ownership":
            # ── Stage 3 · ownership reconstruction ───────────────────────────
            ownership = paralegal.post(f"/api/v1/cases/{case_id}/ownership/reconstruct").json()
            kinds = [e["event_type"] for e in ownership["events"]]
            assert kinds.count("deed_transfer") == 1 and "quitclaim" in kinds and "death" in kinds and "will" in kinds
            assert ownership["current_owners"] == ["Coastal Land Partners LLC"]
            issue_kinds = {i["kind"] for i in ownership["issues"]}
            assert {"broken_chain", "assessed_owner_mismatch", "unprobated_estate", "unprobated_will", "timeline_gap"} <= issue_kinds
            quitclaim = next(e for e in ownership["events"] if e["event_type"] == "quitclaim")
            assert quitclaim["chain_status"] == "break" and quitclaim["document_id"] == documents["04-quitclaim-deed-2016.txt"]["id"]
        moved = paralegal.post(f"/api/v1/cases/{case_id}/stage", json={"to_stage": stage})
        assert moved.status_code == 200, moved.text

    # ── Stage 4 · financial reconciliation ───────────────────────────────────
    derived = paralegal.post(f"/api/v1/cases/{case_id}/obligations/derive").json()["created"]
    assert sorted((o["obligation_type"], o["amount_cents"]) for o in derived) == [("lien", 84217), ("property_tax", 128450), ("property_tax", 131620)]
    assert all(o["fact_ids"] and o["document_id"] for o in derived)
    imported = paralegal.post(
        f"/api/v1/cases/{case_id}/transactions/import",
        files={"file": ("payments.csv", (DOCS / "payments.csv").read_bytes(), "text/csv")},
        data={"date_format": "ISO"},
    ).json()
    assert imported["imported"] == 5 and len(imported["rejections"]) == 1
    assert imported["rejections"][0]["reason"] == "BAD_FIELD" and "date" in imported["rejections"][0]["detail"]

    run = paralegal.post(f"/api/v1/cases/{case_id}/reconciliation/runs").json()
    rules = {r["rule"]: r for r in run["results"]}
    assert rules["T0_REFERENCE_AND_AMOUNT"]["outcome"] == "AUTO_MATCHED"
    assert rules["T2_UNDERPAID"]["amount_cents"] == 21620
    assert rules["T2_ESCALATION_CANDIDATES"]["outcome"] == "PROPOSED"
    assert rules["T1_PAYER_NOT_BILLED_PARTY"]["class"] == "THIRD_PARTY_PAYER"
    assert rules["T4_UNEXPLAINED_PAYMENT"]["outcome"] == "EXCEPTION"
    money = paralegal.get(f"/api/v1/cases/{case_id}/financials").json()["summary"]
    assert money["obligations_total_cents"] == 344287
    assert money["confirmed_paid_cents"] == 128450 + 110000
    assert money["outstanding_cents"] == 21620
    assert money["unverified_cents"] == 84217

    # The review queue: reject the escalated match, then undo it with the inverse command.
    escalation = rules["T2_ESCALATION_CANDIDATES"]
    rejected = paralegal.post(f"/api/v1/reconciliation/runs/{run['run']['id']}/review", json={"type": "Resolve", "ids": [escalation["id"]], "to": "rejected"}).json()
    assert rejected["label"] == "Rejected one match in reconciliation"
    restored = paralegal.post(f"/api/v1/reconciliation/runs/{run['run']['id']}/review", json=rejected["inverse"]).json()
    assert next(r for r in restored["results"] if r["id"] == escalation["id"])["review_status"] == "open"
    assert paralegal.post(f"/api/v1/cases/{case_id}/stage", json={"to_stage": "financial"}).status_code == 200
    assert paralegal.post(f"/api/v1/cases/{case_id}/stage", json={"to_stage": "discrepancies"}).status_code == 200

    # ── Stage 5 · discrepancy detection ──────────────────────────────────────
    detected = paralegal.post(f"/api/v1/cases/{case_id}/risks/detect").json()
    findings = {f["finding_type"]: f for f in detected["findings"]}
    expected = {
        "parcel_id_conflict", "broken_chain_of_title", "owner_name_conflict", "unprobated_estate", "unprobated_will",
        "deceased_owner_of_record", "unreconciled_obligation", "third_party_payer", "unverified_payment_match",
        "unexplained_payment", "evidence_gap",
    }
    assert expected <= set(findings), sorted(findings)
    parcel = findings["parcel_id_conflict"]
    assert parcel["severity"] == "high" and parcel["requires_professional_review"] and parcel["uncertainty"]
    cited = {(ref["filename"], ref["page"]) for ref in parcel["evidence"]}
    assert ("05-tax-bill-2023.txt", 1) in cited and ("01-warranty-deed-1978.txt", 1) in cited
    assert all(ref.get("snippet") for ref in parcel["evidence"])
    again = paralegal.post(f"/api/v1/cases/{case_id}/risks/detect").json()["outcome"]
    assert again["created"] == 0 and again["updated"] == detected["outcome"]["total"]  # idempotent

    # ── Stage 6 · human review ───────────────────────────────────────────────
    assert paralegal.post(f"/api/v1/cases/{case_id}/stage", json={"to_stage": "review"}).status_code == 200
    blocked = paralegal.post(f"/api/v1/cases/{case_id}/stage", json={"to_stage": "resolution"})
    assert blocked.status_code == 409
    serious = [f for f in detected["findings"] if f["severity"] in ("critical", "high")]
    paralegal_resolve = paralegal.patch(f"/api/v1/risks/{serious[0]['id']}", json={"status": "resolved", "note": "done"})
    assert paralegal_resolve.status_code == 403  # critical/high are resolved by an attorney
    for finding in serious:
        reply = paralegal.patch(f"/api/v1/risks/{finding['id']}", json={"status": "acknowledged", "note": "reviewed with supervising attorney"})
        assert reply.status_code == 200

    # Evidence questions are answered from the case's own pages, with citations.
    answer = attorney.post(f"/api/v1/cases/{case_id}/questions", json={"question": "Who is the grantee on the quitclaim deed?"}).json()["question"]
    assert answer["mode"] == "extractive" and answer["citations"]
    assert answer["citations"][0]["filename"] == "04-quitclaim-deed-2016.txt" and answer["citations"][0]["page"] == 1

    # ── Stage 7 · resolution workflow ────────────────────────────────────────
    suggested = paralegal.post(f"/api/v1/cases/{case_id}/actions/suggested").json()["created"]
    assert suggested and all(a["proposed_by_kind"] == "system" and a["status"] == "proposed" for a in suggested)
    probate = next(a for a in suggested if "probate" in a["params"]["title"].lower())
    wrong = attorney.post(f"/api/v1/approvals/{probate['id']}/approve", json={"confirmation": "ok"})
    assert wrong.status_code == 422
    approved = attorney.post(f"/api/v1/approvals/{probate['id']}/approve", json={"confirmation": probate["confirmation_phrase"]}).json()["action"]
    assert approved["status"] == "executed" and approved["decision"]["statement"] == probate["confirmation_phrase"]
    replay = attorney.post(f"/api/v1/approvals/{probate['id']}/approve", json={"confirmation": probate["confirmation_phrase"]})
    assert replay.status_code == 409  # executes once
    for action in suggested:
        if action["id"] != probate["id"]:
            assert attorney.post(f"/api/v1/approvals/{action['id']}/reject", json={"reason": "covered by the probate request"}).status_code == 200

    workflow = paralegal.get(f"/api/v1/cases/{case_id}/workflow").json()
    task = next(t for t in workflow["tasks"] if t["kind"] == "evidence_request")
    assert paralegal.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}).status_code == 200
    paralegal.post(f"/api/v1/cases/{case_id}/comments", json={"body": "Probate petition filed by family's counsel."})
    decision = attorney.post(
        f"/api/v1/cases/{case_id}/decisions",
        json={"title": "Pursue quiet title", "decision": "File quiet-title action after heirship determination", "rationale": "2016 quitclaim was by a non-owner"},
    )
    assert decision.status_code == 201

    to_resolution = paralegal.post(f"/api/v1/cases/{case_id}/actions", json={"action": "case.advance_stage", "params": {"to_stage": "resolution"}, "rationale": "review complete"}).json()["action"]
    self_approve = paralegal.post(f"/api/v1/approvals/{to_resolution['id']}/approve", json={"confirmation": to_resolution["confirmation_phrase"]})
    assert self_approve.status_code == 403
    assert attorney.post(f"/api/v1/approvals/{to_resolution['id']}/approve", json={"confirmation": to_resolution["confirmation_phrase"]}).json()["action"]["status"] == "executed"

    resolve = paralegal.post(f"/api/v1/cases/{case_id}/actions", json={"action": "case.resolve", "params": {"resolution": "Heirship determined; quiet title filed by counsel."}}).json()["action"]
    done = attorney.post(f"/api/v1/approvals/{resolve['id']}/approve", json={"confirmation": resolve["confirmation_phrase"]}).json()["action"]
    assert done["status"] == "executed"

    # ── Stage 8 · verified case history ──────────────────────────────────────
    final = paralegal.get(f"/api/v1/cases/{case_id}").json()
    assert final["case"]["status"] == "resolved" and final["case"]["stage"] == "resolved"
    assert all(g["done"] for g in final["checklist"])
    timeline = paralegal.get(f"/api/v1/cases/{case_id}/timeline").json()["events"]
    events = {e["event"] for e in timeline}
    assert {
        "case.created", "document.uploaded", "document.extracted", "fact.reviewed", "ownership.reconstructed",
        "reconciliation.run", "reconciliation.reviewed", "risks.detected", "finding.reviewed", "action.proposed",
        "action.approved", "action.executed", "action.rejected", "decision.recorded", "case.stage_changed",
    } <= events
    assert any(e["event"] == "action.approved" and e["actor_id"] == attorney_user["id"] for e in timeline)
    assert admin.get("/api/v1/audit/verify").json()["ok"] is True
    report = paralegal.get(f"/api/v1/cases/{case_id}/report").text
    assert "Break in the chain of title" in report and "intact" in report and "not a legal opinion" in report
    csv = admin.get(f"/api/v1/audit/export.csv?case_id={case_id}").text
    assert csv.startswith("seq,occurred_at,event") and "action.executed" in csv
