"""The centralized workflow graph — organisation-wide and per case. Computed live on every call."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.domain.flow import case_flow, org_flow
from app.infrastructure.database.models import User
from app.infrastructure.database.session import get_db
from app.repositories import get_case

from .deps import current_user

router = APIRouter(tags=["flow"])


@router.get("/flow")
def organization_flow(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return org_flow(db, user)


@router.get("/cases/{case_id}/flow")
def one_case_flow(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return case_flow(db, get_case(db, user, case_id), user)
