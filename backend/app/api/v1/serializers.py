"""Model → JSON. One shape per entity, shared by every router (the API contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.security import PERMISSIONS, has_permission
from app.domain.workflows import confirmation_phrase
from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    CaseQuestion,
    Comment,
    Decision,
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


def ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def user(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "is_platform_admin": u.is_platform_admin,
        "organization_id": u.organization_id,
        "created_at": ts(u.created_at),
        "last_login_at": ts(u.last_login_at),
    }


def me(u: User) -> dict[str, Any]:
    return {
        **user(u),
        "organization": {"id": u.organization.id, "name": u.organization.name},
        "permissions": sorted(p for p in PERMISSIONS if has_permission(u.role, p)),
    }


def case(c: Case) -> dict[str, Any]:
    return {
        "id": c.id,
        "reference": c.reference,
        "kind": c.kind,
        "title": c.title,
        "description": c.description,
        "stage": c.stage,
        "status": c.status,
        "resolution_summary": c.resolution_summary,
        "created_by_id": c.created_by_id,
        "assigned_to_id": c.assigned_to_id,
        "detection_ran_at": ts(c.detection_ran_at),
        "created_at": ts(c.created_at),
        "updated_at": ts(c.updated_at),
        "resolved_at": ts(c.resolved_at),
    }


def prop(p: Property) -> dict[str, Any]:
    return {
        "id": p.id,
        "case_id": p.case_id,
        "address_line": p.address_line,
        "city": p.city,
        "county": p.county,
        "state": p.state,
        "postal_code": p.postal_code,
        "parcel_number": p.parcel_number,
        "parcel_number_normalized": p.parcel_number_normalized,
        "legal_description": p.legal_description,
    }


def party(p: Party) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "party_type": p.party_type,
        "roles": p.roles,
        "is_deceased": p.is_deceased,
        "date_of_death": p.date_of_death,
        "notes": p.notes,
        "source": p.source,
    }


def document(d: EvidenceDocument, fact_count: int | None = None) -> dict[str, Any]:
    return {
        "id": d.id,
        "case_id": d.case_id,
        "filename": d.filename,
        "content_type": d.content_type,
        "size_bytes": d.size_bytes,
        "sha256": d.sha256,
        "storage_uri": d.storage_uri,
        "doc_type": d.doc_type,
        "doc_type_source": d.doc_type_source,
        "status": d.status,
        "page_count": d.page_count,
        "extraction_engine": d.extraction_engine,
        "extraction_error": d.extraction_error,
        "pii_summary": d.pii_summary,
        "ai_extraction": d.ai_extraction_log,
        "uploaded_by_id": d.uploaded_by_id,
        "created_at": ts(d.created_at),
        **({"fact_count": fact_count} if fact_count is not None else {}),
    }


def page(p: DocumentPage) -> dict[str, Any]:
    return {"page_number": p.page_number, "text": p.text, "confidence": p.confidence}


def fact(f: ExtractedFact) -> dict[str, Any]:
    return {
        "id": f.id,
        "case_id": f.case_id,
        "document_id": f.document_id,
        "page_number": f.page_number,
        "fact_type": f.fact_type,
        "value": f.value,
        "normalized_value": f.normalized_value,
        "effective_value": f.effective_value,
        "snippet": f.snippet,
        "confidence": f.confidence,
        "extractor": f.extractor,
        "status": f.status,
        "corrected_value": f.corrected_value,
        "review_note": f.review_note,
        "reviewed_by_id": f.reviewed_by_id,
        "reviewed_at": ts(f.reviewed_at),
    }


def event(e: OwnershipEvent, annotation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": e.id,
        "event_type": e.event_type,
        "event_date": e.event_date,
        "grantors": e.grantors,
        "grantees": e.grantees,
        "instrument": e.instrument,
        "document_id": e.document_id,
        "page_number": e.page_number,
        "fact_ids": e.fact_ids,
        "confidence": e.confidence,
        "source": e.source,
        "notes": e.notes,
        "chain_status": (annotation or {}).get("chain_status", "ok"),
        "chain_notes": (annotation or {}).get("chain_notes", []),
    }


def obligation(o: FinancialObligation) -> dict[str, Any]:
    return {
        "id": o.id,
        "obligation_type": o.obligation_type,
        "creditor": o.creditor,
        "debtor_name": o.debtor_name,
        "reference": o.reference,
        "tax_year": o.tax_year,
        "amount_cents": o.amount_cents,
        "assessed_date": o.assessed_date,
        "due_date": o.due_date,
        "status": o.status,
        "document_id": o.document_id,
        "page_number": o.page_number,
        "fact_ids": o.fact_ids,
        "source": o.source,
    }


def transaction(t: Transaction) -> dict[str, Any]:
    return {
        "id": t.id,
        "txn_date": t.txn_date,
        "description": t.description,
        "reference": t.reference,
        "amount_cents": t.amount_cents,
        "payer": t.payer,
        "source": t.source,
        "import_batch": t.import_batch,
    }


def run(r: ReconciliationRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "case_id": r.case_id,
        "kind": r.kind,
        "stats": r.stats,
        "tolerances": r.tolerances,
        "summary": r.summary,
        "created_by_id": r.created_by_id,
        "created_at": ts(r.created_at),
    }


def result(r: ReconciliationResult) -> dict[str, Any]:
    return {
        "id": r.id,
        "seq": r.seq,
        "lane": r.lane,
        "tier": r.tier,
        "rule": r.rule,
        "outcome": r.outcome,
        "confidence": r.confidence,
        "left": r.left_ids,
        "right": r.right_ids,
        "inputs": r.inputs,
        "evidence": r.evidence,
        "class": r.failure_class,
        "amount_cents": r.amount_cents,
        "cap_hit": r.cap_hit,
        "review_status": r.review_status,
        "reviewed_by_id": r.reviewed_by_id,
        "reviewed_at": ts(r.reviewed_at),
    }


def finding(f: RiskFinding, case_ref: str | None = None) -> dict[str, Any]:
    return {
        "id": f.id,
        "case_id": f.case_id,
        **({"case_reference": case_ref} if case_ref else {}),
        "finding_type": f.finding_type,
        "severity": f.severity,
        "title": f.title,
        "description": f.description,
        "evidence": f.evidence_refs,
        "confidence": f.confidence,
        "uncertainty": f.uncertainty,
        "requires_professional_review": f.requires_professional_review,
        "status": f.status,
        "status_note": f.status_note,
        "source": f.source,
        "detected_at": ts(f.created_at),
        "last_seen_at": ts(f.last_seen_at),
        "reviewed_by_id": f.reviewed_by_id,
        "reviewed_at": ts(f.reviewed_at),
    }


def task(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "kind": t.kind,
        "status": t.status,
        "assignee_id": t.assignee_id,
        "due_date": t.due_date,
        "finding_id": t.finding_id,
        "created_by_id": t.created_by_id,
        "created_at": ts(t.created_at),
        "completed_at": ts(t.completed_at),
    }


def comment(c: Comment) -> dict[str, Any]:
    return {"id": c.id, "entity_type": c.entity_type, "entity_id": c.entity_id, "author_id": c.author_id, "body": c.body, "created_at": ts(c.created_at)}


def decision(d: Decision) -> dict[str, Any]:
    return {"id": d.id, "title": d.title, "decision": d.decision, "rationale": d.rationale, "decided_by_id": d.decided_by_id, "related": d.related, "created_at": ts(d.created_at)}


def action(a: WorkflowAction, case_ref: str | None = None, summary: str | None = None) -> dict[str, Any]:
    return {
        "id": a.id,
        "case_id": a.case_id,
        **({"case_reference": case_ref} if case_ref else {}),
        "seq": a.seq,
        "action": a.action,
        "params": a.params,
        "params_hash": a.params_hash,
        "rationale": a.rationale,
        "summary": summary,
        "proposed_by_id": a.proposed_by_id,
        "proposed_by_kind": a.proposed_by_kind,
        "status": a.status,
        "confirmation_phrase": confirmation_phrase(a.seq),
        "expires_at": ts(a.expires_at),
        "result": a.result,
        "created_at": ts(a.created_at),
        "decided_at": ts(a.decided_at),
    }


def question(q: CaseQuestion) -> dict[str, Any]:
    return {
        "id": q.id,
        "question": q.question,
        "answer": q.answer,
        "citations": q.citations,
        "mode": q.mode,
        "verified": q.verified,
        "asked_by_id": q.asked_by_id,
        "created_at": ts(q.created_at),
    }


def referral(r: Referral) -> dict[str, Any]:
    return {
        "id": r.id,
        "full_name": r.full_name,
        "email": r.email,
        "address": r.address,
        "description": r.description,
        "verdict": r.verdict,
        "reason": r.reason,
        "case_id": r.case_id,
        "submitted_by_id": r.submitted_by_id,
        "created_at": ts(r.created_at),
    }


def audit_event(e: AuditEvent, names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": e.id,
        "seq": e.seq,
        "case_id": e.case_id,
        "actor_id": e.actor_id,
        "actor_name": (names or {}).get(e.actor_id or "", None),
        "actor_kind": e.actor_kind,
        "event": e.event,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "detail": e.detail,
        "occurred_at": e.occurred_at,
        "hash": e.hash,
        "prev_hash": e.prev_hash,
    }
