"""Properties: the canonical identity of the land each case concerns."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.evidence import normalize_parcel
from app.infrastructure.database.models import Case, Property, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case_owned

from . import serializers as s
from .deps import current_user, require

router = APIRouter(prefix="/properties", tags=["properties"])


class PropertyPatch(BaseModel):
    address_line: str | None = None
    city: str | None = None
    county: str | None = None
    state: str | None = None
    postal_code: str | None = None
    parcel_number: str | None = None
    legal_description: str | None = None


@router.get("")
def properties(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.execute(
        select(Property, Case).join(Case, Case.id == Property.case_id).where(Case.organization_id == user.organization_id).order_by(Case.reference)
    ).all()
    return {
        "properties": [
            s.prop(p) | {"case": {"id": c.id, "reference": c.reference, "title": c.title, "stage": c.stage, "status": c.status}}
            for p, c in rows
        ]
    }


@router.get("/{property_id}")
def one(property_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    prop, case = get_case_owned(db, user, Property, property_id)
    return {"property": s.prop(prop), "case": s.case(case)}


@router.patch("/{property_id}")
def update(property_id: str, body: PropertyPatch, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    prop, case = get_case_owned(db, user, Property, property_id)
    changes = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items()}
    for key, value in changes.items():
        setattr(prop, key, value)
    if "parcel_number" in changes:
        prop.parcel_number_normalized = normalize_parcel(changes["parcel_number"])
    audit.record(db, organization_id=case.organization_id, event="property.updated", actor=user, case_id=case.id, entity_type="property", entity_id=prop.id, detail=changes)
    return {"property": s.prop(prop)}
