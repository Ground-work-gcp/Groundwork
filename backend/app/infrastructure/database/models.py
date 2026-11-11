"""The canonical Groundwork data model.

One **Property Resolution Case** connects a property's identity, its parties, the evidence
documents and the facts extracted from them (with page-level provenance), the ownership
history reconstructed from those facts, the financial obligations and payments, the
reconciliation results, the findings, the tasks/decisions/approvals, and the audit chain.

Conventions: string UUID primary keys; ``*_date`` columns are ISO ``YYYY-MM-DD`` strings
(the precision legal records actually carry); timestamps are timezone-aware UTC; money is
an integer count of cents (never a float — see the reconciliation engine).

Mapping from the legacy stores (``docs/migration/data-model-mapping.md``):
InHeir MongoDB ``user``/``case_details``/``case_summary``/``chat_history``/``report`` →
``users``/``cases``/``documents``+``extracted_facts``+``parties``/``case_questions``/``referrals``;
Beacon DynamoDB incidents/approvals/contracts → ``cases``+``audit_events``/``workflow_actions``+
``approvals``; Magpie Prisma recon (in-memory queue) → ``reconciliation_runs``/``_results``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Timestamped:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ── tenancy and identity ─────────────────────────────────────────────────────


class Organization(Timestamped, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Head of the tamper-evident audit chain (see domain/audit).
    audit_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audit_head: Mapped[str] = mapped_column(String(64), default="0" * 64, nullable=False)
    case_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class User(Timestamped, Base):
    __tablename__ = "users"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="paralegal", nullable=False)
    # Platform operators only (internal admin boundary: settlement-batch engine, safety).
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship()


# ── the case and the property ────────────────────────────────────────────────


class Case(Timestamped, Base):
    __tablename__ = "cases"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    # heirs_property (US title work) | rural_land (Indian land records) — decides the lifecycle and graph.
    kind: Mapped[str] = mapped_column(String(20), default="heirs_property", server_default="heirs_property", nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    stage: Mapped[str] = mapped_column(String(20), default="intake", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|resolved|closed
    resolution_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detection_ran_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("organization_id", "reference", name="uq_case_reference"),)


class Property(Timestamped, Base):
    __tablename__ = "properties"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    address_line: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    county: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    state: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    # Parcel identity: the asserted number plus its comparable form. Every *other* parcel
    # number seen in evidence lives as an ExtractedFact, which is what conflict detection reads.
    parcel_number: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    parcel_number_normalized: Mapped[str] = mapped_column(String(80), default="", index=True, nullable=False)
    legal_description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Party(Timestamped, Base):
    __tablename__ = "parties"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    party_type: Mapped[str] = mapped_column(String(20), default="person", nullable=False)  # person|organization
    roles: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    is_deceased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_of_death: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual|extracted

    __table_args__ = (UniqueConstraint("case_id", "name_normalized", name="uq_party_name"),)


# ── evidence ─────────────────────────────────────────────────────────────────


class EvidenceDocument(Timestamped, Base):
    __tablename__ = "documents"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(600), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    doc_type_source: Mapped[str] = mapped_column(String(20), default="classified", nullable=False)
    # stored | extracted | needs_ocr | failed
    status: Mapped[str] = mapped_column(String(20), default="stored", nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extraction_engine: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    extraction_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Counts by type and page only. Raw PII values are never copied out of the document.
    pii_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ai_extraction_log: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (UniqueConstraint("case_id", "sha256", name="uq_document_hash"),)


class DocumentPage(Timestamped, Base):
    __tablename__ = "document_pages"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)


class ExtractedFact(Timestamped, Base):
    __tablename__ = "extracted_facts"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer)
    fact_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    extractor: Mapped[str] = mapped_column(String(40), nullable=False)  # rules:v1 | gemini:<model> | manual
    # unreviewed | confirmed | corrected | rejected
    status: Mapped[str] = mapped_column(String(20), default="unreviewed", nullable=False)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def effective_value(self) -> str:
        return self.corrected_value if self.status == "corrected" and self.corrected_value else self.value


# ── ownership ────────────────────────────────────────────────────────────────


class OwnershipEvent(Timestamped, Base):
    __tablename__ = "ownership_events"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"))
    # deed_transfer | quitclaim | inheritance | death | probate | tax_assessment
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_date: Mapped[str | None] = mapped_column(String(10))
    grantors: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    grantees: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    instrument: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    fact_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="reconstructed", nullable=False)  # reconstructed|manual
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


# ── financials ───────────────────────────────────────────────────────────────


class FinancialObligation(Timestamped, Base):
    __tablename__ = "financial_obligations"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    property_id: Mapped[str | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"))
    # property_tax | lien | mortgage | judgment | hoa | other
    obligation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    creditor: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    debtor_name: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    tax_year: Mapped[int | None] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    assessed_date: Mapped[str | None] = mapped_column(String(10))
    due_date: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|partial|paid|disputed
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    fact_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual|evidence


class Transaction(Timestamped, Base):
    __tablename__ = "transactions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    txn_date: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # Signed cents: a payment toward an obligation is positive.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    payer: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual|csv_import
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    import_batch: Mapped[str] = mapped_column(String(36), default="", nullable=False)


class ReconciliationRun(Timestamped, Base):
    __tablename__ = "reconciliation_runs"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # property_case | settlement_batch
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tolerances: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ReconciliationResult(Timestamped, Base):
    __tablename__ = "reconciliation_results"
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    lane: Mapped[str] = mapped_column(String(40), nullable=False)
    tier: Mapped[str] = mapped_column(String(4), nullable=False)
    rule: Mapped[str] = mapped_column(String(60), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    left_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    right_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    inputs: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    failure_class: Mapped[str | None] = mapped_column(String(40))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    cap_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|accepted|rejected
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── findings and the resolution workflow ─────────────────────────────────────


class RiskFinding(Timestamped, Base):
    __tablename__ = "risk_findings"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # critical|high|medium|low|info
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # [{document_id, filename, page, fact_id, snippet, label}]
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, default="", nullable=False)
    requires_professional_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # open | acknowledged | resolved | dismissed
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    status_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="rules", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("case_id", "fingerprint", name="uq_finding_fingerprint"),)


class Task(Timestamped, Base):
    __tablename__ = "tasks"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="general", nullable=False)  # general|evidence_request|review
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|in_progress|done|cancelled
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[str | None] = mapped_column(String(10))
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("risk_findings.id", ondelete="SET NULL"))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(Timestamped, Base):
    __tablename__ = "comments"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), default="case", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowAction(Timestamped, Base):
    """A proposed change that only executes after a qualified human approves it.

    Beacon's approval ledger, adapted: one allowlisted action, exact params pinned by hash,
    an expiry, a per-case sequence number the approver must name, and single use.
    """

    __tablename__ = "workflow_actions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    proposed_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    proposed_by_kind: Mapped[str] = mapped_column(String(10), default="user", nullable=False)  # user|system
    # proposed | executed | rejected | failed | expired | withdrawn
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("case_id", "seq", name="uq_action_seq"),)


class Approval(Timestamped, Base):
    __tablename__ = "approvals"
    action_id: Mapped[str] = mapped_column(ForeignKey("workflow_actions.id", ondelete="CASCADE"), unique=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # approved | rejected
    decided_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    # The exact confirmation phrase, or the rejection reason — the record of consent.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Decision(Timestamped, Base):
    __tablename__ = "decisions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    related: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class CaseQuestion(Timestamped, Base):
    """Evidence-grounded Q&A over a case's own documents (InHeir's case chatbot, made traceable)."""

    __tablename__ = "case_questions"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    asked_by_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # gemini | extractive
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Referral(Timestamped, Base):
    """An intake referral (InHeir's property-fraud "report"), verified before it becomes a case."""

    __tablename__ = "referrals"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    submitted_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    full_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|verified|not_verified
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))


class AuditEvent(Base):
    """Append-only, hash-chained per organisation. ``occurred_at`` is the exact string hashed."""

    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # user|system|ai
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (UniqueConstraint("organization_id", "seq", name="uq_audit_seq"),)


# ── rural land records (India): parcels, records, family, valuation, credit readiness ──
#
# Groundwork prepares the case; ownership decisions stay with revenue authorities and courts.
# Areas are square metres (float, converted from the region's units with the original text kept);
# rupee amounts are whole rupees (int) — land values and fees are never quoted in paise.


class LandParcel(Timestamped, Base):
    """The plot as the family describes it: where it is, how big, and the government's guideline value."""

    __tablename__ = "land_parcels"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    region_code: Mapped[str] = mapped_column(String(8), default="TN", nullable=False)
    district: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    taluk: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    village: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    survey_number: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    survey_key: Mapped[str] = mapped_column(String(60), default="", index=True, nullable=False)
    extent_text: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    extent_sqm: Mapped[float | None] = mapped_column(Float)
    land_class: Mapped[str] = mapped_column(String(20), default="", nullable=False)  # wet|dry|garden|residential|government
    patta_number: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    guideline_inr_per_sqm: Mapped[float | None] = mapped_column(Float)
    road_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    irrigated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # self_acquired | ancestral — decides whether a notional partition applies (Hindu law).
    property_nature: Mapped[str] = mapped_column(String(20), default="self_acquired", nullable=False)
    # hindu | christian | muslim | other — the personal law the family says applies.
    personal_law: Mapped[str] = mapped_column(String(20), default="hindu", nullable=False)
    recorded_holder: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    __table_args__ = (UniqueConstraint("case_id", name="uq_land_parcel_case"),)


class LandRecord(Timestamped, Base):
    """One government or private record's view of a plot: the unit concordance compares."""

    __tablename__ = "land_records"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    # ror | cultivation | sketch | encumbrance | sale_deed | legal_heir | death_certificate | tax_receipt | field_survey | other
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    record_date: Mapped[str | None] = mapped_column(String(10))
    issuing_office: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    survey_number: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    survey_key: Mapped[str] = mapped_column(String(60), default="", index=True, nullable=False)
    extent_text: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    extent_sqm: Mapped[float | None] = mapped_column(Float)
    holders: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    # {"north": "...", "east": "...", "south": "...", "west": "..."}
    boundaries: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    land_class: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    # Free-form, typed by source: encumbrances [{kind, party, date, status}], polygon [[x, y]…], ec_from/ec_to, tax_year …
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual|extracted
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)


class FamilyMember(Timestamped, Base):
    """A person in the holder's family tree. Parents and spouses are ids of other members of the same case."""

    __tablename__ = "family_members"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    sex: Mapped[str] = mapped_column(String(10), default="unknown", nullable=False)  # male|female|unknown
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    date_of_death: Mapped[str | None] = mapped_column(String(10))
    parent_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    spouse_ids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    is_recorded_holder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lives_in_village: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    location: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # unknown | located | contacted | consented | objected | needs_info
    consent_status: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    consent_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How strong the consent is: none | online (replied to the link) | signed_letter | registered_deed.
    # Offices and lenders act on paper; an online reply is intent, not consent they can file.
    consent_evidence: Mapped[str] = mapped_column(String(20), default="none", server_default="none", nullable=False)
    consent_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    # A single-use link for a co-heir who left the village: only the SHA-256 of the token is stored.
    consent_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    consent_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LandAnalysis(Timestamped, Base):
    """A hashed snapshot of one analysis run (concordance, succession, boundaries, valuation, readiness, agent)."""

    __tablename__ = "land_analyses"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ComparableSale(Timestamped, Base):
    """A registered sale near the plot (from an EC, a sale deed, or the field). Shared across an organisation's cases."""

    __tablename__ = "comparable_sales"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    region_code: Mapped[str] = mapped_column(String(8), default="TN", nullable=False)
    district: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    taluk: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    village: Mapped[str] = mapped_column(String(120), default="", index=True, nullable=False)
    survey_number: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    sale_date: Mapped[str] = mapped_column(String(10), nullable=False)
    extent_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    consideration_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    land_class: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    distance_km: Mapped[float | None] = mapped_column(Float)
    road_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="sale_deed", nullable=False)  # encumbrance|sale_deed|field
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class CostEntry(Timestamped, Base):
    """What the family has spent, in money and days, to sort out the land — receipted or not."""

    __tablename__ = "cost_entries"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    # official_fee | document_writer | advocate | travel | lost_wages | unreceipted_payment | survey_fee | stamp_duty | informal_loan | other
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    payee_kind: Mapped[str] = mapped_column(String(30), default="", nullable=False)  # office|document_writer|advocate|middleman|lender|other
    service: Mapped[str] = mapped_column(String(40), default="", nullable=False)  # a fee-schedule service id, if any
    amount_inr: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days_spent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    has_receipt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class FieldObservation(Timestamped, Base):
    """One field visit's answers to the six field questions, for one village (and, optionally, one bank branch)."""

    __tablename__ = "field_observations"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    region_code: Mapped[str] = mapped_column(String(8), default="TN", nullable=False)
    district: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    taluk: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    village: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_on: Mapped[str] = mapped_column(String(10), nullable=False)
    observer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    households_surveyed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    households_deceased_holder: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correction_barriers: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    branch_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    lender_kind: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    branch_requirements: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    valuation_practices: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    # {"names": n, "extents": n, "boundaries": n, "survey_numbers": n}
    disagreement_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    dispute_settlers: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    dispute_days_typical: Mapped[int | None] = mapped_column(Integer)
    family_spend_inr_typical: Mapped[int | None] = mapped_column(Integer)
    family_days_typical: Mapped[int | None] = mapped_column(Integer)
    record_readers: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)  # who families pay to read their records
    record_reading_fee_inr: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class LandPassport(Timestamped, Base):
    """A lender-ready dossier, issued only after an approval, hashed and anchored in the audit chain."""

    __tablename__ = "land_passports"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|superseded|revoked
    issued_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class LandProceeding(Timestamped, Base):
    """An application the family has made (or will make) to an office: mutation, legal heir certificate, survey…

    Tracked as the office actually processes it — filed, field verification, objection window,
    hearing, order — against the indicative statutory timeline, so a stalled file is noticed and
    escalated instead of being chased by middlemen.
    """

    __tablename__ = "land_proceedings"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True, nullable=False)
    # mutation | legal_heir_certificate | encumbrance_certificate | survey_measurement | name_correction | appeal | grievance | dispute_mediation
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    office: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    application_no: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    # draft | filed | field_verification | notice_period | hearing | ordered | rejected | withdrawn
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    filed_on: Mapped[str | None] = mapped_column(String(10))
    sla_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    due_on: Mapped[str | None] = mapped_column(String(10))
    objection_until: Mapped[str | None] = mapped_column(String(10))
    hearing_on: Mapped[str | None] = mapped_column(String(10))
    decided_on: Mapped[str | None] = mapped_column(String(10))
    outcome: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("land_proceedings.id", ondelete="SET NULL"))
    escalation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    applicants: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProceedingEvent(Timestamped, Base):
    """One thing that happened on a proceeding, dated as it happened in the office (not when it was typed)."""

    __tablename__ = "proceeding_events"
    proceeding_id: Mapped[str] = mapped_column(ForeignKey("land_proceedings.id", ondelete="CASCADE"), index=True, nullable=False)
    event_date: Mapped[str] = mapped_column(String(10), nullable=False)
    # filed | acknowledged | field_inspection | notice_published | objection_received | hearing_scheduled | hearing_held |
    # document_requested | follow_up | order_passed | rejected | escalated | withdrawn | note
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    status_after: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
