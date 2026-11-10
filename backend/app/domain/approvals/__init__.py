"""Proposing, approving and rejecting workflow actions (Beacon's approval ledger, adapted).

The rules that make approval a record rather than a prompt:

1. only allowlisted actions with exactly-typed params can be proposed (``workflows.REGISTRY``);
2. the approver must hold an approver role for that action, and by default must not be the
   person who proposed it (``REQUIRE_SEPARATE_APPROVER``);
3. the approver types the exact phrase ``approve action <n>`` — the words are stored;
4. the params hash is re-checked at execution, so an approval covers exactly what was shown;
5. an action executes once: the ``proposed → executed`` transition is a conditional update;
6. ``WORKFLOW_EXECUTION_ENABLED=false`` stops every execution path (the kill switch);
7. proposals expire, and the precondition is re-evaluated at approval time.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.domain.workflows import REGISTRY, confirmation_phrase, params_hash, validate_params
from app.infrastructure.database.models import Approval, Case, User, WorkflowAction, utcnow
from app.infrastructure.external_integrations import notify


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def propose(
    db: Session, case: Case, action: str, params: dict[str, Any], *, actor: User | None, rationale: str = "", kind: str = "user"
) -> WorkflowAction:
    clean = validate_params(action, params)
    spec = REGISTRY[action]
    problem = spec.precondition(db, case, clean)
    if problem:
        raise ApprovalError(f"cannot propose: {problem}")
    case.action_seq += 1
    row = WorkflowAction(
        case_id=case.id,
        seq=case.action_seq,
        action=action,
        params=clean,
        params_hash=params_hash(action, clean),
        rationale=rationale.strip(),
        proposed_by_id=actor.id if actor else None,
        proposed_by_kind=kind,
        expires_at=utcnow() + timedelta(days=get_settings().action_ttl_days),
    )
    db.add(row)
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event="action.proposed", actor=actor, actor_kind=None if actor else "system",
        case_id=case.id, entity_type="workflow_action", entity_id=row.id,
        detail={"seq": row.seq, "action": action, "params": clean, "summary": spec.summary(clean), "rationale": row.rationale},
    )
    notify(
        "approval_requested",
        f"Approval requested on {case.reference}: {spec.summary(clean)}",
        f"Say “{confirmation_phrase(row.seq)}” on the Approvals page to approve.",
        case_id=case.id,
    )
    return row


def _expired(row: WorkflowAction) -> bool:
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    return expires <= utcnow()


def approve(db: Session, case: Case, row: WorkflowAction, actor: User, confirmation: str) -> WorkflowAction:
    settings = get_settings()
    spec = REGISTRY.get(row.action)
    if spec is None:
        raise ApprovalError("action is no longer allowlisted")
    if row.status != "proposed":
        raise ApprovalError(f"action is {row.status}, not awaiting approval")
    if _expired(row):
        row.status = "expired"
        raise ApprovalError("the proposal has expired; propose it again")
    if actor.role not in spec.approver_roles:
        raise ApprovalError(f"{row.action} must be approved by: {', '.join(spec.approver_roles)}", 403)
    if settings.require_separate_approver and row.proposed_by_id == actor.id:
        raise ApprovalError("the person who proposed an action cannot also approve it", 403)
    expected = confirmation_phrase(row.seq)
    if " ".join(confirmation.lower().split()) != expected:
        raise ApprovalError(f'type the exact phrase "{expected}" to approve', 422)
    if params_hash(row.action, row.params) != row.params_hash:
        raise ApprovalError("the action's parameters changed after it was proposed")
    if not settings.workflow_execution_enabled:
        raise ApprovalError("workflow execution is disabled (WORKFLOW_EXECUTION_ENABLED=false)", 423)
    problem = spec.precondition(db, case, row.params)
    if problem:
        raise ApprovalError(f"cannot execute now: {problem}")

    # Single use: exactly one request can move proposed → executing.
    claimed = db.execute(
        update(WorkflowAction)
        .where(WorkflowAction.id == row.id, WorkflowAction.status == "proposed")
        .values(status="executing", decided_at=utcnow())
    )
    if claimed.rowcount != 1:
        raise ApprovalError("this action was already decided")
    db.refresh(row)
    db.add(Approval(action_id=row.id, decision="approved", decided_by_id=actor.id, statement=expected, params_hash=row.params_hash))
    audit.record(
        db, organization_id=case.organization_id, event="action.approved", actor=actor, case_id=case.id,
        entity_type="workflow_action", entity_id=row.id, detail={"seq": row.seq, "action": row.action, "statement": expected},
    )
    try:
        with db.begin_nested():
            outcome = spec.execute(db, case, row.params, actor)
        row.status, row.result = "executed", outcome
        audit.record(
            db, organization_id=case.organization_id, event="action.executed", actor=actor, case_id=case.id,
            entity_type="workflow_action", entity_id=row.id, detail={"seq": row.seq, "action": row.action, "result": outcome},
        )
    except Exception as exc:
        row.status, row.result = "failed", {"error": str(exc)[:500]}
        audit.record(
            db, organization_id=case.organization_id, event="action.failed", actor=actor, case_id=case.id,
            entity_type="workflow_action", entity_id=row.id, detail={"seq": row.seq, "action": row.action, "error": str(exc)[:500]},
        )
    db.flush()
    return row


def reject(db: Session, case: Case, row: WorkflowAction, actor: User, reason: str) -> WorkflowAction:
    spec = REGISTRY.get(row.action)
    if row.status != "proposed":
        raise ApprovalError(f"action is {row.status}, not awaiting approval")
    if spec is None or (actor.role not in spec.approver_roles and actor.id != row.proposed_by_id):
        raise ApprovalError("only an approver, or the proposer withdrawing it, can reject this action", 403)
    if not reason.strip():
        raise ApprovalError("a reason is required", 422)
    withdrawn = actor.id == row.proposed_by_id
    claimed = db.execute(
        update(WorkflowAction)
        .where(WorkflowAction.id == row.id, WorkflowAction.status == "proposed")
        .values(status="withdrawn" if withdrawn else "rejected", decided_at=utcnow())
    )
    if claimed.rowcount != 1:
        raise ApprovalError("this action was already decided")
    db.refresh(row)
    db.add(Approval(action_id=row.id, decision="rejected", decided_by_id=actor.id, statement=reason.strip(), params_hash=row.params_hash))
    audit.record(
        db, organization_id=case.organization_id, event="action.withdrawn" if withdrawn else "action.rejected", actor=actor,
        case_id=case.id, entity_type="workflow_action", entity_id=row.id, detail={"seq": row.seq, "action": row.action, "reason": reason.strip()},
    )
    db.flush()
    return row
