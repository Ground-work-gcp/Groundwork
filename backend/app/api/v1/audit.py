"""The verified history: audit log, CSV export and chain verification."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import to_csv, verify_chain
from app.infrastructure.database.models import AuditEvent, Case, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case

from . import serializers as s
from .deps import current_user

router = APIRouter(prefix="/audit", tags=["audit"])


def _query(user: User, db: Session, case_id: str | None):
    query = select(AuditEvent).where(AuditEvent.organization_id == user.organization_id)
    if case_id:
        get_case(db, user, case_id)
        query = query.where(AuditEvent.case_id == case_id)
    return query


@router.get("")
def log(
    case_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    before: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    query = _query(user, db, case_id)
    if before:
        query = query.where(AuditEvent.seq < before)
    rows = list(db.scalars(query.order_by(AuditEvent.seq.desc()).limit(limit)))
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    refs = {c.id: c.reference for c in db.scalars(select(Case).where(Case.organization_id == user.organization_id))}
    return {"events": [s.audit_event(e, names) | {"case_reference": refs.get(e.case_id or "")} for e in rows], "count": len(rows)}


@router.get("/export.csv", response_class=PlainTextResponse)
def export(case_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> PlainTextResponse:
    rows = list(db.scalars(_query(user, db, case_id).order_by(AuditEvent.seq)))
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    return PlainTextResponse(
        to_csv(rows, names), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="groundwork-audit.csv"'}
    )


@router.get("/verify")
def verify(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return verify_chain(db, user.organization_id)
