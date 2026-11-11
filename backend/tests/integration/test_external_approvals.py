"""External approvals against an in-process FAKE ledger (failure modes that a live network cannot be made to produce on demand).

The fake speaks the same JSON Ledger API v2 shapes as Canton 3.4 and enforces the parts Groundwork
relies on: per-participant tokens, signatory/controller authorisation, consuming choices, commitment
checks, command deduplication, offsets and resets. The real-network proof is tests/canton_live.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime

import httpx
import jwt
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domain import external_approvals as ext
from app.infrastructure.canton import participants
from app.infrastructure.canton.auth import HmacTokenProvider
from app.infrastructure.canton.client import LedgerClient
from app.infrastructure.database.models import ExternalApproval, ExternalApprovalEvent, User
from app.infrastructure.database.session import session_factory
from tests.helpers import add_member, sign_up, upload

LA, RG = "LegalAidParty::aa", "RegistryParty::bb"
SECRETS = {"legal_aid": "la-secret-0123456789abcdef0123456789", "registry": "rg-secret-0123456789abcdef0123456789"}
PARTY = {"legal_aid": LA, "registry": RG}
DECIDE = {"ApproveOwnership": "Approved", "RejectOwnership": "Rejected", "RequestAdditionalEvidence": "ChangesRequested"}


class FakeLedger:
    def __init__(self):
        self.reset()
        self.down = {"legal_aid": False, "registry": False}
        self.lose_response_once = False

    def reset(self):
        self.contracts: dict[str, dict] = {}
        self.txs: list[dict] = []
        self.commands: set[str] = set()
        self.ids = itertools.count(1)

    def end(self) -> int:
        return self.txs[-1]["offset"] if self.txs else 0

    def visible(self, c: dict, party: str) -> bool:
        return party in c["signatories"] or party in c["observers"]

    def handler(self, role: str):
        def handle(request: httpx.Request) -> httpx.Response:
            if self.down[role]:
                raise httpx.ConnectError("participant down", request=request)
            try:
                claims = jwt.decode(request.headers.get("authorization", "")[7:], SECRETS[role], algorithms=["HS256"])
            except jwt.PyJWTError:
                return httpx.Response(401, json={"code": "UNAUTHENTICATED", "cause": "bad token", "correlationId": "x"})
            party = PARTY[role]
            path = request.url.path
            if path == "/v2/state/ledger-end":
                return httpx.Response(200, json={"offset": self.end()})
            body = json.loads(request.content or b"{}")
            if path == "/v2/state/active-contracts":
                return httpx.Response(200, json=[{"workflowId": "", "contractEntry": {"JsActiveContract": {"createdEvent": self.ev(c), "synchronizerId": "sync::1", "reassignmentCounter": 0}}}
                                                 for c in self.contracts.values() if c["active"] and self.visible(c, party)])
            if path == "/v2/updates":
                out = []
                for tx in self.txs:
                    if body["beginExclusive"] < tx["offset"] <= body["endInclusive"]:
                        evs = [e for e in tx["events"] if party in e["_witnesses"]]
                        if evs:
                            out.append({"update": {"Transaction": {"value": {**tx, "events": [{k: {kk: vv for kk, vv in v.items() if kk != "_witnesses"}} for e in evs for k, v in [(e["kind"], e["body"] | {"_witnesses": None})]]}}}})
                return httpx.Response(200, json=out)
            if path == "/v2/commands/submit-and-wait-for-transaction":
                return self.submit(body["commands"], party, claims)
            return httpx.Response(404, json={"code": "NOT_FOUND", "cause": path})
        return handle

    def ev(self, c):
        return {"contractId": c["cid"], "templateId": "pkg1:Groundwork.Approval.OwnershipApproval:" + c["entity"], "createArgument": c["args"], "signatories": c["signatories"], "observers": c["observers"], "offset": c["offset"]}

    def create(self, entity, args, signatories, observers, offset):
        cid = f"00cid{next(self.ids)}"
        self.contracts[cid] = {"cid": cid, "entity": entity, "args": args, "signatories": signatories, "observers": observers, "active": True, "offset": offset}
        return {"kind": "CreatedEvent", "body": self.ev(self.contracts[cid]), "_witnesses": signatories + observers}

    def submit(self, cmds, party, claims):
        if party not in cmds["actAs"]:
            return httpx.Response(403, json={"code": "PERMISSION_DENIED", "cause": "actAs", "correlationId": "x"})
        if cmds["commandId"] in self.commands:
            return httpx.Response(409, json={"code": "DUPLICATE_COMMAND", "cause": "dup", "correlationId": "x"})
        offset = self.end() + 1
        cmd = cmds["commands"][0]
        events = []
        if "CreateCommand" in cmd:
            args = cmd["CreateCommand"]["createArguments"]
            if args["legalAid"] != party:
                return httpx.Response(400, json={"code": "DAML_AUTHORIZATION_ERROR", "cause": "missing authorization", "correlationId": "x"})
            events.append(self.create("OwnershipApprovalRequest", args, [args["legalAid"]], [args["registry"]], offset))
        else:
            ex = cmd["ExerciseCommand"]
            c = self.contracts.get(ex["contractId"])
            if c is None or not c["active"]:
                return httpx.Response(404, json={"code": "CONTRACT_NOT_FOUND", "cause": "inactive", "correlationId": "x"})
            a, arg, choice = c["args"], ex["choiceArgument"], ex["choice"]
            if choice in DECIDE:
                if party != a["registry"]:
                    return httpx.Response(400, json={"code": "DAML_AUTHORIZATION_ERROR", "cause": "missing authorization", "correlationId": "x"})
                if arg["reviewedEvidenceCommitment"] != a["evidenceCommitment"]:
                    return httpx.Response(400, json={"code": "UNHANDLED_EXCEPTION", "cause": "the reviewed evidence is not the evidence that was proposed"})
                dec = {k: a[k] for k in ("proposalId", "caseReference", "legalAid", "registry", "actionType", "evidenceCommitment", "payloadCommitment", "caseVersion")}
                dec |= {"decisionStatus": DECIDE[choice], "decisionReasonCode": arg["reasonCode"], "requestedEvidence": arg.get("requested", []), "decisionTimestamp": datetime.now(tz=UTC).isoformat()}
                events.append(self.create("OwnershipApprovalDecision", dec, [a["registry"]], [a["legalAid"]], offset))
            elif choice in ("CancelRequest", "MarkExpired"):
                if choice == "CancelRequest" and party != a["legalAid"]:
                    return httpx.Response(400, json={"code": "DAML_AUTHORIZATION_ERROR", "cause": "missing authorization"})
                closed = {k: a[k] for k in ("proposalId", "caseReference", "legalAid", "registry", "caseVersion", "evidenceCommitment", "payloadCommitment")}
                closed |= {"closure": "cancelled" if choice == "CancelRequest" else "expired", "reasonCode": "OtherSeeOffLedgerNote", "closedAt": datetime.now(tz=UTC).isoformat()}
                events.append(self.create("OwnershipApprovalClosed", closed, [a["legalAid"]], [a["registry"]], offset))
            c["active"] = False
            events.insert(0, {"kind": "ArchivedEvent", "body": {"contractId": c["cid"], "templateId": "pkg1:Groundwork.Approval.OwnershipApproval:" + c["entity"], "offset": offset}, "_witnesses": c["signatories"] + c["observers"]})
        self.commands.add(cmds["commandId"])
        tx = {"updateId": f"upd{offset}", "commandId": cmds["commandId"], "offset": offset, "synchronizerId": "sync::1", "recordTime": datetime.now(tz=UTC).isoformat(), "events": events}
        self.txs.append(tx)
        if self.lose_response_once:
            self.lose_response_once = False
            raise httpx.ReadTimeout("response lost after commit")
        party_events = [{e["kind"]: e["body"]} for e in events if party in e["_witnesses"]]
        return httpx.Response(200, json={"transaction": {**tx, "events": party_events}})


@pytest.fixture
def ledger(monkeypatch):
    fake = FakeLedger()
    env = {"GROUNDWORK_CANTON_ENABLED": "true", "GROUNDWORK_CANTON_NETWORK": "localnet", "CANTON_LEGAL_AID_PARTY_ID": LA, "CANTON_REGISTRY_PARTY_ID": RG,
           "CANTON_LEGAL_AID_LEDGER_URL": "http://legal-aid.test", "CANTON_REGISTRY_LEDGER_URL": "http://registry.test"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    clients = {role: LedgerClient(role, f"http://{role}.test", PARTY[role], f"gw-{role}", HmacTokenProvider(SECRETS[role], f"gw-{role}"),
                                  transport=httpx.MockTransport(fake.handler(role))) for role in participants.ROLES}
    monkeypatch.setattr(participants, "client", lambda role: clients[role])
    run(lambda db: db.query(ext.CantonCursor).delete())  # each test is a fresh ledger with the same party ids
    yield fake, clients
    get_settings.cache_clear()


def run(fn):
    db = session_factory()()
    try:
        out = fn(db)
        db.commit()
        return out
    finally:
        db.close()


def world(app):
    legal, la_admin = sign_up(app)
    attorney, _ = add_member(legal, app, "attorney")
    registry, rg_admin = sign_up(app)
    officer, _ = add_member(registry, app, "attorney")

    def binder(db):
        admin = db.get(User, la_admin["id"])
        ext.bind(db, la_admin["organization_id"], "legal_aid", admin)
        ext.bind(db, rg_admin["organization_id"], "registry", admin)
    run(binder)
    cid = legal.post("/api/v1/cases", json={"title": "CASE-101 Raj Kumar estate"}).json()["case"]["id"]
    upload(legal, cid, "deed.txt", b"WARRANTY DEED\nGrantor: Mohan Lal\nGrantee: Raj Kumar\nParcel No.: 22-104-0009")
    for f in legal.get(f"/api/v1/cases/{cid}/facts").json()["facts"]:
        legal.patch(f"/api/v1/facts/{f['id']}", json={"status": "confirmed"})
    return legal, attorney, officer, cid


def submit(legal, attorney, cid):
    a = legal.post(f"/api/v1/cases/{cid}/external-approvals", json={"action_type": "ConfirmSuccession"})
    assert a.status_code == 201, a.text
    act = next(x for x in legal.get(f"/api/v1/cases/{cid}/workflow").json()["actions"] if x["action"] == "external.submit_to_registry" and x["status"] == "proposed")
    assert attorney.post(f"/api/v1/approvals/{act['id']}/approve", json={"confirmation": f"approve action {act['seq']}"}).status_code == 200
    return a.json()["approval"]["id"]


def status(legal, aid):
    return legal.get(f"/api/v1/external-approvals/{aid}").json()["approval"]


def test_disabled_canton_leaves_groundwork_untouched(app):
    legal, _ = sign_up(app)
    cid = legal.post("/api/v1/cases", json={"title": "No ledger here"}).json()["case"]["id"]
    assert legal.post(f"/api/v1/cases/{cid}/external-approvals", json={}).status_code == 409
    assert legal.get("/api/v1/canton/status").json()["enabled"] is False


def test_full_lifecycle_projection_and_replay(app, ledger):
    fake, _ = ledger
    legal, attorney, officer, cid = world(app)
    aid = submit(legal, attorney, cid)
    assert status(legal, aid)["status"] == "submitted"
    assert legal.post(f"/api/v1/cases/{cid}/external-approvals", json={}).status_code == 409  # one in flight per case
    run(ext.tick)
    assert status(legal, aid)["status"] == "awaiting_review"
    assert officer.post(f"/api/v1/registry/external-approvals/{aid}/approve", json={}).status_code == 200
    assert status(legal, aid)["status"] == "decision_submitted"  # not approved until Legal Aid's stream shows it
    run(ext.tick)
    done = status(legal, aid)
    assert done["status"] == "approved" and done["ledger"]["decision_update_id"].startswith("upd")
    # replay: rewind both cursors and re-read everything — nothing is applied twice
    before = run(lambda db: db.query(ExternalApprovalEvent).filter_by(approval_id=aid).count())
    run(lambda db: [setattr(c, "ledger_offset", 0) for c in db.query(ext.CantonCursor).all()])
    run(ext.tick)
    assert run(lambda db: db.query(ExternalApprovalEvent).filter_by(approval_id=aid).count()) == before
    assert len([d for d in legal.get(f"/api/v1/cases/{cid}/workflow").json()["decisions"] if "approved" in d["title"]]) == 1


def test_outage_is_never_a_decision_and_recovers(app, ledger):
    fake, _ = ledger
    legal, attorney, officer, cid = world(app)
    fake.down["legal_aid"] = True
    aid = submit(legal, attorney, cid)
    s = status(legal, aid)
    assert s["status"] == "pending_submission" and "retrying" in s["error"]
    run(lambda db: [setattr(o, "next_attempt_at", datetime(2000, 1, 1, tzinfo=UTC)) for o in db.query(ext.CantonOutbox).all()])
    fake.down["legal_aid"] = False
    run(ext.tick)
    assert status(legal, aid)["status"] == "awaiting_review"
    fake.down["legal_aid"] = True
    assert officer.post(f"/api/v1/registry/external-approvals/{aid}/approve", json={}).status_code == 200
    run(ext.tick)  # Legal Aid participant down: the decision exists on the ledger but is not projected yet
    s = status(legal, aid)
    assert s["status"] == "decision_submitted" and "legal_aid participant" in s["error"]
    fake.down["legal_aid"] = False
    run(ext.tick)
    assert status(legal, aid)["status"] == "approved"


def test_lost_response_is_deduplicated(app, ledger):
    fake, _ = ledger
    legal, attorney, _officer, cid = world(app)
    fake.lose_response_once = True  # the ledger commits, the HTTP answer is lost
    aid = submit(legal, attorney, cid)
    assert status(legal, aid)["status"] == "pending_submission"
    run(lambda db: [setattr(o, "next_attempt_at", datetime(2000, 1, 1, tzinfo=UTC)) for o in db.query(ext.CantonOutbox).all()])
    run(ext.tick)  # resubmission → DUPLICATE_COMMAND → done; the update stream supplies the contract
    s = status(legal, aid)
    assert s["status"] == "awaiting_review" and s["ledger"]["request_contract_id"]
    assert sum(1 for c in fake.contracts.values() if c["entity"] == "OwnershipApprovalRequest") == 1


def test_auth_failure_is_terminal_and_sanitised(app, ledger, monkeypatch):
    fake, clients = ledger
    legal, attorney, _officer, cid = world(app)
    clients["legal_aid"].tokens = HmacTokenProvider("the-wrong-secret-0000000000000000", "gw-legal_aid")
    aid = submit(legal, attorney, cid)
    s = status(legal, aid)
    assert s["status"] == "submission_failed" and s["error"].startswith("auth") and "secret" not in s["error"]


def test_reset_and_stale_contracts_never_approve(app, ledger):
    fake, _ = ledger
    legal, attorney, officer, cid = world(app)
    aid = submit(legal, attorney, cid)
    run(ext.tick)
    fake.reset()  # DevNet/TestNet reset: the ledger forgets everything
    assert officer.post(f"/api/v1/registry/external-approvals/{aid}/approve", json={}).status_code == 200
    s = status(legal, aid)
    assert s["status"] == "sync_delayed" and "no longer active" in s["error"]
    run(ext.tick)
    assert status(legal, aid)["status"] == "sync_delayed"


def test_reviewer_must_match_the_commitment_and_tenant_isolation(app, ledger):
    fake, _ = ledger
    legal, attorney, officer, cid = world(app)
    aid = submit(legal, attorney, cid)
    run(ext.tick)
    stranger, _ = sign_up(app)
    assert stranger.get(f"/api/v1/external-approvals/{aid}").status_code == 404
    assert stranger.get("/api/v1/registry/external-approvals/pending").status_code == 404
    pend = officer.get("/api/v1/registry/external-approvals/pending").json()["approvals"]
    assert "case_id" not in pend[0] and pend[0]["manifest"]["documents"]
    run(lambda db: setattr(db.scalar(select(ExternalApproval).where(ExternalApproval.id == aid)), "manifest", {"documents": [], "payload": {}}))
    r = officer.post(f"/api/v1/registry/external-approvals/{aid}/approve", json={})
    assert r.status_code == 409 and "commitment" in r.json()["error"]
    assert officer.post(f"/api/v1/registry/external-approvals/{aid}/request-changes", json={"reason_code": "MissingDocument", "requested": ["LegalHeirCertificate"]}).status_code == 200
    run(ext.tick)
    s = status(legal, aid)
    assert s["status"] == "changes_requested" and s["requested_evidence"] == ["LegalHeirCertificate"]
    assert any("more evidence" in t["title"] for t in legal.get(f"/api/v1/cases/{cid}/workflow").json()["tasks"])


def test_cancel_by_legal_aid(app, ledger):
    legal, attorney, officer, cid = world(app)
    aid = submit(legal, attorney, cid)
    paralegal, _ = add_member(legal, app, "paralegal")
    assert paralegal.post(f"/api/v1/external-approvals/{aid}/cancel").status_code == 403  # cancelling needs an approver role
    assert attorney.post(f"/api/v1/external-approvals/{aid}/cancel").status_code == 200
    run(ext.tick)
    assert status(legal, aid)["status"] == "cancelled"
    assert officer.post(f"/api/v1/registry/external-approvals/{aid}/approve", json={}).status_code == 409
