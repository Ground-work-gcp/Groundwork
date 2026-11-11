"""Discrepancy detection: the inconsistencies between legal, ownership and financial records.

Every finding says what was detected, which records support it (document, page, fact),
how confident the underlying evidence is, what is uncertain, and whether it needs a
professional's review. Findings are idempotent by fingerprint: re-running detection
updates what is still true, keeps human decisions on findings that persist, and closes
open findings the evidence no longer supports (and says so in their note).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.evidence import names_match, normalize_address, street_part
from app.domain.ownership import analyze
from app.domain.reconciliation.engine.money import to_usd
from app.domain.reconciliation.service import latest_run, results_of
from app.infrastructure.database.models import (
    Case,
    EvidenceDocument,
    ExtractedFact,
    FinancialObligation,
    OwnershipEvent,
    Party,
    Property,
    RiskFinding,
    Transaction,
    utcnow,
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class Candidate:
    finding_type: str
    severity: str
    title: str
    description: str
    key: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.8
    uncertainty: str = ""
    professional: bool = True


class Evidence:
    """Builds evidence references that point at a document page and a fact."""

    def __init__(self, db: Session, case: Case):
        self.documents = {d.id: d for d in db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id))}

    def fact(self, fact: ExtractedFact, label: str = "") -> dict[str, Any]:
        document = self.documents.get(fact.document_id or "")
        return {
            "document_id": fact.document_id,
            "filename": document.filename if document else None,
            "page": fact.page_number,
            "fact_id": fact.id,
            "snippet": fact.snippet,
            "label": label or fact.fact_type.replace("_", " "),
            "confidence": fact.confidence,
            "reviewed": fact.status in ("confirmed", "corrected"),
        }

    def document(self, document_id: str | None, page: int | None, label: str, snippet: str = "") -> dict[str, Any]:
        document = self.documents.get(document_id or "")
        return {"document_id": document_id, "filename": document.filename if document else None, "page": page, "fact_id": None, "snippet": snippet, "label": label}


def _confidence(refs: list[dict[str, Any]], default: float = 0.8) -> float:
    values = [r["confidence"] for r in refs if isinstance(r.get("confidence"), int | float)]
    return round(min(values), 2) if values else default


def detect(db: Session, case: Case) -> list[Candidate]:
    ev = Evidence(db, case)
    facts = [f for f in db.scalars(select(ExtractedFact).where(ExtractedFact.case_id == case.id)) if f.status != "rejected"]
    by_type: dict[str, list[ExtractedFact]] = {}
    for fact in facts:
        by_type.setdefault(fact.fact_type, []).append(fact)
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    out: list[Candidate] = []

    def norm(f: ExtractedFact) -> str:
        return f.corrected_value if f.status == "corrected" and f.corrected_value else f.normalized_value

    # 1 · parcel identifiers that disagree
    parcels: dict[str, list[ExtractedFact]] = {}
    for fact in by_type.get("parcel_number", []):
        parcels.setdefault(norm(fact), []).append(fact)
    asserted = prop.parcel_number_normalized if prop else ""
    distinct = set(parcels) | ({asserted} if asserted else set())
    if len(distinct) > 1:
        refs = [ev.fact(f, f"parcel number {f.value}") for group in parcels.values() for f in group]
        listed = ", ".join(sorted({f.value for group in parcels.values() for f in group} | ({prop.parcel_number} if prop and prop.parcel_number else set())))
        out.append(Candidate(
            "parcel_id_conflict", "high", "Parcel identifiers do not agree",
            f"The evidence identifies the property by {len(distinct)} different parcel numbers: {listed}. "
            "A transposed or outdated parcel number can attach a tax bill, lien or deed to the wrong land.",
            "parcels:" + "|".join(sorted(distinct)), refs, _confidence(refs),
            "Parcel numbers are sometimes reissued after a split or re-plat; the assessor's history is needed to tell a typo from a re-numbering.",
        ))

    # 2 · addresses that disagree on the street
    streets: dict[str, list[ExtractedFact]] = {}
    for fact in by_type.get("property_address", []):
        streets.setdefault(street_part(normalize_address(fact.value)), []).append(fact)
    if prop and prop.address_line:
        streets.setdefault(street_part(normalize_address(prop.address_line)), [])
    if len([k for k in streets if k]) > 1:
        refs = [ev.fact(f, f"address {f.value}") for group in streets.values() for f in group]
        out.append(Candidate(
            "address_conflict", "medium", "Property addresses disagree",
            "Documents give different street addresses for the property: " + "; ".join(sorted(k for k in streets if k)) + ".",
            "addresses:" + "|".join(sorted(k for k in streets if k)), refs, _confidence(refs),
            "Rural routes are often renamed for 911 addressing; confirm against the parcel map before treating this as a different property.",
            professional=False,
        ))

    # 3 · the chain of title
    chain = analyze(db, case)
    events = {e.id: e for e in db.scalars(select(OwnershipEvent).where(OwnershipEvent.case_id == case.id))}
    facts_by_id = {f.id: f for f in facts}

    def event_refs(event_id: str | None) -> list[dict[str, Any]]:
        event = events.get(event_id or "")
        if event is None:
            return []
        refs = [ev.fact(facts_by_id[i]) for i in event.fact_ids if i in facts_by_id]
        return refs or [ev.document(event.document_id, event.page_number, event.event_type.replace("_", " "))]

    for issue in chain["issues"]:
        kind = issue["kind"]
        refs = event_refs(issue.get("event_id"))
        if kind == "broken_chain":
            out.append(Candidate(
                "broken_chain_of_title", "high", "Break in the chain of title", issue["message"] + ".",
                f"chain:{issue['event_id']}", refs, _confidence(refs),
                "An unrecorded deed, an heirship determination or a corrective instrument could close this gap; none is in the evidence.",
            ))
        elif kind == "conveyance_by_decedent":
            out.append(Candidate(
                "conveyance_by_decedent", "critical", "Conveyance signed in the name of a deceased person", issue["message"] + ".",
                f"decedent-grantor:{issue['event_id']}", refs, _confidence(refs),
                "Dates of death and recording dates come from extraction and should be confirmed against the certified records.",
            ))
        elif kind == "assessed_owner_mismatch":
            out.append(Candidate(
                "owner_name_conflict", "high", "Tax roll owner differs from the deeds", issue["message"] + ".",
                f"assessed:{issue['event_id']}", refs, _confidence(refs),
                "Assessors update ownership on their own schedule; a lag is common, but a different name can also mean an unrecorded transfer.",
            ))
        elif kind == "unprobated_estate":
            decedent = issue["decedent"]
            named = [f for f in by_type.get("decedent", []) if names_match(f.value, decedent)]
            their_documents = {f.document_id for f in named}
            death_refs = [ev.fact(f) for f in named] + [
                ev.fact(f) for f in by_type.get("date_of_death", []) if f.document_id in their_documents
            ]
            out.append(Candidate(
                "unprobated_estate", "high", f"Title remains in the name of a decedent ({decedent})",
                issue["message"] + ". Without probate or a determination of heirship the heirs hold fractional, unrecorded interests (heirs' property).",
                f"unprobated:{normalize_address(decedent)}", death_refs, _confidence(death_refs),
                "A probate filing in another county, or a small-estate affidavit, may exist but is not in this case's evidence.",
            ))
        elif kind == "unprobated_will":
            out.append(Candidate(
                "unprobated_will", "high", "A will is in evidence but was never probated", issue["message"] + ".",
                f"will:{issue['event_id']}", refs, _confidence(refs),
                "The will may have been probated in another county, or the estate may qualify for a small-estate procedure.",
            ))
        elif kind == "timeline_gap":
            out.append(Candidate(
                "evidence_gap", "medium", f"{issue['years']}-year gap in the ownership timeline", issue["message"] + ".",
                f"gap:{issue['event_id']}", refs, _confidence(refs),
                "Intervening instruments may exist at the recorder's office; this reflects only what was uploaded.", professional=False,
            ))

    # 4 · an owner of record who is deceased (tax roll) — the classic heirs'-property signal
    deceased = [p for p in db.scalars(select(Party).where(Party.case_id == case.id)) if p.is_deceased]
    for fact in by_type.get("owner_of_record", []):
        match = next((p for p in deceased if names_match(p.name, fact.value)), None)
        if match is None:
            continue
        refs = [ev.fact(fact, "owner of record")] + [ev.fact(f) for f in by_type.get("decedent", []) if names_match(f.value, match.name)]
        out.append(Candidate(
            "deceased_owner_of_record", "high", f"Records still list a deceased owner ({match.name})",
            f"{fact.value} appears as owner of record although {match.name} is recorded as deceased"
            + (f" on {match.date_of_death}" if match.date_of_death else "") + ". Tax notices may not be reaching anyone with authority to act.",
            f"deceased-owner:{fact.id}", refs, _confidence(refs),
            "Name matching is by normalised tokens; a different person with the same name is possible.",
        ))

    # 5 · financial reconciliation outcomes
    run = latest_run(db, case)
    if run is not None:
        obligations = {o.id: o for o in db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id))}
        transactions = {t.id: t for t in db.scalars(select(Transaction).where(Transaction.case_id == case.id))}
        for result in results_of(db, run):
            if result.review_status == "rejected" and result.outcome != "EXCEPTION":
                continue
            obligation = obligations.get(result.left_ids[0]) if result.left_ids else None
            refs: list[dict[str, Any]] = []
            if obligation is not None:
                refs += [ev.fact(facts_by_id[i]) for i in obligation.fact_ids if i in facts_by_id] or [
                    ev.document(obligation.document_id, obligation.page_number, f"{obligation.obligation_type} record")
                ]
            refs += [
                {"document_id": None, "filename": None, "page": None, "fact_id": None, "transaction_id": t, "label": "payment", "snippet": transactions[t].description}
                for t in result.right_ids if t in transactions
            ]
            refs.append({"document_id": None, "filename": None, "page": None, "fact_id": None, "reconciliation_result_id": result.id, "label": result.rule, "snippet": " · ".join(result.evidence[:2])})
            label = f"{obligation.obligation_type.replace('_', ' ')} {obligation.reference}".strip() if obligation else "payment"
            tax = obligation is not None and obligation.obligation_type == "property_tax"
            if result.rule == "T4_UNPAID_OBLIGATION":
                out.append(Candidate(
                    "unreconciled_obligation", "high" if tax else "medium", f"No payment found for {label}",
                    f"{to_usd(result.amount_cents)} is recorded as owed and no payment in the financial records ties to it."
                    + (" Unpaid property taxes can lead to a tax sale." if tax else ""),
                    f"recon:{result.rule}:{obligation.id if obligation else result.id}", refs, 0.9,
                    "Payments made from accounts not uploaded to this case would not appear here.",
                ))
            elif result.rule == "T2_UNDERPAID":
                out.append(Candidate(
                    "unreconciled_obligation", "high" if tax else "medium", f"{label} is only partly paid",
                    " ".join(result.evidence) + ".", f"recon:{result.rule}:{obligation.id if obligation else result.id}", refs, 0.9,
                    "Penalties and interest are not modelled; the true payoff may be higher.",
                ))
            elif result.rule == "T2_AMOUNT_MISMATCH":
                out.append(Candidate(
                    "amount_mismatch", "medium", f"Payment amounts do not match {label}", " ".join(result.evidence) + ".",
                    f"recon:{result.rule}:{obligation.id if obligation else result.id}", refs, 0.85,
                    "Either the bill, the payment record or the extraction of an amount is wrong.",
                ))
            elif result.rule == "T1_PAYER_NOT_BILLED_PARTY":
                out.append(Candidate(
                    "third_party_payer", "medium", f"{label} was paid by someone other than the billed owner",
                    " ".join(result.evidence) + ".", f"recon:{result.rule}:{':'.join(result.left_ids + result.right_ids)}", refs, 0.85,
                    "The payer may be an heir paying on the estate's behalf; their contribution can matter in partition or quiet-title proceedings.",
                ))
            elif result.rule == "T2_ESCALATION_CANDIDATES" and result.review_status == "open":
                out.append(Candidate(
                    "unverified_payment_match", "low", f"A payment may belong to {label}", " ".join(result.evidence[:3]) + ".",
                    f"recon:{result.rule}:{':'.join(result.right_ids)}", refs, 0.3,
                    "The amounts are close but not equal and nothing names the obligation; a person must confirm or reject the link.",
                    professional=False,
                ))
            elif result.rule == "T4_UNEXPLAINED_PAYMENT":
                out.append(Candidate(
                    "unexplained_payment", "low", "A payment matches no recorded obligation", " ".join(result.evidence) + ".",
                    f"recon:{result.rule}:{':'.join(result.right_ids)}", refs, 0.9,
                    "It may pay an obligation whose record has not been uploaded.", professional=False,
                ))

    # 6 · conflicting amounts for the same lien across documents
    liens: dict[str, set[str]] = {}
    lien_facts: dict[str, list[ExtractedFact]] = {}
    documents_by_ref: dict[str, str] = {}
    for fact in by_type.get("lien_reference", []) + [f for f in by_type.get("instrument_number", []) if ev.documents.get(f.document_id or "") and ev.documents[f.document_id].doc_type == "lien"]:
        documents_by_ref[fact.document_id or ""] = norm(fact)
    for fact in by_type.get("lien_amount", []):
        reference = documents_by_ref.get(fact.document_id or "")
        if reference:
            liens.setdefault(reference, set()).add(norm(fact))
            lien_facts.setdefault(reference, []).append(fact)
    for reference, amounts in liens.items():
        if len(amounts) > 1:
            refs = [ev.fact(f) for f in lien_facts[reference]]
            out.append(Candidate(
                "lien_amount_conflict", "medium", f"Lien {reference} is recorded with different amounts",
                "Amounts: " + ", ".join(to_usd(int(a)) for a in sorted(amounts)) + ".", f"lien:{reference}", refs, _confidence(refs),
                "A later notice may include accrued interest; confirm the payoff with the lienholder.",
            ))

    # 7 · evidence that could not be read
    for document in ev.documents.values():
        if document.status in ("needs_ocr", "failed"):
            out.append(Candidate(
                "unreadable_evidence", "medium", f"{document.filename} could not be read",
                document.extraction_error or "No text could be extracted, so nothing in it has been checked.",
                f"unreadable:{document.id}", [ev.document(document.id, None, "document")], 1.0,
                "Its contents are unknown to every other check.", professional=False,
            ))

    # 8 · low-confidence facts that findings rely on and nobody has reviewed
    relied = {r.get("fact_id") for c in out for r in c.evidence if r.get("fact_id")}
    weak = [f for f in facts if f.id in relied and f.status == "unreviewed" and f.confidence < 0.7]
    if weak:
        refs = [ev.fact(f) for f in weak]
        out.append(Candidate(
            "unreviewed_low_confidence_facts", "low", f"{len(weak)} finding(s) rest on low-confidence, unreviewed facts",
            "These facts were extracted with confidence below 0.70 and support other findings. Confirm or correct them.",
            "weak:" + "|".join(sorted(f.id for f in weak)), refs, _confidence(refs), professional=False,
        ))
    return out


def run_detection(db: Session, case: Case) -> dict[str, Any]:
    from app.domain.land.service import finding_candidates

    # Land-record concordance issues (rural India) are findings like any other.
    candidates = detect(db, case) + finding_candidates(db, case)
    now = utcnow()
    existing = {f.fingerprint: f for f in db.scalars(select(RiskFinding).where(RiskFinding.case_id == case.id))}
    seen: set[str] = set()
    created = updated = closed = 0
    for candidate in candidates:
        fingerprint = hashlib.sha256(f"{candidate.finding_type}|{candidate.key}".encode()).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        finding = existing.get(fingerprint)
        if finding is None:
            db.add(RiskFinding(
                case_id=case.id, finding_type=candidate.finding_type, severity=candidate.severity, title=candidate.title,
                description=candidate.description, evidence_refs=candidate.evidence, confidence=candidate.confidence,
                uncertainty=candidate.uncertainty, requires_professional_review=candidate.professional,
                fingerprint=fingerprint, last_seen_at=now,
            ))
            created += 1
        else:
            finding.title, finding.description, finding.severity = candidate.title, candidate.description, candidate.severity
            finding.evidence_refs, finding.confidence, finding.uncertainty = candidate.evidence, candidate.confidence, candidate.uncertainty
            finding.last_seen_at = now
            updated += 1
    for fingerprint, finding in existing.items():
        if fingerprint not in seen and finding.status in ("open", "acknowledged"):
            finding.status = "resolved"
            finding.status_note = f"No longer supported by the evidence when detection re-ran on {now.date().isoformat()}."
            closed += 1
    case.detection_ran_at = now
    db.flush()
    return {"created": created, "updated": updated, "closed": closed, "total": len(seen)}
