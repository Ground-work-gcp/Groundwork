"""Ownership history: reconstructed events, manual events, and the chain analysis."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.evidence import parse_date_text
from app.domain.ownership import analyze, reconstruct
from app.infrastructure.database.models import OwnershipEvent, Property, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["ownership"])
EVENT_TYPES = ("deed_transfer", "quitclaim", "probate_distribution", "will", "death", "tax_assessment")


class EventIn(BaseModel):
    event_type: str
    event_date: str | None = None
    grantors: list[str] = []
    grantees: list[str] = []
    instrument: str = ""
    document_id: str | None = None
    page_number: int | None = None
    notes: str = ""


def _payload(db: Session, case) -> dict:
    analysis = analyze(db, case)
    annotations = {a["id"]: a for a in analysis["events"]}
    events = sorted(
        db.scalars(select(OwnershipEvent).where(OwnershipEvent.case_id == case.id)),
        key=lambda e: (e.event_date is None, e.event_date or ""),
    )
    return {
        "events": [s.event(e, annotations.get(e.id)) for e in events],
        "current_owners": analysis["current_owners"],
        "deceased_current_owners": analysis["deceased_current_owners"],
        "issues": analysis["issues"],
    }


@router.get("/cases/{case_id}/ownership")
def ownership(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return _payload(db, get_case(db, user, case_id))


@router.post("/cases/{case_id}/ownership/reconstruct")
def rebuild(case_id: str, user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    analysis = reconstruct(db, case)
    audit.record(
        db, organization_id=case.organization_id, event="ownership.reconstructed", actor=user, case_id=case.id, entity_type="case", entity_id=case.id,
        detail={"events": analysis["event_count"], "issues": len(analysis["issues"]), "current_owners": analysis["current_owners"]},
    )
    return _payload(db, case)


@router.post("/cases/{case_id}/ownership/events", status_code=201)
def add_event(case_id: str, body: EventIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(422, detail=f"event_type must be one of {', '.join(EVENT_TYPES)}")
    date = parse_date_text(body.event_date) if body.event_date else None
    if body.event_date and not date:
        raise HTTPException(422, detail="event_date is not a date")
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    event = OwnershipEvent(
        case_id=case.id, property_id=prop.id if prop else None, event_type=body.event_type, event_date=date,
        grantors=[g.strip() for g in body.grantors if g.strip()], grantees=[g.strip() for g in body.grantees if g.strip()],
        instrument=body.instrument, document_id=body.document_id, page_number=body.page_number, confidence=1.0, source="manual", notes=body.notes,
    )
    db.add(event)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="ownership.event_added", actor=user, case_id=case.id, entity_type="ownership_event", entity_id=event.id, detail=body.model_dump())
    return _payload(db, case)


@router.delete("/ownership/events/{event_id}")
def delete_event(event_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    event, case = get_case_owned(db, user, OwnershipEvent, event_id)
    if event.source != "manual":
        raise HTTPException(409, detail="Reconstructed events come from evidence; correct or reject the facts instead")
    audit.record(db, organization_id=case.organization_id, event="ownership.event_removed", actor=user, case_id=case.id, entity_type="ownership_event", entity_id=event.id)
    db.delete(event)
    db.flush()
    return _payload(db, case)
