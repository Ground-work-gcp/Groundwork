"""Reconciliation runs over a case, their results, and the review queue (with undo)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.reconciliation.engine.tolerance import tolerances_as_dict
from app.domain.reconciliation.property_lane import PROPERTY_TOLERANCES
from app.domain.reconciliation.service import apply_review, results_of, run_case, summary
from app.infrastructure.database.models import Case, ReconciliationRun, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_run

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["reconciliation"])


class QueueCommand(BaseModel):
    type: str  # Resolve | Restore
    ids: list[str] = []
    to: str | None = None
    states: dict[str, str] | None = None
    scope: str = ""


@router.post("/cases/{case_id}/reconciliation/runs", status_code=201)
def run(case_id: str, user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    row = run_case(db, case, user)
    audit.record(
        db, organization_id=case.organization_id, event="reconciliation.run", actor=user, case_id=case.id, entity_type="reconciliation_run",
        entity_id=row.id, detail={"stats": row.stats, "summary": row.summary},
    )
    return {"run": s.run(row), "results": [s.result(r) for r in results_of(db, row)]}


@router.get("/cases/{case_id}/reconciliation/runs")
def runs(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    rows = db.scalars(select(ReconciliationRun).where(ReconciliationRun.case_id == case.id).order_by(ReconciliationRun.created_at.desc()))
    return {"runs": [s.run(r) for r in rows]}


@router.get("/reconciliation/runs/{run_id}")
def one(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    row = get_run(db, user, run_id)
    payload: dict[str, Any] = {"run": s.run(row), "results": [s.result(r) for r in results_of(db, row)]}
    if row.case_id:
        case = db.get(Case, row.case_id)
        assert case is not None
        payload["summary"] = summary(db, case, row)
    return payload


@router.post("/reconciliation/runs/{run_id}/review")
def review(run_id: str, body: QueueCommand, user: User = Depends(require("finding:review")), db: Session = Depends(get_db, scope="function")) -> dict:
    row = get_run(db, user, run_id)
    try:
        outcome = apply_review(db, row, body.model_dump(exclude_none=True), user)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    audit.record(
        db, organization_id=row.organization_id, event="reconciliation.reviewed", actor=user, case_id=row.case_id,
        entity_type="reconciliation_run", entity_id=row.id, detail={"command": body.model_dump(exclude_none=True), "label": outcome["label"]},
    )
    return {"label": outcome["label"], "inverse": outcome["inverse"], "results": [s.result(r) for r in results_of(db, row)]}


@router.get("/reconciliation/tolerances")
def tolerances(_user: User = Depends(current_user)) -> dict:
    return {"property_case": PROPERTY_TOLERANCES.as_dict(), "settlement_batch": tolerances_as_dict()}
