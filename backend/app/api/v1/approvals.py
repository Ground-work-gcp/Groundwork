"""The approval queue: every proposed action across the organisation, and the decision on each."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.approvals import ApprovalError, approve, reject
from app.domain.workflows import REGISTRY
from app.infrastructure.database.models import Approval, Case, User, WorkflowAction
from app.infrastructure.database.session import get_db
from app.repositories import get_case_owned

from . import serializers as s
from .deps import current_user

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApproveIn(BaseModel):
    confirmation: str


class RejectIn(BaseModel):
    reason: str


def _row(a: WorkflowAction, c: Case, approval: Approval | None, names: dict[str, str]) -> dict:
    spec = REGISTRY.get(a.action)
    return s.action(a, c.reference, spec.summary(a.params) if spec else None) | {
        "case_title": c.title,
        "approver_roles": list(spec.approver_roles) if spec else [],
        "reversible": spec.reversible if spec else None,
        "proposed_by_name": names.get(a.proposed_by_id or "", "detection rules" if a.proposed_by_kind == "system" else None),
        "decision": {"decision": approval.decision, "by": names.get(approval.decided_by_id), "statement": approval.statement, "at": s.ts(approval.created_at)} if approval else None,
    }


@router.get("")
def queue(status: str | None = "proposed", user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    query = select(WorkflowAction, Case).join(Case, Case.id == WorkflowAction.case_id).where(Case.organization_id == user.organization_id)
    if status:
        query = query.where(WorkflowAction.status == status)
    rows = db.execute(query.order_by(WorkflowAction.created_at.desc())).all()
    approvals = {a.action_id: a for a in db.scalars(select(Approval).where(Approval.action_id.in_([r[0].id for r in rows])))} if rows else {}
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    return {"actions": [_row(a, c, approvals.get(a.id), names) for a, c in rows]}


def _decide(action_id: str, user: User, db: Session):
    action, case = get_case_owned(db, user, WorkflowAction, action_id)
    return action, case


@router.post("/{action_id}/approve")
def approve_action(action_id: str, body: ApproveIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    action, case = _decide(action_id, user, db)
    try:
        approve(db, case, action, user, body.confirmation)
    except ApprovalError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    return {"action": _row(action, case, approval, names)}


@router.post("/{action_id}/reject")
def reject_action(action_id: str, body: RejectIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    action, case = _decide(action_id, user, db)
    try:
        reject(db, case, action, user, body.reason)
    except ApprovalError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    approval = db.scalar(select(Approval).where(Approval.action_id == action.id))
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    return {"action": _row(action, case, approval, names)}
