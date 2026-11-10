"""Financials: obligations (tax, liens), payments, CSV import, and the case position."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.evidence import parse_date_text
from app.domain.financials import OBLIGATION_TYPES, derive_obligations, import_transactions
from app.domain.reconciliation.service import latest_run, summary
from app.infrastructure.database.models import FinancialObligation, Property, Transaction, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["financials"])


class ObligationIn(BaseModel):
    obligation_type: str
    amount_cents: int = Field(gt=0)
    reference: str = ""
    creditor: str = ""
    debtor_name: str = ""
    tax_year: int | None = None
    due_date: str | None = None
    assessed_date: str | None = None
    status: str | None = None


class TransactionIn(BaseModel):
    txn_date: str
    amount_cents: int
    description: str = ""
    reference: str = ""
    payer: str = ""


def _date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    parsed = parse_date_text(value)
    if not parsed:
        raise HTTPException(422, detail=f"{field} is not a date")
    return parsed


@router.get("/cases/{case_id}/financials")
def financials(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    obligations = db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id).order_by(FinancialObligation.due_date))
    transactions = db.scalars(select(Transaction).where(Transaction.case_id == case.id).order_by(Transaction.txn_date))
    run = latest_run(db, case)
    return {
        "obligations": [s.obligation(o) for o in obligations],
        "transactions": [s.transaction(t) for t in transactions],
        "summary": summary(db, case, run),
        "latest_run_id": run.id if run else None,
    }


@router.post("/cases/{case_id}/obligations", status_code=201)
def add_obligation(case_id: str, body: ObligationIn, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.obligation_type not in OBLIGATION_TYPES:
        raise HTTPException(422, detail=f"obligation_type must be one of {', '.join(OBLIGATION_TYPES)}")
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    row = FinancialObligation(
        case_id=case.id, property_id=prop.id if prop else None, obligation_type=body.obligation_type, amount_cents=body.amount_cents,
        reference=body.reference.strip(), creditor=body.creditor.strip(), debtor_name=body.debtor_name.strip(), tax_year=body.tax_year,
        due_date=_date(body.due_date, "due_date"), assessed_date=_date(body.assessed_date, "assessed_date"), source="manual",
    )
    db.add(row)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="obligation.added", actor=user, case_id=case.id, entity_type="obligation", entity_id=row.id, detail=s.obligation(row))
    return {"obligation": s.obligation(row)}


@router.post("/cases/{case_id}/obligations/derive")
def derive(case_id: str, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    created = derive_obligations(db, case)
    audit.record(
        db, organization_id=case.organization_id, event="obligations.derived_from_evidence", actor=user, case_id=case.id, entity_type="case", entity_id=case.id,
        detail={"created": [{"id": o.id, "type": o.obligation_type, "reference": o.reference, "amount_cents": o.amount_cents, "document_id": o.document_id} for o in created]},
    )
    return {"created": [s.obligation(o) for o in created]}


@router.patch("/obligations/{obligation_id}")
def patch_obligation(obligation_id: str, body: ObligationIn, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    row, case = get_case_owned(db, user, FinancialObligation, obligation_id)
    before = s.obligation(row)
    row.obligation_type, row.amount_cents = body.obligation_type, body.amount_cents
    row.reference, row.creditor, row.debtor_name, row.tax_year = body.reference, body.creditor, body.debtor_name, body.tax_year
    row.due_date, row.assessed_date = _date(body.due_date, "due_date"), _date(body.assessed_date, "assessed_date")
    if body.status in ("open", "partial", "disputed"):
        row.status = body.status  # "paid" only through reconciliation or an approved action
    audit.record(db, organization_id=case.organization_id, event="obligation.updated", actor=user, case_id=case.id, entity_type="obligation", entity_id=row.id, detail={"before": before, "after": s.obligation(row)})
    return {"obligation": s.obligation(row)}


@router.delete("/obligations/{obligation_id}")
def delete_obligation(obligation_id: str, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    row, case = get_case_owned(db, user, FinancialObligation, obligation_id)
    audit.record(db, organization_id=case.organization_id, event="obligation.removed", actor=user, case_id=case.id, entity_type="obligation", entity_id=row.id, detail=s.obligation(row))
    db.delete(row)
    return {"ok": True}


@router.post("/cases/{case_id}/transactions", status_code=201)
def add_transaction(case_id: str, body: TransactionIn, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    row = Transaction(
        case_id=case.id, txn_date=_date(body.txn_date, "txn_date"), amount_cents=body.amount_cents, description=body.description,
        reference=body.reference, payer=body.payer, source="manual",
    )
    db.add(row)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="transaction.added", actor=user, case_id=case.id, entity_type="transaction", entity_id=row.id, detail=s.transaction(row))
    return {"transaction": s.transaction(row)}


@router.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: str, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    row, case = get_case_owned(db, user, Transaction, transaction_id)
    audit.record(db, organization_id=case.organization_id, event="transaction.removed", actor=user, case_id=case.id, entity_type="transaction", entity_id=row.id, detail=s.transaction(row))
    db.delete(row)
    return {"ok": True}


@router.post("/cases/{case_id}/transactions/import")
async def import_csv(
    case_id: str,
    file: UploadFile = File(...),
    date_format: str = Form("ISO"),
    payer: str = Form(""),
    user: User = Depends(require("financials:write")),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    case = get_case(db, user, case_id)
    if date_format not in ("ISO", "MDY", "DMY"):
        raise HTTPException(422, detail="date_format must be ISO, MDY or DMY")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, detail="CSV imports are limited to 5 MB")
    outcome = import_transactions(db, case, raw.decode("utf-8-sig", errors="replace"), date_format=date_format, payer=payer.strip())
    audit.record(
        db, organization_id=case.organization_id, event="transactions.imported", actor=user, case_id=case.id, entity_type="case", entity_id=case.id,
        detail={"filename": file.filename, "imported": outcome["imported"], "rejected": len(outcome["rejections"]), "batch": outcome["import_batch"]},
    )
    return outcome
