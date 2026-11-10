"""The resolution workflow: tasks, comments, decisions, and proposing allowlisted actions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.approvals import ApprovalError, propose
from app.domain.workflows import (
    REGISTRY,
    STAGES,
    ParamError,
    catalog,
    checklist,
    params_hash,
    suggest_actions,
)
from app.infrastructure.database.models import (
    Comment,
    Decision,
    RiskFinding,
    Task,
    User,
    WorkflowAction,
    utcnow,
)
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned, get_org_user

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["workflows"])


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = ""
    kind: str = "general"
    assignee_id: str | None = None
    due_date: str | None = None
    finding_id: str | None = None


class TaskPatch(BaseModel):
    status: str | None = None
    assignee_id: str | None = None
    due_date: str | None = None
    title: str | None = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    entity_type: str = "case"
    entity_id: str = ""


class DecisionIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    decision: str = Field(min_length=3)
    rationale: str = ""
    related: dict[str, Any] = {}


class ActionIn(BaseModel):
    action: str
    params: dict[str, Any]
    rationale: str = ""


def workflow_payload(db: Session, case) -> dict:
    actions = list(db.scalars(select(WorkflowAction).where(WorkflowAction.case_id == case.id).order_by(WorkflowAction.seq.desc())))
    return {
        "stage": case.stage,
        "status": case.status,
        "stages": [{"key": k, "label": label} for k, label in STAGES],
        "checklist": checklist(db, case),
        "tasks": [s.task(t) for t in db.scalars(select(Task).where(Task.case_id == case.id).order_by(Task.created_at))],
        "decisions": [s.decision(d) for d in db.scalars(select(Decision).where(Decision.case_id == case.id).order_by(Decision.created_at))],
        "actions": [s.action(a, summary=REGISTRY[a.action].summary(a.params) if a.action in REGISTRY else None) for a in actions],
        "suggestions": suggest_actions(db, case),
    }


@router.get("/workflows/stages")
def stages() -> dict:
    return {"stages": [{"key": k, "label": label} for k, label in STAGES]}


@router.get("/workflows/actions")
def actions_catalog(_user: User = Depends(current_user)) -> dict:
    return {"actions": catalog()}


@router.get("/cases/{case_id}/workflow")
def workflow(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return workflow_payload(db, get_case(db, user, case_id))


@router.post("/cases/{case_id}/tasks", status_code=201)
def add_task(case_id: str, body: TaskIn, user: User = Depends(require("task:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.assignee_id:
        get_org_user(db, user, body.assignee_id)
    if body.finding_id:
        finding, finding_case = get_case_owned(db, user, RiskFinding, body.finding_id)
        if finding_case.id != case.id:
            raise HTTPException(422, detail="finding belongs to another case")
    if body.kind not in ("general", "evidence_request", "review"):
        raise HTTPException(422, detail="kind must be general, evidence_request or review")
    task = Task(case_id=case.id, created_by_id=user.id, **body.model_dump())
    db.add(task)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="task.created", actor=user, case_id=case.id, entity_type="task", entity_id=task.id, detail={"title": task.title, "assignee_id": task.assignee_id})
    return {"task": s.task(task)}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch, user: User = Depends(require("task:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    task, case = get_case_owned(db, user, Task, task_id)
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("open", "in_progress", "done", "cancelled"):
        raise HTTPException(422, detail="status must be open, in_progress, done or cancelled")
    if "assignee_id" in changes and changes["assignee_id"]:
        get_org_user(db, user, changes["assignee_id"])
    for key, value in changes.items():
        setattr(task, key, value)
    if changes.get("status") == "done":
        task.completed_at = utcnow()
    audit.record(db, organization_id=case.organization_id, event="task.updated", actor=user, case_id=case.id, entity_type="task", entity_id=task.id, detail=changes)
    return {"task": s.task(task)}


@router.get("/cases/{case_id}/comments")
def comments(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    rows = db.scalars(select(Comment).where(Comment.case_id == case.id).order_by(Comment.created_at))
    return {"comments": [s.comment(c) | {"author_name": names.get(c.author_id)} for c in rows]}


@router.post("/cases/{case_id}/comments", status_code=201)
def add_comment(case_id: str, body: CommentIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if user.role == "viewer":
        raise HTTPException(403, detail="Viewers cannot comment")
    row = Comment(case_id=case.id, author_id=user.id, body=body.body.strip(), entity_type=body.entity_type, entity_id=body.entity_id)
    db.add(row)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="comment.added", actor=user, case_id=case.id, entity_type="comment", entity_id=row.id, detail={"on": f"{body.entity_type}:{body.entity_id}"})
    return {"comment": s.comment(row) | {"author_name": user.full_name}}


@router.post("/cases/{case_id}/decisions", status_code=201)
def add_decision(case_id: str, body: DecisionIn, user: User = Depends(require("decision:record")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    row = Decision(case_id=case.id, decided_by_id=user.id, **body.model_dump())
    db.add(row)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="decision.recorded", actor=user, case_id=case.id, entity_type="decision", entity_id=row.id, detail={"title": row.title, "decision": row.decision})
    return {"decision": s.decision(row)}


@router.post("/cases/{case_id}/actions", status_code=201)
def propose_action(case_id: str, body: ActionIn, user: User = Depends(require("action:propose")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    try:
        row = propose(db, case, body.action, body.params, actor=user, rationale=body.rationale)
    except ParamError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    return {"action": s.action(row, summary=REGISTRY[row.action].summary(row.params))}


@router.post("/cases/{case_id}/actions/suggested")
def propose_suggested(case_id: str, user: User = Depends(require("action:propose")), db: Session = Depends(get_db, scope="function")) -> dict:
    """Queue the rule-based evidence requests as proposals. Each still needs an approver."""
    case = get_case(db, user, case_id)
    pending = {
        a.params_hash for a in db.scalars(select(WorkflowAction).where(WorkflowAction.case_id == case.id, WorkflowAction.status.in_(("proposed", "executed"))))
    }
    created = []
    for suggestion in suggest_actions(db, case):
        if params_hash(suggestion["action"], suggestion["params"]) in pending:
            continue
        try:
            row = propose(db, case, suggestion["action"], suggestion["params"], actor=None, rationale=suggestion["rationale"], kind="system")
        except (ParamError, ApprovalError):
            continue
        pending.add(row.params_hash)
        created.append(s.action(row, summary=REGISTRY[row.action].summary(row.params)))
    return {"created": created}
