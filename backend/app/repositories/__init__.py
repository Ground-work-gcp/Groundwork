"""Tenant-scoped data access.

Every read of a case-owned record goes through here, and every lookup joins back to the
case's organisation. A record from another organisation is indistinguishable from one that
does not exist (404), so one user's documents can never be reached by guessing an id.
"""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import (
    Case,
    Comment,
    DocumentPage,
    EvidenceDocument,
    ExtractedFact,
    FinancialObligation,
    OwnershipEvent,
    Party,
    Property,
    ReconciliationResult,
    ReconciliationRun,
    Referral,
    RiskFinding,
    Task,
    Transaction,
    User,
    WorkflowAction,
)

T = TypeVar("T")

# Models that carry a case_id directly.
_CASE_OWNED = (
    Property,
    Party,
    EvidenceDocument,
    ExtractedFact,
    OwnershipEvent,
    FinancialObligation,
    Transaction,
    RiskFinding,
    Task,
    Comment,
    WorkflowAction,
)


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def get_case(db: Session, user: User, case_id: str) -> Case:
    case = db.scalar(select(Case).where(Case.id == case_id, Case.organization_id == user.organization_id))
    if case is None:
        raise not_found("Case not found")
    return case


def get_case_owned(db: Session, user: User, model: type[T], obj_id: str) -> tuple[T, Case]:
    """A case-owned record and its case, only if the case belongs to the user's organisation."""
    if model not in _CASE_OWNED:
        raise TypeError(f"{model.__name__} is not case-owned")
    row = db.execute(
        select(model, Case)
        .join(Case, Case.id == model.case_id)  # type: ignore[attr-defined]
        .where(model.id == obj_id, Case.organization_id == user.organization_id)  # type: ignore[attr-defined]
    ).first()
    if row is None:
        raise not_found(f"{model.__name__} not found")
    return row[0], row[1]


def get_run(db: Session, user: User, run_id: str) -> ReconciliationRun:
    run = db.scalar(
        select(ReconciliationRun).where(
            ReconciliationRun.id == run_id, ReconciliationRun.organization_id == user.organization_id
        )
    )
    if run is None:
        raise not_found("Reconciliation run not found")
    return run


def get_result(db: Session, user: User, result_id: str) -> tuple[ReconciliationResult, ReconciliationRun]:
    row = db.execute(
        select(ReconciliationResult, ReconciliationRun)
        .join(ReconciliationRun, ReconciliationRun.id == ReconciliationResult.run_id)
        .where(ReconciliationResult.id == result_id, ReconciliationRun.organization_id == user.organization_id)
    ).first()
    if row is None:
        raise not_found("Reconciliation result not found")
    return row[0], row[1]


def get_pages(db: Session, document: EvidenceDocument) -> list[DocumentPage]:
    return list(
        db.scalars(
            select(DocumentPage).where(DocumentPage.document_id == document.id).order_by(DocumentPage.page_number)
        )
    )


def get_referral(db: Session, user: User, referral_id: str) -> Referral:
    referral = db.scalar(
        select(Referral).where(Referral.id == referral_id, Referral.organization_id == user.organization_id)
    )
    if referral is None:
        raise not_found("Referral not found")
    return referral


def get_org_user(db: Session, user: User, user_id: str) -> User:
    other = db.scalar(select(User).where(User.id == user_id, User.organization_id == user.organization_id))
    if other is None:
        raise not_found("User not found")
    return other
