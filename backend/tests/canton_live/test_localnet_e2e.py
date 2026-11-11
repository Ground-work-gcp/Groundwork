"""LIVE: the full multi-party approval through Groundwork's API against a running two-participant LocalNet.

Not a mock. Skipped unless infra/canton/localnet/up.sh has been run (it writes localnet.env and
state/localnet.json). Every ledger reference asserted here comes from the real participants and is
written to infra/canton/localnet/state/last-e2e.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

LOCALNET = Path(__file__).resolve().parents[3] / "infra" / "canton" / "localnet"
ENV, STATE = LOCALNET / "localnet.env", LOCALNET / "state" / "localnet.json"
pytestmark = pytest.mark.skipif(not (ENV.exists() and STATE.exists() and os.environ.get("GROUNDWORK_CANTON_LIVE") == "1"), reason="set GROUNDWORK_CANTON_LIVE=1 with LocalNet running")

DOCS = {
    "case101-deed-1998.txt": "WARRANTY DEED\nGrantor: Mohan Lal\nGrantee: Raj Kumar\nParcel No.: 22-104-0009\nRecorded: 03/14/1998\nProperty address: 12 Banyan Lane",
    "case101-death-certificate-raj.txt": "CERTIFICATE OF DEATH\nDecedent: Raj Kumar\nDate of death: 08/02/2021\nParcel No.: 22-104-0009",
    "case101-inheritance-order.txt": "LETTERS OF ADMINISTRATION\nDecedent: Raj Kumar\nHeir: Priya Kumar\nDate: 01/10/2022\nParcel No.: 22-104-0009",
}


@pytest.fixture
def canton(monkeypatch):
    env = dict(line.strip().split("=", 1) for line in ENV.read_text().splitlines() if "=" in line)
    st = json.loads(STATE.read_text())
    values = {
        "GROUNDWORK_CANTON_ENABLED": "true", "GROUNDWORK_CANTON_NETWORK": "localnet", "CANTON_BACKGROUND_SYNC": "false",
        "CANTON_LEGAL_AID_LEDGER_URL": "http://127.0.0.1:7011", "CANTON_LEGAL_AID_PARTY_ID": st["legal_aid_party"], "CANTON_LEGAL_AID_SECRET_REF": env["LEGALAID_JWT_SECRET"],
        "CANTON_REGISTRY_LEDGER_URL": "http://127.0.0.1:7021", "CANTON_REGISTRY_PARTY_ID": st["registry_party"], "CANTON_REGISTRY_SECRET_REF": env["REGISTRY_JWT_SECRET"],
        "CANTON_SYNCHRONIZER_ID": st["synchronizer_id"],
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    from app.core.config import get_settings
    from app.infrastructure.canton import participants

    get_settings.cache_clear()
    participants.reset()
    yield st
    for k in values:
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    participants.reset()


def test_case_101_multi_party_approval_on_localnet(app, canton):
    from app.domain import external_approvals as ext
    from app.infrastructure.canton import participants
    from app.infrastructure.canton.errors import CantonError
    from app.infrastructure.database.models import ExternalApproval, User
    from app.infrastructure.database.session import session_factory
    from tests.helpers import add_member, sign_up, upload

    def session_do(fn):
        db = session_factory()()
        try:
            result = fn(db)
            db.commit()
            return result
        finally:
            db.close()

    def tick():
        return session_do(ext.tick)

    # Two organisations, each a Groundwork tenant; a platform admin binds them to their parties.
    legal, legal_admin = sign_up(app, "Groundwork Legal Aid")
    attorney, _ = add_member(legal, app, "attorney", "Asha Attorney")
    registry, reg_admin = sign_up(app, "Registry Authority")
    officer, _ = add_member(registry, app, "attorney", "Ravi Registrar")
    session_do(lambda db: setattr(db.get(User, legal_admin["id"]), "is_platform_admin", True))
    assert legal.post("/api/v1/admin/canton/bindings", json={"organization_id": legal_admin["organization_id"], "role": "legal_aid"}).status_code == 200
    assert legal.post("/api/v1/admin/canton/bindings", json={"organization_id": reg_admin["organization_id"], "role": "registry"}).status_code == 200
    st = legal.get("/api/v1/canton/status").json()
    assert st["participants"]["legal_aid"]["ok"] and st["participants"]["registry"]["ok"] and st["role"] == "legal_aid"

    # CASE-101: Raj died; Priya is the proposed successor. Evidence uploaded and reviewed by a person.
    case = legal.post("/api/v1/cases", json={"title": "CASE-101 — Raj Kumar estate, 12 Banyan Lane", "property": {"parcel_number": "22-104-0009"},
                                             "parties": [{"name": "Priya Kumar", "roles": ["client", "heir"]}]}).json()["case"]
    cid = case["id"]
    for name, text in DOCS.items():
        upload(legal, cid, name, text.encode())
    for f in legal.get(f"/api/v1/cases/{cid}/facts").json()["facts"]:
        assert legal.patch(f"/api/v1/facts/{f['id']}", json={"status": "confirmed"}).status_code == 200

    # Proposal → internal two-person approval → committed on the Legal Aid participant.
    prop = legal.post(f"/api/v1/cases/{cid}/external-approvals", json={"action_type": "ConfirmSuccession", "rationale": "Priya is the sole heir"})
    assert prop.status_code == 201, prop.text
    approval = prop.json()["approval"]
    assert approval["status"] == "draft" and approval["commitment_verified"]
    action = next(a for a in legal.get(f"/api/v1/cases/{cid}/workflow").json()["actions"] if a["action"] == "external.submit_to_registry")
    assert legal.post(f"/api/v1/approvals/{action['id']}/approve", json={"confirmation": f"approve action {action['seq']}"}).status_code in (403, 409)  # proposer cannot self-approve
    ok = attorney.post(f"/api/v1/approvals/{action['id']}/approve", json={"confirmation": f"approve action {action['seq']}"})
    assert ok.status_code == 200, ok.text
    a = legal.get(f"/api/v1/external-approvals/{approval['id']}").json()["approval"]
    assert a["status"] == "submitted" and a["ledger"]["request_contract_id"] and a["ledger"]["request_update_id"], a

    # The registry participant receives it through its own update stream.
    tick()
    pending = officer.get("/api/v1/registry/external-approvals/pending").json()["approvals"]
    mine = next(p for p in pending if p["id"] == approval["id"])
    assert mine["status"] == "awaiting_review" and "case_id" not in mine and mine["salts"]["evidence"]
    assert legal.get("/api/v1/registry/external-approvals/pending").status_code == 404  # legal aid has no registry portal

    # An unauthorised party cannot approve: Groundwork refuses, and so does the ledger itself.
    assert attorney.post(f"/api/v1/registry/external-approvals/{approval['id']}/approve", json={}).status_code == 404
    row = session_do(lambda db: db.scalar(select(ExternalApproval).where(ExternalApproval.id == approval["id"])))
    tmpl = participants.template_id("OwnershipApprovalRequest")
    with pytest.raises(CantonError) as forged:
        participants.client("legal_aid").exercise(tmpl, row.request_contract_id, "ApproveOwnership",
                                                  {"reviewedEvidenceCommitment": row.evidence_commitment, "reviewedPayloadCommitment": row.payload_commitment, "reasonCode": "EvidenceSufficient"},
                                                  "forged-" + row.proposal_id)
    assert forged.value.kind in ("rejected", "auth")

    # The registry officer approves through the Registry participant; Legal Aid learns it from its own stream.
    dec = officer.post(f"/api/v1/registry/external-approvals/{approval['id']}/approve", json={"reason_code": "EvidenceSufficient"})
    assert dec.status_code == 200, dec.text
    assert dec.json()["approval"]["status"] == "decision_submitted"
    assert legal.get(f"/api/v1/external-approvals/{approval['id']}").json()["approval"]["status"] == "decision_submitted"  # not yet seen by Legal Aid
    tick()
    done = legal.get(f"/api/v1/external-approvals/{approval['id']}").json()["approval"]
    assert done["status"] == "approved" and done["ledger"]["decision_update_id"] and done["ledger"]["decision_contract_id"], done
    # Legal Aid can independently read the decision contract from its own participant.
    acs = participants.client("legal_aid").active_contracts([participants.template_id("OwnershipApprovalDecision")])
    on_ledger = next(e for e in acs if e["contractId"] == done["ledger"]["decision_contract_id"])
    assert on_ledger["createArgument"]["decisionStatus"] == "Approved" and on_ledger["createArgument"]["evidenceCommitment"] == done["evidence_commitment"]
    assert on_ledger["signatories"] == [canton["registry_party"]]

    # A second decision on the consumed request fails — in Groundwork and on the ledger.
    assert officer.post(f"/api/v1/registry/external-approvals/{approval['id']}/reject", json={"reason_code": "ConflictingClaim"}).status_code == 409
    with pytest.raises(CantonError) as twice:
        participants.client("registry").exercise(tmpl, row.request_contract_id, "RejectOwnership",
                                                 {"reviewedEvidenceCommitment": row.evidence_commitment, "reviewedPayloadCommitment": row.payload_commitment, "reasonCode": "ConflictingClaim"},
                                                 "second-" + row.proposal_id)
    assert twice.value.kind in ("stale_contract", "rejected")

    # The case and its audit chain reflect the external decision with its ledger reference.
    decisions = legal.get(f"/api/v1/cases/{cid}/workflow").json()["decisions"]
    assert any("Registry Authority approved ConfirmSuccession" in d["title"] for d in decisions)
    timeline = legal.get(f"/api/v1/cases/{cid}/timeline").json()["events"]
    approved_evt = next(e for e in timeline if e["event"] == "external_approval.approved")
    assert approved_evt["detail"]["update_id"] == done["ledger"]["decision_update_id"] and approved_evt["actor_kind"] == "system"
    assert legal.get("/api/v1/audit/verify").json()["ok"] and registry.get("/api/v1/audit/verify").json()["ok"]

    # A different proposal, rejected by the registry, reaches Legal Aid as a rejection.
    prop2 = legal.post(f"/api/v1/cases/{cid}/external-approvals", json={"action_type": "RecordMutation"}).json()["approval"]
    act2 = next(a for a in legal.get(f"/api/v1/cases/{cid}/workflow").json()["actions"] if a["action"] == "external.submit_to_registry" and a["status"] == "proposed")
    attorney.post(f"/api/v1/approvals/{act2['id']}/approve", json={"confirmation": f"approve action {act2['seq']}"})
    tick()
    assert officer.post(f"/api/v1/registry/external-approvals/{prop2['id']}/reject", json={"reason_code": "ConflictingClaim"}).status_code == 200
    tick()
    rej = legal.get(f"/api/v1/external-approvals/{prop2['id']}").json()["approval"]
    assert rej["status"] == "rejected" and rej["decision_reason_code"] == "ConflictingClaim"
    hist = legal.get(f"/api/v1/external-approvals/{approval['id']}/events").json()["events"]
    assert [e["kind"] for e in hist][:2] == ["proposed", "queued_for_submission"] and any(e["kind"] == "decision_approved" for e in hist)

    (LOCALNET / "state" / "last-e2e.json").write_text(json.dumps({
        "network": "localnet", "synchronizer_id": canton["synchronizer_id"], "legal_aid_party": canton["legal_aid_party"], "registry_party": canton["registry_party"],
        "approved": {"proposal_id": done["proposal_id"], **done["ledger"], "evidence_commitment": done["evidence_commitment"]},
        "rejected": {"proposal_id": rej["proposal_id"], **rej["ledger"]},
        "forged_approval_refused": forged.value.safe(), "second_decision_refused": twice.value.safe(),
    }, indent=2))
