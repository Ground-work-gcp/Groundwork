"""Cross-organisation approvals over Canton: the Legal Aid side, the Registry portal, and status/history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import has_permission
from app.domain import external_approvals as ext
from app.domain.approvals import ApprovalError
from app.infrastructure.canton import participants
from app.infrastructure.canton.errors import CantonError
from app.infrastructure.database.models import ExternalApproval, ExternalApprovalEvent, Organization, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case, not_found

from . import serializers as s
from .deps import current_user, platform_admin, require

router = APIRouter(tags=["external-approvals"])

STATUS_LABELS = {
    "draft": "Draft — awaiting a second Groundwork approver", "pending_submission": "Pending submission", "submitted": "Submitted to Canton (committed)",
    "awaiting_review": "Awaiting external review", "decision_submitted": "Registry decision committed — syncing", "approved": "Approved",
    "rejected": "Rejected", "changes_requested": "Additional evidence requested", "cancel_submitted": "Cancellation committed — syncing",
    "cancelled": "Cancelled", "expired": "Expired", "submission_failed": "Submission failed", "sync_delayed": "Synchronisation delayed",
}


def out(db: Session, row: ExternalApproval, *, viewer: User, full: bool = False) -> dict[str, Any]:
    orgs = {o.id: o.name for o in db.scalars(select(Organization).where(Organization.id.in_([row.organization_id, row.reviewer_organization_id])))}
    is_reviewer = viewer.organization_id == row.reviewer_organization_id
    data = {
        "id": row.id, "proposal_id": row.proposal_id, "case_reference": row.case_reference, "action_type": row.action_type, "case_version": row.case_version,
        "status": row.status, "status_label": STATUS_LABELS.get(row.status, row.status), "network": row.network,
        "requesting_organization": orgs.get(row.organization_id), "reviewer_organization": orgs.get(row.reviewer_organization_id),
        "evidence_commitment": row.evidence_commitment, "payload_commitment": row.payload_commitment, "commitment_verified": ext.verify(row),
        "reviewer_verified_commitment": row.reviewer_verified_commitment,
        "ledger": {
            "synchronizer_id": row.synchronizer_id, "package_id": row.package_id, "request_contract_id": row.request_contract_id,
            "request_update_id": row.request_update_id, "decision_contract_id": row.decision_contract_id, "decision_update_id": row.decision_update_id,
        },
        "decision_reason_code": row.decision_reason_code, "requested_evidence": row.requested_evidence,
        "expires_at": s.ts(row.expires_at), "submitted_at": s.ts(row.submitted_at), "registry_seen_at": s.ts(row.registry_seen_at),
        "decided_at": s.ts(row.decided_at), "last_synced_at": s.ts(row.last_synced_at), "error": row.error, "created_at": s.ts(row.created_at),
        "internal_action_id": row.internal_action_id,
    }
    if not is_reviewer:
        data["case_id"] = row.case_id
    if full:
        # The manifest is shared only with the two organisations in the workflow (off-ledger channel).
        data["manifest"] = {k: v for k, v in row.manifest.items() if k != "payload"}
        data["payload"] = row.manifest.get("payload", {})
        if is_reviewer:
            data["salts"] = {"evidence": row.evidence_salt, "payload": row.payload_salt}
    return data


def _visible(db: Session, user: User, approval_id: str) -> ExternalApproval:
    row = db.scalar(select(ExternalApproval).where(ExternalApproval.id == approval_id, or_(
        ExternalApproval.organization_id == user.organization_id, ExternalApproval.reviewer_organization_id == user.organization_id)))
    if row is None:
        raise not_found("External approval not found")
    return row


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, ext.ExternalApprovalError):
        return HTTPException(exc.status, detail=str(exc))
    if isinstance(exc, ApprovalError):
        return HTTPException(exc.status, detail=str(exc))
    if isinstance(exc, CantonError):
        return HTTPException(503 if exc.retryable else 409, detail=exc.safe())
    raise exc


# ── legal aid side ───────────────────────────────────────────────────────────


class ProposeIn(BaseModel):
    action_type: str = "ConfirmSuccession"
    rationale: str = Field(default="", max_length=1000)


@router.post("/cases/{case_id}/external-approvals", status_code=201)
def propose(case_id: str, body: ProposeIn, user: User = Depends(require("action:propose")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    try:
        row = ext.propose(db, case, user, action_type=body.action_type, rationale=body.rationale)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc) from exc
    return {"approval": out(db, row, viewer=user, full=True)}


@router.get("/cases/{case_id}/external-approvals")
def for_case(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    rows = db.scalars(select(ExternalApproval).where(ExternalApproval.case_id == case.id).order_by(ExternalApproval.created_at.desc()))
    return {"approvals": [out(db, r, viewer=user) for r in rows], "canton": canton_summary(db, user)}


@router.get("/external-approvals/{approval_id}")
def one(approval_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return {"approval": out(db, _visible(db, user, approval_id), viewer=user, full=True)}


@router.get("/external-approvals/{approval_id}/events")
def events(approval_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    row = _visible(db, user, approval_id)
    rows = db.scalars(select(ExternalApprovalEvent).where(ExternalApprovalEvent.approval_id == row.id).order_by(ExternalApprovalEvent.created_at))
    return {"events": [{"kind": e.kind, "participant": e.participant, "update_id": e.update_id, "contract_id": e.contract_id, "offset": e.ledger_offset,
                        "record_time": e.record_time, "detail": e.detail, "at": s.ts(e.created_at)} for e in rows], "network": row.network}


@router.post("/external-approvals/{approval_id}/cancel")
def cancel(approval_id: str, user: User = Depends(require("action:approve")), db: Session = Depends(get_db, scope="function")) -> dict:
    row = _visible(db, user, approval_id)
    if row.organization_id != user.organization_id:
        raise not_found("External approval not found")
    try:
        ext.cancel(db, row, user)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc) from exc
    return {"approval": out(db, row, viewer=user)}


@router.post("/external-approvals/sync")
def sync_now(user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    if not get_settings().groundwork_canton_enabled:
        raise HTTPException(409, detail="Canton integration is disabled")
    result = ext.tick(db)
    return {"ok": True, "result": {k: v for k, v in result.items() if k in ("legal_aid", "registry", "outbox", "expired")}}


# ── registry portal ──────────────────────────────────────────────────────────


def _registry_user(db: Session, user: User) -> User:
    if ext.role_of(db, user.organization_id) != "registry":
        raise HTTPException(404, detail="Not found")
    return user


@router.get("/registry/external-approvals/pending")
def registry_pending(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    _registry_user(db, user)
    rows = db.scalars(select(ExternalApproval).where(ExternalApproval.reviewer_organization_id == user.organization_id).order_by(ExternalApproval.created_at.desc()))
    items = [out(db, r, viewer=user, full=True) for r in rows if r.status not in ("draft", "pending_submission")]
    return {"approvals": items, "can_decide": has_permission(user.role, "action:approve"), "canton": canton_summary(db, user)}


class DecisionIn(BaseModel):
    reason_code: str = "EvidenceSufficient"
    requested: list[str] = []
    note: str = Field(default="", max_length=1000)


def _decide(approval_id: str, decision: str, body: DecisionIn, user: User, db: Session) -> dict:
    _registry_user(db, user)
    row = _visible(db, user, approval_id)
    try:
        ext.decide(db, row, user, decision=decision, reason_code=body.reason_code, requested=body.requested, note=body.note)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc) from exc
    return {"approval": out(db, row, viewer=user, full=True)}


@router.post("/registry/external-approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionIn = DecisionIn(), user: User = Depends(require("action:approve")), db: Session = Depends(get_db, scope="function")) -> dict:
    return _decide(approval_id, "approve", body, user, db)


@router.post("/registry/external-approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionIn, user: User = Depends(require("action:approve")), db: Session = Depends(get_db, scope="function")) -> dict:
    return _decide(approval_id, "reject", body, user, db)


@router.post("/registry/external-approvals/{approval_id}/request-changes")
def request_changes(approval_id: str, body: DecisionIn, user: User = Depends(require("action:approve")), db: Session = Depends(get_db, scope="function")) -> dict:
    return _decide(approval_id, "request_changes", body, user, db)


# ── status and bindings ──────────────────────────────────────────────────────


def canton_summary(db: Session, user: User) -> dict[str, Any]:
    st = get_settings()
    return {"enabled": st.groundwork_canton_enabled, "network": st.groundwork_canton_network, "role": ext.role_of(db, user.organization_id) if st.groundwork_canton_enabled else None}


@router.get("/canton/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    data = participants.status()
    data["role"] = ext.role_of(db, user.organization_id) if data["enabled"] else None
    if not user.is_platform_admin:  # party ids and offsets are operator detail
        data["participants"] = {k: {"ok": v.get("ok")} for k, v in data.get("participants", {}).items()}
    return data


class BindIn(BaseModel):
    organization_id: str
    role: str


@router.post("/admin/canton/bindings")
def bind(body: BindIn, admin: User = Depends(platform_admin), db: Session = Depends(get_db, scope="function")) -> dict:
    try:
        row = ext.bind(db, body.organization_id, body.role, admin)
    except Exception as exc:  # noqa: BLE001
        raise _err(exc) from exc
    return {"binding": {"organization_id": row.organization_id, "role": row.role, "network": row.network, "party_id": row.party_id}}


