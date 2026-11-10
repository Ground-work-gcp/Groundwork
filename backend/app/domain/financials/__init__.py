"""Financial obligations and payments attached to a case.

Obligations can be entered by hand or **derived from evidence**: a tax bill's amount due,
bill number and due date, or a lien's amount and holder — each derived obligation keeps the
fact ids and page it came from. Payments arrive by hand or as a CSV, ingested with Magpie's
rule that a row which cannot be parsed becomes a rejection record, never a silent drop.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.reconciliation.engine.ingest import Reader, ingest_file
from app.infrastructure.database.models import (
    Case,
    EvidenceDocument,
    ExtractedFact,
    FinancialObligation,
    Property,
    Transaction,
)

OBLIGATION_TYPES = ("property_tax", "lien", "mortgage", "judgment", "hoa", "other")


def _facts(db: Session, document_id: str) -> dict[str, list[ExtractedFact]]:
    out: dict[str, list[ExtractedFact]] = {}
    for fact in db.scalars(
        select(ExtractedFact).where(ExtractedFact.document_id == document_id, ExtractedFact.status != "rejected")
    ):
        out.setdefault(fact.fact_type, []).append(fact)
    return out


def _first(facts: dict[str, list[ExtractedFact]], *types: str) -> ExtractedFact | None:
    for fact_type in types:
        if facts.get(fact_type):
            return max(facts[fact_type], key=lambda f: f.confidence)
    return None


def _norm(fact: ExtractedFact | None) -> str:
    if fact is None:
        return ""
    if fact.status == "corrected" and fact.corrected_value:
        return fact.corrected_value
    return fact.normalized_value


def derive_obligations(db: Session, case: Case) -> list[FinancialObligation]:
    """Create obligations from tax and lien documents not already derived."""
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    existing = {
        o.document_id for o in db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id)) if o.document_id
    }
    created: list[FinancialObligation] = []
    documents = db.scalars(
        select(EvidenceDocument).where(
            EvidenceDocument.case_id == case.id,
            EvidenceDocument.doc_type.in_(("tax_record", "lien")),
            EvidenceDocument.status == "extracted",
        )
    )
    for document in documents:
        if document.id in existing:
            continue
        facts = _facts(db, document.id)
        if document.doc_type == "tax_record":
            amount = _first(facts, "amount_due")
            if amount is None:
                continue
            reference = _first(facts, "bill_number", "instrument_number")
            due = _first(facts, "due_date", "document_date")
            year = _first(facts, "tax_year")
            debtor = _first(facts, "owner_of_record")
            creditor = _first(facts, "taxing_authority")
            used = [f for f in (amount, reference, due, year, debtor, creditor) if f is not None]
            obligation = FinancialObligation(
                case_id=case.id,
                property_id=prop.id if prop else None,
                obligation_type="property_tax",
                creditor=creditor.value if creditor else "",
                debtor_name=debtor.value if debtor else "",
                reference=reference.value if reference else "",
                tax_year=int(_norm(year)) if year and _norm(year).isdigit() else None,
                amount_cents=int(_norm(amount)),
                due_date=_norm(due) or None,
                document_id=document.id,
                page_number=amount.page_number,
                fact_ids=[f.id for f in used],
                source="evidence",
            )
        else:
            amount = _first(facts, "lien_amount", "amount_due")
            if amount is None:
                continue
            reference = _first(facts, "lien_reference", "instrument_number")
            recorded = _first(facts, "recording_date", "document_date")
            holder = _first(facts, "lien_holder")
            debtor = _first(facts, "lien_debtor", "owner_of_record")
            used = [f for f in (amount, reference, recorded, holder, debtor) if f is not None]
            obligation = FinancialObligation(
                case_id=case.id,
                property_id=prop.id if prop else None,
                obligation_type="lien",
                creditor=holder.value if holder else "",
                debtor_name=debtor.value if debtor else "",
                reference=reference.value if reference else "",
                amount_cents=int(_norm(amount)),
                assessed_date=_norm(recorded) or None,
                due_date=_norm(recorded) or None,
                document_id=document.id,
                page_number=amount.page_number,
                fact_ids=[f.id for f in used],
                source="evidence",
            )
        db.add(obligation)
        created.append(obligation)
    db.flush()
    return created


def import_transactions(
    db: Session, case: Case, text: str, *, date_format: str = "ISO", payer: str = ""
) -> dict[str, Any]:
    """CSV with columns ``date, amount`` and optional ``description, reference, payer``."""
    batch = str(uuid.uuid4())

    def decode(read: Reader, _errors: list[str]) -> Transaction:
        return Transaction(
            id=str(uuid.uuid4()),
            case_id=case.id,
            txn_date=read.date("date", date_format),  # type: ignore[arg-type]
            amount_cents=read.minor("amount"),
            description=read.text("description", required=False),
            reference=read.text("reference", required=False),
            payer=read.text("payer", required=False) or payer,
            source="csv_import",
            import_batch=batch,
        )

    records, rejections, stat = ingest_file("transactions.csv", text, ("date", "amount"), decode)
    for record in records:
        db.add(record)
    db.flush()
    return {
        "import_batch": batch,
        "imported": len(records),
        "rejections": [r.to_dict() for r in rejections],
        "file": stat.to_dict(),
    }
