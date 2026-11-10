"""Risk findings: detection, the cross-case review list, and human review of each finding."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.risk import SEVERITY_ORDER, run_detection
from app.infrastructure.database.models import Case, RiskFinding, User, utcnow
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["risks"])


class FindingReview(BaseModel):
    status: str  # open | acknowledged | resolved
    note: str = ""


def _sorted(rows):
    return sorted(rows, key=lambda f: (f.status != "open", SEVERITY_ORDER.get(f.severity, 9), f.title))


@router.post("/cases/{case_id}/risks/detect")
def detect(case_id: str, user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    outcome = run_detection(db, case)
    audit.record(db, organization_id=case.organization_id, event="risks.detected", actor=user, case_id=case.id, entity_type="case", entity_id=case.id, detail=outcome)
    rows = db.scalars(select(RiskFinding).where(RiskFinding.case_id == case.id))
    return {"outcome": outcome, "findings": [s.finding(f) for f in _sorted(rows)]}


@router.get("/cases/{case_id}/risks")
def case_risks(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    rows = db.scalars(select(RiskFinding).where(RiskFinding.case_id == case.id))
    return {"findings": [s.finding(f) for f in _sorted(rows)]}


@router.get("/risks")
def all_risks(status: str | None = "open", severity: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    query = select(RiskFinding, Case).join(Case, Case.id == RiskFinding.case_id).where(Case.organization_id == user.organization_id)
    if status:
        query = query.where(RiskFinding.status == status)
    if severity:
        query = query.where(RiskFinding.severity == severity)
    rows = db.execute(query).all()
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r[0].severity, 9), r[1].reference))
    return {"findings": [s.finding(f, c.reference) for f, c in rows]}


@router.patch("/risks/{finding_id}")
def review(finding_id: str, body: FindingReview, user: User = Depends(require("finding:review")), db: Session = Depends(get_db, scope="function")) -> dict:
    finding, case = get_case_owned(db, user, RiskFinding, finding_id)
    if body.status not in ("open", "acknowledged", "resolved"):
        raise HTTPException(422, detail="status must be open, acknowledged or resolved (dismissal is an approved action)")
    if body.status == "resolved":
        if not body.note.strip():
            raise HTTPException(422, detail="say how it was resolved")
        if finding.severity in ("critical", "high") and user.role not in ("attorney", "admin"):
            raise HTTPException(403, detail="critical and high findings are resolved by an attorney")
    before = finding.status
    finding.status, finding.status_note = body.status, body.note.strip()
    finding.reviewed_by_id, finding.reviewed_at = user.id, utcnow()
    audit.record(
        db, organization_id=case.organization_id, event="finding.reviewed", actor=user, case_id=case.id, entity_type="finding", entity_id=finding.id,
        detail={"from": before, "to": body.status, "note": body.note.strip(), "title": finding.title},
    )
    return {"finding": s.finding(finding)}
