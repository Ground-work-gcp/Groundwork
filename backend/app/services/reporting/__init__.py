"""The case report: one Markdown document an attorney can read, file or forward.

Rendered from the case record, never written by a model (Beacon's postmortem principle).
Every finding cites its evidence; the audit chain's verification result is stated.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit import verify_chain
from app.domain.ownership import analyze
from app.domain.reconciliation.engine.money import to_usd
from app.domain.reconciliation.service import latest_run, summary
from app.domain.risk import SEVERITY_ORDER
from app.domain.workflows import checklist, stages_for
from app.infrastructure.database.models import (
    Approval,
    Case,
    Decision,
    EvidenceDocument,
    FinancialObligation,
    OwnershipEvent,
    Party,
    Property,
    RiskFinding,
    User,
    WorkflowAction,
    utcnow,
)


def _cite(ref: dict) -> str:
    if ref.get("filename"):
        page = f", p. {ref['page']}" if ref.get("page") else ""
        snippet = f" — “{ref['snippet'][:120]}”" if ref.get("snippet") else ""
        return f"{ref['filename']}{page}{snippet}"
    return ref.get("snippet") or ref.get("label") or "record"


def case_report(db: Session, case: Case) -> str:
    users = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == case.organization_id))}
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    parties = list(db.scalars(select(Party).where(Party.case_id == case.id).order_by(Party.name)))
    documents = list(db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id).order_by(EvidenceDocument.created_at)))
    events = sorted(db.scalars(select(OwnershipEvent).where(OwnershipEvent.case_id == case.id)), key=lambda e: e.event_date or "9999")
    chain = analyze(db, case)
    obligations = list(db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == case.id)))
    figures = summary(db, case, latest_run(db, case))
    findings = sorted(db.scalars(select(RiskFinding).where(RiskFinding.case_id == case.id)), key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.title))
    decisions = list(db.scalars(select(Decision).where(Decision.case_id == case.id).order_by(Decision.created_at)))
    actions = list(db.scalars(select(WorkflowAction).where(WorkflowAction.case_id == case.id).order_by(WorkflowAction.seq)))
    approvals = {a.action_id: a for a in db.scalars(select(Approval).where(Approval.action_id.in_([a.id for a in actions])))} if actions else {}
    stage_label = dict(stages_for(case)).get(case.stage, case.stage)

    lines = [
        f"# {case.reference} · {case.title}",
        "",
        f"**Status:** {case.status} · **Stage:** {stage_label} · generated {utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "> This report summarises evidence and automated checks for professional review. It is not a legal opinion "
        "and no finding in it is a legal determination.",
        "",
        "## Property",
    ]
    if prop:
        lines += [
            f"- Address: {', '.join(p for p in [prop.address_line, prop.city, prop.county, prop.state] if p) or '—'}",
            f"- Parcel number: {prop.parcel_number or '—'}",
        ]
    lines += ["", "## Parties"]
    lines += [
        f"- {p.name} — {', '.join(r.replace('_', ' ') for r in p.roles) or 'party'}"
        + (f" · deceased {p.date_of_death or ''}".rstrip() if p.is_deceased else "")
        for p in parties
    ] or ["- none recorded"]
    lines += ["", "## Evidence", "", "| Document | Type | Pages | Status | SHA-256 |", "|---|---|---|---|---|"]
    lines += [f"| {d.filename} | {d.doc_type} | {d.page_count} | {d.status} | `{d.sha256[:16]}…` |" for d in documents] or ["| — | | | | |"]
    lines += ["", "## Ownership history", ""]
    status_of = {e["id"]: e["chain_status"] for e in chain["events"]}
    for event in events:
        who = f"{', '.join(event.grantors) or '—'} → {', '.join(event.grantees) or '—'}"
        lines.append(f"- **{event.event_date or 'undated'}** · {event.event_type.replace('_', ' ')} · {who} · _{status_of.get(event.id, 'ok')}_")
    if not events:
        lines.append("- no ownership events reconstructed")
    lines += ["", f"Current owners per the evidence: {', '.join(chain['current_owners']) or 'undetermined'}"]
    lines += [
        "",
        "## Financial position",
        "",
        f"- Obligations recorded: {to_usd(figures['obligations_total_cents'])} across {figures['obligation_count']}",
        f"- Confirmed paid: {to_usd(figures['confirmed_paid_cents'])}",
        f"- Outstanding: {to_usd(figures['outstanding_cents'])}",
        f"- Unverified (needs review): {to_usd(figures['unverified_cents'])}",
    ]
    lines += [f"  - {o.obligation_type.replace('_', ' ')} {o.reference or ''} {o.tax_year or ''}: {to_usd(o.amount_cents)} · {o.status}" for o in obligations]
    lines += ["", "## Findings", ""]
    for finding in findings:
        lines.append(f"### [{finding.severity.upper()}] {finding.title} — {finding.status}")
        lines.append(finding.description)
        if finding.uncertainty:
            lines.append(f"_Uncertain:_ {finding.uncertainty}")
        if finding.status_note:
            lines.append(f"_Note:_ {finding.status_note}")
        lines += [f"- {_cite(ref)}" for ref in finding.evidence_refs[:6]]
        lines.append("")
    if not findings:
        lines.append("- no findings")
    lines += ["", "## Decisions and approvals", ""]
    lines += [f"- {d.created_at.strftime('%Y-%m-%d')} · **{d.title}** — {d.decision} ({users.get(d.decided_by_id, 'unknown')})" for d in decisions]
    for action in actions:
        approval = approvals.get(action.id)
        decided = f" · {approval.decision} by {users.get(approval.decided_by_id, 'unknown')}: “{approval.statement}”" if approval else ""
        lines.append(f"- action {action.seq} `{action.action}` · {action.status}{decided}")
    if not decisions and not actions:
        lines.append("- none yet")
    lines += ["", "## Stage checklist", ""]
    lines += [f"- [{'x' if g['done'] else ' '}] {dict(stages_for(case))[g['stage']]} — {g['detail']}" for g in checklist(db, case)]
    verified = verify_chain(db, case.organization_id)
    lines += [
        "",
        "## Audit trail",
        "",
        f"Audit chain verification: **{'intact' if verified['ok'] else 'BROKEN at #' + str(verified.get('broken_at'))}** "
        f"({verified['count']} organisation events).",
    ]
    if case.resolution_summary:
        lines += ["", "## Resolution", "", case.resolution_summary]
    return "\n".join(lines) + "\n"
