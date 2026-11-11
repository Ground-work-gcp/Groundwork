"""Creating cases, moving them through stages, and the organisation-level overview."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.evidence import is_organization, normalize_name, normalize_parcel
from app.domain.reconciliation.service import latest_run, summary
from app.domain.workflows import STAGE_KEYS, approval_stages, can_advance, stage_keys
from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    Organization,
    Party,
    Property,
    RiskFinding,
    User,
    WorkflowAction,
)


class StageError(Exception):
    pass


def create_case(
    db: Session,
    actor: User,
    *,
    title: str,
    description: str = "",
    prop: dict[str, str] | None = None,
    parties: list[dict[str, Any]] | None = None,
    kind: str = "heirs_property",
) -> Case:
    org = db.scalar(select(Organization).where(Organization.id == actor.organization_id).with_for_update())
    assert org is not None
    org.case_seq += 1
    case = Case(
        organization_id=org.id,
        reference=f"GW-{org.case_seq:04d}",
        kind=kind,
        title=title.strip(),
        description=description.strip(),
        created_by_id=actor.id,
        assigned_to_id=actor.id,
    )
    db.add(case)
    db.flush()
    data = prop or {}
    db.add(
        Property(
            case_id=case.id,
            address_line=data.get("address_line", "").strip(),
            city=data.get("city", "").strip(),
            county=data.get("county", "").strip(),
            state=data.get("state", "").strip(),
            postal_code=data.get("postal_code", "").strip(),
            parcel_number=data.get("parcel_number", "").strip(),
            parcel_number_normalized=normalize_parcel(data.get("parcel_number", "")),
            legal_description=data.get("legal_description", "").strip(),
        )
    )
    for party in parties or []:
        add_party(db, case, party)
    audit.record(
        db, organization_id=org.id, event="case.created", actor=actor, case_id=case.id, entity_type="case", entity_id=case.id,
        detail={"reference": case.reference, "title": case.title, "kind": case.kind},
    )
    return case


def add_party(db: Session, case: Case, data: dict[str, Any]) -> Party:
    name = str(data.get("name", "")).strip()
    normalized = normalize_name(name)
    if not normalized:
        raise ValueError("a party needs a name")
    existing = db.scalar(select(Party).where(Party.case_id == case.id, Party.name_normalized == normalized))
    roles = [str(r) for r in data.get("roles", []) if str(r).strip()]
    if existing is not None:
        existing.roles = sorted(set(existing.roles) | set(roles))
        return existing
    party = Party(
        case_id=case.id,
        name=name,
        name_normalized=normalized,
        party_type=data.get("party_type") or ("organization" if is_organization(name) else "person"),
        roles=roles,
        is_deceased=bool(data.get("is_deceased", False)),
        date_of_death=data.get("date_of_death") or None,
        notes=str(data.get("notes", "")),
        source="manual",
    )
    db.add(party)
    db.flush()
    return party


def move_stage(db: Session, case: Case, actor: User, to_stage: str) -> Case:
    """Direct stage moves for staff; the approval stages must go through an approved action."""
    keys = stage_keys(case)
    if to_stage in approval_stages(case) and to_stage in keys and keys.index(to_stage) > keys.index(case.stage):
        raise StageError(f"moving to {to_stage} requires an approved action (case.advance_stage / case.resolve)")
    problem = can_advance(db, case, to_stage)
    if problem:
        raise StageError(problem)
    previous = case.stage
    case.stage = to_stage
    audit.record(
        db, organization_id=case.organization_id, event="case.stage_changed", actor=actor, case_id=case.id,
        entity_type="case", entity_id=case.id, detail={"from": previous, "to": to_stage},
    )
    return case


def overview(db: Session, organization_id: str) -> dict[str, Any]:
    cases = list(db.scalars(select(Case).where(Case.organization_id == organization_id)))
    case_ids = [c.id for c in cases]
    by_stage = {key: 0 for key in STAGE_KEYS}
    for case in cases:
        if case.status == "open":
            by_stage[case.stage] = by_stage.get(case.stage, 0) + 1
    severities = {s: 0 for s in ("critical", "high", "medium", "low", "info")}
    if case_ids:
        for severity, count in db.execute(
            select(RiskFinding.severity, func.count())
            .where(RiskFinding.case_id.in_(case_ids), RiskFinding.status == "open")
            .group_by(RiskFinding.severity)
        ):
            severities[severity] = count
    pending = (
        int(db.scalar(select(func.count()).select_from(WorkflowAction).where(WorkflowAction.case_id.in_(case_ids), WorkflowAction.status == "proposed")) or 0)
        if case_ids
        else 0
    )
    money = {"obligations_total_cents": 0, "confirmed_paid_cents": 0, "outstanding_cents": 0, "unverified_cents": 0}
    for case in cases:
        if case.status != "open" or case.kind == "rural_land":
            continue
        figures = summary(db, case, latest_run(db, case))
        for key in money:
            money[key] += figures[key]
    recent = list(
        db.scalars(select(AuditEvent).where(AuditEvent.organization_id == organization_id).order_by(AuditEvent.seq.desc()).limit(12))
    )
    return {
        "cases": {
            "total": len(cases),
            "open": sum(1 for c in cases if c.status == "open"),
            "resolved": sum(1 for c in cases if c.status == "resolved"),
            "closed": sum(1 for c in cases if c.status == "closed"),
            "by_stage": by_stage,
        },
        "findings_open": severities,
        "approvals_pending": pending,
        "money": money,
        "recent": recent,
    }
