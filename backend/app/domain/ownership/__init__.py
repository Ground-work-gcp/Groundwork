"""Ownership reconstruction: a chronological chain of title from extracted evidence.

Each deed, death record, probate order, will and tax assessment becomes an event that
cites its document, page and the fact ids it was built from. The chain is then *checked*,
not asserted: a conveyance by someone who was not an owner of record, an owner who died
with no transfer of their interest, a will that was never probated, an assessor's owner
who disagrees with the deeds — each is reported as an issue for human review.

This module states what the evidence shows. It does not decide who owns the property.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.evidence import is_organization, names_match, normalize_name
from app.infrastructure.database.models import (
    Case,
    EvidenceDocument,
    ExtractedFact,
    OwnershipEvent,
    Party,
    Property,
)

TRANSFERS = {"deed_transfer", "quitclaim", "probate_distribution"}
_ORDER = {"death": 0, "will": 1, "probate_distribution": 2, "deed_transfer": 3, "quitclaim": 3, "tax_assessment": 4}
GAP_YEARS = 25


def _value(fact: ExtractedFact) -> str:
    return fact.corrected_value if fact.status == "corrected" and fact.corrected_value else fact.value


def _norm(fact: ExtractedFact) -> str:
    return fact.corrected_value if fact.status == "corrected" and fact.corrected_value else fact.normalized_value


def _facts_by_document(db: Session, case_id: str) -> dict[str, dict[str, list[ExtractedFact]]]:
    out: dict[str, dict[str, list[ExtractedFact]]] = {}
    for fact in db.scalars(
        select(ExtractedFact).where(ExtractedFact.case_id == case_id, ExtractedFact.status != "rejected")
    ):
        if fact.document_id:
            out.setdefault(fact.document_id, {}).setdefault(fact.fact_type, []).append(fact)
    return out


def _upsert_party(db: Session, case_id: str, name: str, role: str, *, deceased_on: str | None = None) -> None:
    normalized = normalize_name(name)
    if not normalized:
        return
    party = db.scalar(select(Party).where(Party.case_id == case_id, Party.name_normalized == normalized))
    if party is None:
        for candidate in db.scalars(select(Party).where(Party.case_id == case_id)):
            if names_match(candidate.name, name):
                party = candidate
                break
    if party is None:
        party = Party(
            case_id=case_id,
            name=name,
            name_normalized=normalized,
            party_type="organization" if is_organization(name) else "person",
            roles=[],
            source="extracted",
        )
        db.add(party)
        db.flush()
    if role not in party.roles:
        party.roles = [*party.roles, role]
    if deceased_on:
        party.is_deceased = True
        party.date_of_death = party.date_of_death or deceased_on


def reconstruct(db: Session, case: Case) -> dict[str, Any]:
    """Rebuild the reconstructed events from evidence (manual events are kept)."""
    db.execute(delete(OwnershipEvent).where(OwnershipEvent.case_id == case.id, OwnershipEvent.source == "reconstructed"))
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    documents = {d.id: d for d in db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id))}

    for document_id, facts in _facts_by_document(db, case.id).items():
        document = documents.get(document_id)
        if document is None:
            continue

        def names(*types: str, facts: dict = facts) -> list[ExtractedFact]:
            return [f for t in types for f in facts.get(t, [])]

        def first(*types: str, facts: dict = facts) -> ExtractedFact | None:
            found = names(*types)
            return max(found, key=lambda f: f.confidence) if found else None  # noqa: B023 - f is the lambda's own parameter

        def add(event_type: str, grantors: list[ExtractedFact], grantees: list[ExtractedFact], date: ExtractedFact | None, notes: str = "", document: EvidenceDocument = document) -> None:
            used = [*grantors, *grantees, *([date] if date else [])]
            instrument = first("instrument_number", "book_page")
            if instrument:
                used.append(instrument)
            page = next((f.page_number for f in used if f.page_number), None)
            db.add(
                OwnershipEvent(
                    case_id=case.id,
                    property_id=prop.id if prop else None,
                    event_type=event_type,
                    event_date=_norm(date) if date else None,
                    grantors=[_value(f) for f in grantors],
                    grantees=[_value(f) for f in grantees],
                    instrument=_value(instrument) if instrument else "",
                    document_id=document.id,
                    page_number=page,
                    fact_ids=[f.id for f in used],
                    confidence=round(min((f.confidence for f in used), default=0.5), 2),
                    source="reconstructed",
                    notes=notes,
                )
            )

        grantors, grantees = names("grantor"), names("grantee")
        if document.doc_type == "deed" or (grantors and grantees):
            kind = first("instrument_type")
            event_type = "quitclaim" if kind and _norm(kind) == "QUITCLAIM" else "deed_transfer"
            add(event_type, grantors, grantees, first("recording_date", "document_date"))
            for f in grantors:
                _upsert_party(db, case.id, _value(f), "grantor")
            for f in grantees:
                _upsert_party(db, case.id, _value(f), "grantee")

        decedents = names("decedent")
        death = first("date_of_death")
        if decedents and (document.doc_type == "death_certificate" or death):
            add("death", decedents, [], death)
        heirs = names("heir", "devisee")
        if decedents and heirs and document.doc_type == "probate":
            add("probate_distribution", decedents, heirs, first("document_date", "recording_date"), "court-supervised transfer")
        elif decedents and heirs and document.doc_type == "will":
            add("will", decedents, heirs, first("document_date"), "a will alone does not transfer title until admitted to probate")
        for f in decedents:
            _upsert_party(db, case.id, _value(f), "decedent", deceased_on=_norm(death) if death else None)
        for f in heirs:
            _upsert_party(db, case.id, _value(f), "heir" if f.fact_type == "heir" else "devisee")

        owners = names("owner_of_record")
        if owners and document.doc_type == "tax_record":
            year = first("tax_year")
            date = first("document_date", "due_date")
            event_date = f"{_norm(year)}-01-01" if year else None
            event_owner_date = date if not year else None
            add("tax_assessment", [], owners, event_owner_date)
            if event_date:
                db.flush()
                latest = db.scalars(
                    select(OwnershipEvent).where(OwnershipEvent.document_id == document.id, OwnershipEvent.event_type == "tax_assessment")
                ).all()
                for event in latest:
                    event.event_date = event_date
            for f in owners:
                _upsert_party(db, case.id, _value(f), "owner_of_record")
        for f in names("lien_holder"):
            _upsert_party(db, case.id, _value(f), "lienholder")
        for f in names("taxing_authority"):
            _upsert_party(db, case.id, _value(f), "taxing_authority")

    db.flush()
    return analyze(db, case)


def _events(db: Session, case_id: str) -> list[OwnershipEvent]:
    events = list(db.scalars(select(OwnershipEvent).where(OwnershipEvent.case_id == case_id)))
    events.sort(key=lambda e: (e.event_date is None, e.event_date or "", _ORDER.get(e.event_type, 9)))
    return events


def analyze(db: Session, case: Case) -> dict[str, Any]:
    """Walk the chain in date order and report what does not connect."""
    events = _events(db, case.id)
    owners: list[str] = []
    deceased: dict[str, str | None] = {}
    issues: list[dict[str, Any]] = []
    annotated: list[dict[str, Any]] = []
    previous_date: str | None = None

    def is_owner(name: str) -> bool:
        return any(names_match(name, owner) for owner in owners)

    for event in events:
        status, notes = "ok", []
        if previous_date and event.event_date and event.event_type in TRANSFERS:
            gap = int(event.event_date[:4]) - int(previous_date[:4])
            if gap >= GAP_YEARS:
                issues.append({
                    "kind": "timeline_gap", "event_id": event.id, "years": gap,
                    "message": f"{gap} years pass between recorded events ({previous_date} → {event.event_date}) with no evidence of what happened in between",
                })
                notes.append(f"{gap}-year gap before this event")
        if event.event_type in TRANSFERS:
            if owners and not any(is_owner(g) for g in event.grantors):
                status = "break"
                issues.append({
                    "kind": "broken_chain", "event_id": event.id,
                    "message": f"{', '.join(event.grantors) or 'An unnamed grantor'} conveyed on {event.event_date or 'an unknown date'}, but the prior evidence shows the owners as {', '.join(owners)}",
                })
            for grantor in event.grantors:
                died = next((d for name, d in deceased.items() if names_match(name, grantor)), "absent")
                if died != "absent" and event.event_type != "probate_distribution":
                    status = "break"
                    issues.append({
                        "kind": "conveyance_by_decedent", "event_id": event.id,
                        "message": f"{grantor} is recorded as deceased{f' ({died})' if died else ''} but appears as grantor on {event.event_date or 'an undated instrument'}",
                    })
            if event.grantees:
                owners = list(event.grantees)
            if not event.grantees:
                status = "incomplete"
                notes.append("no grantee could be extracted")
        elif event.event_type == "death":
            for name in event.grantors:
                deceased[name] = event.event_date
                if is_owner(name):
                    notes.append(f"{name} was an owner of record at death")
        elif event.event_type == "will":
            probated = any(
                e.event_type == "probate_distribution" and any(names_match(g, t) for g in e.grantors for t in event.grantors)
                for e in events
            )
            if not probated:
                status = "unprobated"
                notes.append(event.notes)
                issues.append({
                    "kind": "unprobated_will", "event_id": event.id,
                    "message": f"the will of {', '.join(event.grantors)} names {', '.join(event.grantees)}, but no probate order admits it; a will alone does not transfer title",
                })
        elif event.event_type == "tax_assessment":
            if owners and not all(is_owner(name) for name in event.grantees):
                status = "conflict"
                issues.append({
                    "kind": "assessed_owner_mismatch", "event_id": event.id,
                    "message": f"the tax roll for {event.event_date[:4] if event.event_date else 'an unknown year'} assesses {', '.join(event.grantees)}, but the deeds show {', '.join(owners)}",
                })
        if event.event_date and event.event_type in TRANSFERS:
            previous_date = event.event_date
        annotated.append({"id": event.id, "chain_status": status, "chain_notes": notes})

    deceased_owners = [owner for owner in owners if any(names_match(owner, name) for name in deceased)]
    probated = {name for e in events if e.event_type == "probate_distribution" for name in e.grantors}
    for name, died in deceased.items():
        if not any(names_match(name, p) for p in probated) and any(
            names_match(name, g) for e in events if e.event_type in TRANSFERS | {"tax_assessment"} for g in e.grantees
        ):
            issues.append({
                "kind": "unprobated_estate",
                "message": f"{name} held title and died{f' on {died}' if died else ''}; no probate order or heirship determination transfers their interest",
                "decedent": name,
            })
    return {
        "events": annotated,
        "current_owners": owners,
        "deceased_current_owners": deceased_owners,
        "issues": issues,
        "event_count": len(events),
    }
