"""Cross-organisation approvals over Canton: Groundwork proposes, the registry decides, the ledger is authoritative.

Life of an external approval (statuses in ``ExternalApproval.status``):

    draft ──(second Groundwork person approves the internal action)──► pending_submission
      ──(outbox: create through the LEGAL AID participant; committed)──► submitted
      ──(REGISTRY participant's update stream shows the request)──► awaiting_review
      ──(registry user decides; outbox: exercise through the REGISTRY participant)──► decision_submitted
      ──(LEGAL AID participant's update stream shows the decision)──► approved | rejected | changes_requested

Nothing is marked decided because an HTTP call returned: the status changes only when the decision
contract appears in Legal Aid's own committed update stream (or its active contract set on
reconciliation). Commands are written to an outbox in the same database transaction as the state
change, delivered at least once with a deterministic command id (so retries deduplicate), and
classified as retryable or terminal. Each participant's update stream is read from a persisted
offset; a ledger reset (offset ahead of the ledger end) is detected and reconciled from the active
contract set, and approvals whose contracts vanished are marked ``sync_delayed`` — never approved.

Only salted commitments, opaque references and decision codes go on the ledger. The manifest the
commitments are computed from (document hashes, reviewed facts) stays in Groundwork and is shared
only with the reviewing organisation, which recomputes the commitment before it may decide.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.infrastructure.canton import participants
from app.infrastructure.canton.client import archived_events, created_events, entity
from app.infrastructure.canton.errors import CantonError
from app.infrastructure.database.models import (
    CantonCursor,
    CantonOutbox,
    CantonPartyBinding,
    Case,
    Decision,
    EvidenceDocument,
    ExternalApproval,
    ExternalApprovalEvent,
    ExtractedFact,
    Organization,
    Task,
    User,
    utcnow,
)

log = logging.getLogger("groundwork.canton")
ACTION_TYPES = ("ConfirmSuccession", "RecordMutation", "CorrectRecord", "ConfirmOwnership")
REASON_CODES = ("EvidenceSufficient", "EvidenceInsufficient", "CommitmentMismatch", "ConflictingClaim", "JurisdictionMismatch", "MissingDocument", "OtherSeeOffLedgerNote")
EVIDENCE_ITEMS = ("DeathCertificate", "LegalHeirCertificate", "ConsentOfCoHeirs", "EncumbranceCertificate", "SurveySketch", "TitleDeed", "CourtOrder", "IdentityProof")
DECISIONS = {"approve": ("ApproveOwnership", "approved"), "reject": ("RejectOwnership", "rejected"), "request_changes": ("RequestAdditionalEvidence", "changes_requested")}
TERMINAL = {"approved", "rejected", "changes_requested", "cancelled", "expired"}
MAX_ATTEMPTS = 8


class ExternalApprovalError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def network() -> str:
    return get_settings().groundwork_canton_network


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def commitment(salt: str, obj: Any) -> str:
    return "sha256:" + hashlib.sha256((salt + _canonical(obj)).encode("utf-8")).hexdigest()


def _ledger_time(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


# ── organisation ↔ party bindings (set by a platform admin; users never choose a party) ──


def binding(db: Session, role: str) -> CantonPartyBinding | None:
    return db.scalar(select(CantonPartyBinding).where(CantonPartyBinding.network == network(), CantonPartyBinding.role == role))


def role_of(db: Session, organization_id: str) -> str | None:
    row = db.scalar(select(CantonPartyBinding).where(CantonPartyBinding.network == network(), CantonPartyBinding.organization_id == organization_id))
    return row.role if row else None


def bind(db: Session, organization_id: str, role: str, actor: User) -> CantonPartyBinding:
    if role not in participants.ROLES:
        raise ExternalApprovalError("role must be legal_aid or registry", 422)
    party = getattr(get_settings(), f"canton_{role}_party_id")
    if not party:
        raise ExternalApprovalError(f"CANTON_{role.upper()}_PARTY_ID is not configured", 409)
    if db.get(Organization, organization_id) is None:
        raise ExternalApprovalError("organisation not found", 404)
    other = db.scalar(select(CantonPartyBinding).where(CantonPartyBinding.network == network(), CantonPartyBinding.organization_id == organization_id))
    if other is not None and other.role != role:
        raise ExternalApprovalError("an organisation can hold only one party on a network", 409)
    row = binding(db, role) or CantonPartyBinding(network=network(), role=role, organization_id=organization_id, party_id=party)
    row.organization_id, row.party_id = organization_id, party
    db.add(row)
    db.flush()
    audit.record(db, organization_id=organization_id, event="canton.party_bound", actor=actor, entity_type="canton_binding", entity_id=row.id,
                 detail={"network": row.network, "role": role, "party": party})
    return row


# ── proposing (Groundwork side, legal aid) ───────────────────────────────────


def build_manifest(db: Session, case: Case) -> dict[str, Any]:
    """What the registry reviews: document fingerprints and human-reviewed facts. Stays off-ledger."""
    docs = list(db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id, EvidenceDocument.status == "extracted").order_by(EvidenceDocument.created_at)))
    facts = list(db.scalars(select(ExtractedFact).where(ExtractedFact.case_id == case.id, ExtractedFact.status.in_(("confirmed", "corrected"))).order_by(ExtractedFact.created_at)))
    manifest: dict[str, Any] = {
        "schema": "groundwork.evidence-manifest/1",
        "documents": [{"document_id": d.id, "sha256": d.sha256, "doc_type": d.doc_type, "pages": d.page_count, "filename": d.filename} for d in docs],
        "reviewed_facts": [{"fact_id": f.id, "fact_type": f.fact_type, "value": f.effective_value, "document_id": f.document_id, "page": f.page_number, "status": f.status} for f in facts],
    }
    if case.kind == "rural_land":
        from app.domain.land import service as land

        parcel = land.parcel_of(db, case)
        succ = land.latest(db, case, "succession")
        manifest["land"] = {
            "survey": parcel.survey_key if parcel else "", "village": parcel.village if parcel else "", "region": parcel.region_code if parcel else "",
            "draft_shares": [{"name": s["name"], "share": s["share"]} for s in (succ.result.get("shares", []) if succ else []) if s.get("alive")],
            "records": [{"source_type": r.source_type, "document_id": r.document_id, "survey": r.survey_key} for r in land.records_of(db, case)],
        }
    return manifest


def propose(db: Session, case: Case, user: User, *, action_type: str, rationale: str = "") -> ExternalApproval:
    s = get_settings()
    if not s.groundwork_canton_enabled:
        raise ExternalApprovalError("Canton integration is disabled", 409)
    if action_type not in ACTION_TYPES:
        raise ExternalApprovalError(f"action_type must be one of {', '.join(ACTION_TYPES)}", 422)
    if role_of(db, case.organization_id) != "legal_aid":
        raise ExternalApprovalError("this organisation is not bound to the Legal Aid party on this network", 403)
    reviewer = binding(db, "registry")
    if reviewer is None:
        raise ExternalApprovalError("no registry organisation is bound on this network", 409)
    open_rows = db.scalars(select(ExternalApproval).where(ExternalApproval.case_id == case.id, ExternalApproval.status.notin_(tuple(TERMINAL | {"submission_failed"}))))
    if any(True for _ in open_rows):
        raise ExternalApprovalError("this case already has an external approval in progress", 409)
    manifest = build_manifest(db, case)
    if not manifest["documents"] or not manifest["reviewed_facts"]:
        raise ExternalApprovalError("the evidence must be uploaded and at least one fact reviewed by a person before it goes to the registry", 409)
    version = 1 + len(list(db.scalars(select(ExternalApproval.id).where(ExternalApproval.case_id == case.id))))
    proposal_id = "gwp_" + secrets.token_hex(12)
    case_reference = "cr_" + secrets.token_hex(12)
    ev_salt, pl_salt = secrets.token_hex(16), secrets.token_hex(16)
    evidence_c = commitment(ev_salt, manifest)
    payload = {"proposal_id": proposal_id, "case_reference": case_reference, "action_type": action_type, "case_version": version, "evidence_commitment": evidence_c, "rationale": rationale}
    row = ExternalApproval(
        organization_id=case.organization_id, case_id=case.id, reviewer_organization_id=reviewer.organization_id, requested_by_id=user.id,
        proposal_id=proposal_id, case_reference=case_reference, action_type=action_type, case_version=version, manifest=manifest | {"payload": payload},
        evidence_salt=ev_salt, payload_salt=pl_salt, evidence_commitment=evidence_c, payload_commitment=commitment(pl_salt, payload),
        status="draft", network=network(), synchronizer_id=s.canton_synchronizer_id, package_id=s.canton_approval_package_id,
        expires_at=utcnow() + timedelta(days=s.canton_request_ttl_days),
    )
    db.add(row)
    db.flush()
    from app.domain.approvals import propose as propose_action

    action = propose_action(db, case, "external.submit_to_registry", {"external_approval_id": row.id}, actor=user,
                            rationale=rationale or f"Send {action_type} to the registry over Canton ({network()})")
    row.internal_action_id = action.id
    event(db, row, "proposed", detail={"by": user.id, "internal_action_seq": action.seq, "evidence_commitment": evidence_c})
    audit.record(db, organization_id=case.organization_id, event="external_approval.proposed", actor=user, case_id=case.id, entity_type="external_approval",
                 entity_id=row.id, detail={"proposal_id": proposal_id, "action_type": action_type, "network": row.network, "evidence_commitment": evidence_c})
    return row


def release(db: Session, row: ExternalApproval, actor: User) -> None:
    """Executed by the internal two-person action: queue the ledger create."""
    if row.status != "draft":
        raise ExternalApprovalError(f"the approval is {row.status}")
    s = get_settings()
    args = {
        "proposalId": row.proposal_id, "caseReference": row.case_reference, "legalAid": s.canton_legal_aid_party_id, "registry": s.canton_registry_party_id,
        "actionType": row.action_type, "caseVersion": str(row.case_version), "evidenceCommitment": row.evidence_commitment,
        "payloadCommitment": row.payload_commitment, "createdAt": _ledger_time(utcnow() - timedelta(seconds=5)), "expiresAt": _ledger_time(_aware(row.expires_at)), "supersedes": None,
    }
    row.status = "pending_submission"
    enqueue(db, row, "legal_aid", "create", args)
    event(db, row, "queued_for_submission", detail={"approved_by": actor.id})


# ── outbox ───────────────────────────────────────────────────────────────────


def enqueue(db: Session, row: ExternalApproval, participant: str, command: str, payload: dict[str, Any]) -> CantonOutbox:
    item = CantonOutbox(approval_id=row.id, participant=participant, command=command, command_id=f"gw:{row.network}:{row.proposal_id}:{command}", payload=payload)
    db.add(item)
    db.flush()
    return item


def process_outbox(db: Session, *, approval_id: str | None = None, limit: int = 20) -> dict[str, int]:
    q = select(CantonOutbox).where(CantonOutbox.status == "pending", CantonOutbox.next_attempt_at <= utcnow())
    if approval_id:
        q = q.where(CantonOutbox.approval_id == approval_id)
    done = failed = retried = 0
    for item in list(db.scalars(q.order_by(CantonOutbox.created_at).limit(limit))):
        row = db.get(ExternalApproval, item.approval_id)
        if row is None:
            item.status = "failed"
            continue
        try:
            _deliver(db, item, row)
            item.status, item.completed_at, item.last_error = "done", utcnow(), ""
            done += 1
        except CantonError as exc:
            item.attempts += 1
            item.last_error = exc.safe()
            if exc.kind == "duplicate":
                item.status = "done"  # the ledger already has this command; the update stream will show its effect
                done += 1
            elif exc.retryable and item.attempts < MAX_ATTEMPTS:
                item.next_attempt_at = utcnow() + timedelta(seconds=min(600, 5 * 2 ** item.attempts))
                row.error = f"retrying: {exc.safe()}"
                if row.status in ("pending_submission",) and item.attempts >= 3:
                    row.status = "sync_delayed"
                retried += 1
            else:
                item.status = "failed"
                failed += 1
                _command_failed(db, row, item, exc)
        db.flush()
    return {"done": done, "failed": failed, "retried": retried}


def _deliver(db: Session, item: CantonOutbox, row: ExternalApproval) -> None:
    c = participants.client(item.participant)
    if item.command == "create":
        tx = c.create(participants.template_id("OwnershipApprovalRequest"), item.payload, item.command_id)
        created = next(e for e in created_events(tx) if entity(e["templateId"]) == "OwnershipApprovalRequest")
        row.request_contract_id, row.request_update_id = created["contractId"], tx["updateId"]
        row.package_id = created["templateId"].split(":", 1)[0]
        row.synchronizer_id = tx.get("synchronizerId", row.synchronizer_id)
        row.submitted_at, row.error = utcnow(), ""
        if row.status in ("pending_submission", "sync_delayed"):
            row.status = "submitted"
        event(db, row, "request_committed", participant=item.participant, tx=tx, contract_id=created["contractId"])
        audit.record(db, organization_id=row.organization_id, event="external_approval.submitted", actor_kind="system", case_id=row.case_id,
                     entity_type="external_approval", entity_id=row.id, detail={"network": row.network, "update_id": tx["updateId"], "contract_id": created["contractId"]})
        return
    if not row.request_contract_id:
        raise CantonError("stale_contract", "the request has no contract on the ledger")
    template = participants.template_id("OwnershipApprovalRequest")
    if item.command in DECISIONS:
        tx = c.exercise(template, row.request_contract_id, DECISIONS[item.command][0], item.payload, item.command_id)
        dec = next((e for e in created_events(tx) if entity(e["templateId"]) == "OwnershipApprovalDecision"), None)
        row.status = "decision_submitted"
        event(db, row, "registry_decision_committed", participant=item.participant, tx=tx, contract_id=dec["contractId"] if dec else "",
              detail={"decision": item.command})
        audit.record(db, organization_id=row.reviewer_organization_id, event="external_approval.decision_committed", actor_kind="system",
                     entity_type="external_approval", entity_id=row.id, detail={"decision": item.command, "network": row.network, "update_id": tx["updateId"]})
    elif item.command in ("cancel", "expire"):
        choice, arg = ("CancelRequest", item.payload) if item.command == "cancel" else ("MarkExpired", {"actor": c.party})
        tx = c.exercise(template, row.request_contract_id, choice, arg, item.command_id)
        row.status = "cancel_submitted" if item.command == "cancel" else row.status
        event(db, row, f"{item.command}_committed", participant=item.participant, tx=tx)


def _command_failed(db: Session, row: ExternalApproval, item: CantonOutbox, exc: CantonError) -> None:
    row.error = exc.safe()
    if item.command == "create":
        row.status = "submission_failed"
    elif item.command in DECISIONS:
        row.status = "awaiting_review" if exc.kind != "stale_contract" else "sync_delayed"
        row.decided_by_id = None
    event(db, row, f"{item.command}_failed", participant=item.participant, detail={"error": exc.safe(), "kind": exc.kind})
    audit.record(db, organization_id=row.organization_id, event="external_approval.command_failed", actor_kind="system", case_id=row.case_id,
                 entity_type="external_approval", entity_id=row.id, detail={"command": item.command, "kind": exc.kind, "code": exc.code})


# ── the registry's decision (registry side) ─────────────────────────────────


def verify(row: ExternalApproval) -> bool:
    """The reviewer recomputes both commitments from what was shared with it."""
    manifest = {k: v for k, v in row.manifest.items() if k != "payload"}
    return commitment(row.evidence_salt, manifest) == row.evidence_commitment and commitment(row.payload_salt, row.manifest.get("payload", {})) == row.payload_commitment


def decide(db: Session, row: ExternalApproval, user: User, *, decision: str, reason_code: str, requested: list[str], note: str = "") -> ExternalApproval:
    if role_of(db, user.organization_id) != "registry" or row.reviewer_organization_id != user.organization_id:
        raise ExternalApprovalError("External approval not found", 404)
    if decision not in DECISIONS:
        raise ExternalApprovalError("unknown decision", 422)
    if reason_code not in REASON_CODES or any(r not in EVIDENCE_ITEMS for r in requested):
        raise ExternalApprovalError("unknown reason code or evidence item", 422)
    if row.status != "awaiting_review":
        raise ExternalApprovalError(f"the request is {row.status.replace('_', ' ')}, not awaiting review")
    if _aware(row.expires_at) <= utcnow():
        raise ExternalApprovalError("the request has expired")
    verified = verify(row)
    row.reviewer_verified_commitment = verified
    if not verified and decision == "approve":
        raise ExternalApprovalError("the evidence does not match its commitment; it cannot be approved", 409)
    arg: dict[str, Any] = {"reviewedEvidenceCommitment": row.evidence_commitment, "reviewedPayloadCommitment": row.payload_commitment, "reasonCode": reason_code}
    if decision == "request_changes":
        arg["requested"] = requested
    row.decided_by_id = user.id
    enqueue(db, row, "registry", decision, arg)
    event(db, row, "registry_decision_queued", participant="registry", detail={"decision": decision, "reason": reason_code, "by": user.id, "note_chars": len(note)})
    audit.record(db, organization_id=user.organization_id, event="external_approval.decided_by_reviewer", actor=user, entity_type="external_approval",
                 entity_id=row.id, detail={"decision": decision, "reason": reason_code, "requested": requested, "proposal_id": row.proposal_id})
    db.flush()
    process_outbox(db, approval_id=row.id)
    return row


def cancel(db: Session, row: ExternalApproval, user: User) -> ExternalApproval:
    if row.status not in ("submitted", "awaiting_review", "sync_delayed") or not row.request_contract_id:
        raise ExternalApprovalError(f"a {row.status.replace('_', ' ')} request cannot be cancelled")
    enqueue(db, row, "legal_aid", "cancel", {"reasonCode": "OtherSeeOffLedgerNote"})
    event(db, row, "cancel_queued", detail={"by": user.id})
    db.flush()
    process_outbox(db, approval_id=row.id)
    return row


# ── the update streams: the only way an outcome reaches the projection ───────


def _cursor(db: Session, role: str) -> CantonCursor:
    # Keyed by party: a network reset re-allocates parties under new namespaces, so a stale offset
    # from the previous ledger can never be mistaken for a position in the new one.
    party = getattr(get_settings(), f"canton_{role}_party_id")
    key = f"{network()}:{role}:{hashlib.sha256(party.encode()).hexdigest()[:16]}"
    cur = db.get(CantonCursor, key)
    if cur is None:
        cur = CantonCursor(key=key, ledger_offset=0)
        db.add(cur)
        db.flush()
    return cur


def sync(db: Session, role: str) -> dict[str, Any]:
    c = participants.client(role)
    cur = _cursor(db, role)
    end = c.ledger_end()
    reset = cur.ledger_offset > end
    if reset:
        log.warning("canton ledger reset detected", extra={"role": role, "cursor": cur.ledger_offset, "end": end})
        cur.ledger_offset = 0
    templates = [participants.template_id(t) for t in participants.TEMPLATES]
    seen = 0
    begin = cur.ledger_offset
    while begin < end:
        batch_end = min(end, begin + 500)
        for tx in c.updates(begin, batch_end, templates):
            seen += _apply(db, role, tx)
            cur.ledger_offset = max(cur.ledger_offset, int(tx.get("offset", begin)))
        begin = batch_end
    if cur.ledger_offset != end:
        cur.ledger_offset = end  # idle ticks write nothing
    if reset:
        reconcile(db)
    db.flush()
    return {"role": role, "offset": end, "events": seen, "reset": reset}


def _by_proposal(db: Session, proposal_id: str) -> ExternalApproval | None:
    return db.scalar(select(ExternalApproval).where(ExternalApproval.proposal_id == proposal_id, ExternalApproval.network == network()))


def _apply(db: Session, role: str, tx: dict[str, Any]) -> int:
    n = 0
    for ev in created_events(tx):
        name, args = entity(ev["templateId"]), ev.get("createArgument") or {}
        row = _by_proposal(db, str(args.get("proposalId", "")))
        if row is None:
            continue
        row.last_synced_at = utcnow()
        if name == "OwnershipApprovalRequest":
            if not row.request_contract_id:
                row.request_contract_id, row.request_update_id = ev["contractId"], tx["updateId"]
            if role == "legal_aid" and row.status in ("pending_submission", "sync_delayed"):
                row.status = "submitted"
            if role == "registry":
                row.registry_seen_at = row.registry_seen_at or utcnow()
                if row.status in ("submitted", "pending_submission", "sync_delayed"):
                    row.status = "awaiting_review"
                n += event(db, row, "received_by_registry", participant=role, tx=tx, contract_id=ev["contractId"])
        elif name == "OwnershipApprovalDecision" and role == "legal_aid":
            n += _apply_decision(db, row, ev, tx)
        elif name == "OwnershipApprovalClosed" and role == "legal_aid" and row.status not in TERMINAL:
            row.status = "cancelled" if args.get("closure") == "cancelled" else "expired"
            row.decided_at = _parse_time(str(args.get("closedAt", ""))) or utcnow()
            n += event(db, row, row.status, participant=role, tx=tx, contract_id=ev["contractId"])
            audit.record(db, organization_id=row.organization_id, event=f"external_approval.{row.status}", actor_kind="system", case_id=row.case_id,
                         entity_type="external_approval", entity_id=row.id, detail={"network": row.network, "update_id": tx["updateId"]})
    for ev in archived_events(tx):
        if entity(ev["templateId"]) == "OwnershipApprovalRequest" and role == "legal_aid":
            row = db.scalar(select(ExternalApproval).where(ExternalApproval.request_contract_id == ev["contractId"]))
            if row is not None:
                n += event(db, row, "request_archived", participant=role, tx=tx, contract_id=ev["contractId"])
    return n


def _apply_decision(db: Session, row: ExternalApproval, ev: dict[str, Any], tx: dict[str, Any]) -> int:
    if row.status in TERMINAL:
        return 0
    args = ev.get("createArgument") or {}
    if args.get("evidenceCommitment") != row.evidence_commitment or args.get("payloadCommitment") != row.payload_commitment:
        row.status, row.error = "sync_delayed", "the ledger decision does not carry this proposal's commitments"
        return event(db, row, "decision_commitment_mismatch", participant="legal_aid", tx=tx, contract_id=ev["contractId"])
    status = {"Approved": "approved", "Rejected": "rejected", "ChangesRequested": "changes_requested"}[str(args.get("decisionStatus"))]
    row.status, row.error = status, ""
    row.decision_contract_id, row.decision_update_id = ev["contractId"], tx["updateId"]
    row.decision_reason_code = str(args.get("decisionReasonCode", ""))
    row.requested_evidence = list(args.get("requestedEvidence") or [])
    row.decided_at = _parse_time(str(args.get("decisionTimestamp", ""))) or utcnow()
    n = event(db, row, f"decision_{status}", participant="legal_aid", tx=tx, contract_id=ev["contractId"],
              detail={"reason": row.decision_reason_code, "requested": row.requested_evidence, "decision_time": args.get("decisionTimestamp")})
    case = db.get(Case, row.case_id)
    reviewer = db.get(Organization, row.reviewer_organization_id)
    label = {"approved": "approved", "rejected": "rejected", "changes_requested": "asked for more evidence on"}[status]
    db.add(Decision(
        case_id=row.case_id, title=f"{reviewer.name if reviewer else 'Registry'} {label} {row.action_type}",
        decision=f"External decision committed on Canton {row.network}: {status.replace('_', ' ')} ({row.decision_reason_code}).",
        rationale=f"Ledger update {row.decision_update_id}; decision contract {row.decision_contract_id[:24]}…; evidence commitment {row.evidence_commitment}.",
        decided_by_id=row.requested_by_id or (case.created_by_id if case else None),
        related={"external_approval_id": row.id, "network": row.network, "update_id": row.decision_update_id},
    ))
    if status == "changes_requested" and case is not None:
        db.add(Task(case_id=case.id, title="Registry asked for more evidence: " + ", ".join(row.requested_evidence), kind="evidence_request",
                    description=f"Requested over Canton ({row.network}), reason {row.decision_reason_code}. Add the evidence, have it reviewed, and send a new proposal."))
    audit.record(db, organization_id=row.organization_id, event=f"external_approval.{status}", actor_kind="system", case_id=row.case_id,
                 entity_type="external_approval", entity_id=row.id,
                 detail={"network": row.network, "update_id": row.decision_update_id, "decision_contract_id": row.decision_contract_id,
                         "reason": row.decision_reason_code, "evidence_commitment": row.evidence_commitment, "synchronizer_id": tx.get("synchronizerId", "")})
    return n


def reconcile(db: Session) -> dict[str, int]:
    """Compare in-flight approvals with Legal Aid's active contract set (after a reset, or on demand)."""
    c = participants.client("legal_aid")
    active = c.active_contracts([participants.template_id(t) for t in participants.TEMPLATES])
    by_pid: dict[str, list[dict[str, Any]]] = {}
    for ev in active:
        by_pid.setdefault(str((ev.get("createArgument") or {}).get("proposalId", "")), []).append(ev)
    fixed = lost = 0
    for row in db.scalars(select(ExternalApproval).where(ExternalApproval.network == network(), ExternalApproval.status.in_(("submitted", "awaiting_review", "decision_submitted", "sync_delayed", "cancel_submitted")))):
        evs = by_pid.get(row.proposal_id, [])
        dec = next((e for e in evs if entity(e["templateId"]) == "OwnershipApprovalDecision"), None)
        req = next((e for e in evs if entity(e["templateId"]) == "OwnershipApprovalRequest"), None)
        if dec is not None:
            fixed += _apply_decision(db, row, dec, {"updateId": row.decision_update_id or "reconciled", "offset": None})
        elif req is not None:
            row.request_contract_id = req["contractId"]
            if row.status == "sync_delayed":
                row.status, row.error = "submitted", ""
                fixed += 1
        elif row.status != "sync_delayed":
            row.status, row.error = "sync_delayed", "the request is no longer on the ledger (network reset or pruning); resubmit after review"
            event(db, row, "contract_missing_after_reconcile", participant="legal_aid")
            lost += 1
    return {"fixed": fixed, "lost": lost}


def expire_due(db: Session) -> int:
    n = 0
    for row in db.scalars(select(ExternalApproval).where(ExternalApproval.network == network(), ExternalApproval.status.in_(("submitted", "awaiting_review")))):
        if _aware(row.expires_at) <= utcnow() and not db.scalar(select(CantonOutbox.id).where(CantonOutbox.approval_id == row.id, CantonOutbox.command == "expire")):
            enqueue(db, row, "legal_aid", "expire", {})
            n += 1
    return n


def tick(db: Session) -> dict[str, Any]:
    """One synchronisation round: deliver commands, then read both update streams."""
    if not db.scalar(select(CantonPartyBinding.id).where(CantonPartyBinding.network == network())):
        return {"skipped": "no organisation is bound to a party on this network"}
    out: dict[str, Any] = {"expired": expire_due(db), "outbox": process_outbox(db)}
    for role in participants.ROLES:
        try:
            out[role] = sync(db, role)
        except CantonError as exc:
            out[role] = {"error": exc.safe()}
            for row in db.scalars(select(ExternalApproval).where(ExternalApproval.network == network(), ExternalApproval.status.in_(("submitted", "awaiting_review", "decision_submitted")))):
                row.error = f"{role} participant: {exc.safe()}"  # an outage is never a decision
    return out


def event(db: Session, row: ExternalApproval, kind: str, *, participant: str = "", tx: dict[str, Any] | None = None, contract_id: str = "", detail: dict[str, Any] | None = None) -> int:
    update_id = (tx or {}).get("updateId", "") or ""
    if update_id and db.scalar(select(ExternalApprovalEvent.id).where(ExternalApprovalEvent.approval_id == row.id, ExternalApprovalEvent.kind == kind, ExternalApprovalEvent.update_id == update_id)):
        return 0  # replayed update: already applied
    db.add(ExternalApprovalEvent(
        approval_id=row.id, kind=kind, participant=participant, update_id=update_id, contract_id=contract_id,
        ledger_offset=(tx or {}).get("offset"), record_time=(tx or {}).get("recordTime", "") or "", detail=detail or {},
    ))
    db.flush()
    return 1
