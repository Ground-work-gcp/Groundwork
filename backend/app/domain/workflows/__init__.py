"""The eight-stage case lifecycle and the allowlist of actions that need approval.

Stage gates are computed from the case's own records, never ticked by hand. Moving forward
through the early stages is a direct, audited step for staff; entering *resolution* and
closing a case are **actions**: proposed, then executed only after a qualified approver
confirms them (``domain.approvals``).

The action registry is Beacon's ``remediation/registry.py`` pattern: nothing outside it can
be proposed or executed, params must match the schema exactly, and each action names who
may approve it. No action files, records or sends a legal document.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import (
    Case,
    Decision,
    EvidenceDocument,
    FinancialObligation,
    OwnershipEvent,
    Party,
    Property,
    ReconciliationRun,
    RiskFinding,
    Task,
    Transaction,
    User,
    utcnow,
)

STAGES: list[tuple[str, str]] = [
    ("intake", "Case intake"),
    ("evidence", "Evidence ingestion"),
    ("ownership", "Ownership reconstruction"),
    ("financial", "Financial reconciliation"),
    ("discrepancies", "Discrepancy detection"),
    ("review", "Human review"),
    ("resolution", "Resolution workflow"),
    ("resolved", "Verified case history"),
]
STAGE_KEYS = [key for key, _ in STAGES]
APPROVAL_STAGES = {"resolution", "resolved"}

# Rural land (India): the stages a family actually goes through, from a record in a dead parent's
# name to land a bank will lend against. Gates read the land records, analyses and proceedings.
LAND_STAGES: list[tuple[str, str]] = [
    ("intake", "Plot and family"),
    ("records", "Records gathered"),
    ("reconciled", "Records reconciled"),
    ("heirs", "Heirs and shares"),
    ("consent", "Co-heir consent"),
    ("mutation", "Mutation recorded"),
    ("credit", "Lender-ready"),
    ("resolved", "Verified case history"),
]
LAND_APPROVAL_STAGES = {"resolved"}


def stages_for(case: Case) -> list[tuple[str, str]]:
    return LAND_STAGES if case.kind == "rural_land" else STAGES


def stage_keys(case: Case) -> list[str]:
    return [key for key, _ in stages_for(case)]


def approval_stages(case: Case) -> set[str]:
    return LAND_APPROVAL_STAGES if case.kind == "rural_land" else APPROVAL_STAGES


def closing_stage(case: Case) -> str:
    """The stage a case is resolved from (the one before ``resolved``)."""
    return stage_keys(case)[-2]


def _count(db: Session, model: Any, *where: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def checklist(db: Session, case: Case) -> list[dict[str, Any]]:
    """What each stage needs, and whether the case's records satisfy it."""
    if case.kind == "rural_land":
        from app.domain.land.lifecycle import land_checklist

        return land_checklist(db, case)
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    parties = _count(db, Party, Party.case_id == case.id)
    extracted = _count(db, EvidenceDocument, EvidenceDocument.case_id == case.id, EvidenceDocument.status == "extracted")
    unreadable = _count(db, EvidenceDocument, EvidenceDocument.case_id == case.id, EvidenceDocument.status.in_(("needs_ocr", "failed")))
    events = _count(db, OwnershipEvent, OwnershipEvent.case_id == case.id)
    obligations = _count(db, FinancialObligation, FinancialObligation.case_id == case.id)
    transactions = _count(db, Transaction, Transaction.case_id == case.id)
    runs = _count(db, ReconciliationRun, ReconciliationRun.case_id == case.id)
    open_serious = _count(
        db, RiskFinding, RiskFinding.case_id == case.id, RiskFinding.status == "open", RiskFinding.severity.in_(("critical", "high"))
    )
    open_findings = _count(db, RiskFinding, RiskFinding.case_id == case.id, RiskFinding.status == "open")
    decisions = _count(db, Decision, Decision.case_id == case.id)
    open_tasks = _count(db, Task, Task.case_id == case.id, Task.status.in_(("open", "in_progress")))
    identified = bool(prop and (prop.address_line or prop.parcel_number))
    return [
        {"stage": "intake", "done": identified and parties > 0, "detail": f"property {'identified' if identified else 'not identified'} · {parties} part{'y' if parties == 1 else 'ies'}"},
        {"stage": "evidence", "done": extracted > 0, "detail": f"{extracted} document(s) extracted" + (f" · {unreadable} unreadable" if unreadable else "")},
        {"stage": "ownership", "done": events > 0, "detail": f"{events} ownership event(s)"},
        {"stage": "financial", "done": runs > 0, "detail": f"{obligations} obligation(s) · {transactions} payment(s) · {runs} reconciliation run(s)"},
        {"stage": "discrepancies", "done": case.detection_ran_at is not None, "detail": f"{open_findings} open finding(s)" if case.detection_ran_at else "detection not run"},
        {"stage": "review", "done": case.detection_ran_at is not None and open_serious == 0, "detail": f"{open_serious} open critical/high finding(s)"},
        {"stage": "resolution", "done": decisions > 0 and open_tasks == 0, "detail": f"{decisions} decision(s) · {open_tasks} open task(s)"},
        {"stage": "resolved", "done": case.status in ("resolved", "closed"), "detail": case.status},
    ]


def can_advance(db: Session, case: Case, to_stage: str) -> str | None:
    """None if the move is allowed, else why not."""
    keys = stage_keys(case)
    if to_stage not in keys:
        return f"unknown stage {to_stage!r}"
    if case.status != "open":
        return f"the case is {case.status}"
    current, target = keys.index(case.stage), keys.index(to_stage)
    if target <= current:
        return None  # stepping back to redo a stage is always allowed
    if target != current + 1:
        return "stages advance one at a time"
    gate = next(g for g in checklist(db, case) if g["stage"] == case.stage)
    if not gate["done"]:
        return f"the {case.stage} stage is not complete: {gate['detail']}"
    return None


# ── the action allowlist ─────────────────────────────────────────────────────


class ParamError(ValueError):
    pass


@dataclass(frozen=True)
class ActionSpec:
    id: str
    description: str
    params_schema: dict[str, type]
    approver_roles: tuple[str, ...]
    reversible: bool
    precondition: Callable[[Session, Case, dict[str, Any]], str | None]
    execute: Callable[[Session, Case, dict[str, Any], User], dict[str, Any]]
    summary: Callable[[dict[str, Any]], str]


def params_hash(action: str, params: dict[str, Any]) -> str:
    canonical = json.dumps({"action": action, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pre_advance(db: Session, case: Case, params: dict[str, Any]) -> str | None:
    return can_advance(db, case, params["to_stage"])


def _do_advance(db: Session, case: Case, params: dict[str, Any], _actor: User) -> dict[str, Any]:
    previous = case.stage
    case.stage = params["to_stage"]
    return {"from": previous, "to": case.stage}


def _pre_resolve(db: Session, case: Case, _params: dict[str, Any]) -> str | None:
    if case.status != "open":
        return f"the case is already {case.status}"
    last = closing_stage(case)
    if case.stage != last:
        return f"a case is resolved from the {last} stage"
    gate = next(g for g in checklist(db, case) if g["stage"] == last)
    if not gate["done"]:
        return f"{last} is not complete: {gate['detail']}"
    open_serious = _count(
        db, RiskFinding, RiskFinding.case_id == case.id, RiskFinding.status == "open", RiskFinding.severity.in_(("critical", "high"))
    )
    if open_serious:
        return f"{open_serious} critical/high finding(s) are still open"
    return None


def _do_resolve(_db: Session, case: Case, params: dict[str, Any], _actor: User) -> dict[str, Any]:
    case.status, case.stage = "resolved", "resolved"
    case.resolution_summary = params["resolution"]
    case.resolved_at = utcnow()
    return {"status": case.status}


def _pre_close(_db: Session, case: Case, _params: dict[str, Any]) -> str | None:
    return None if case.status == "open" else f"the case is already {case.status}"


def _do_close(_db: Session, case: Case, params: dict[str, Any], _actor: User) -> dict[str, Any]:
    case.status = "closed"
    case.resolution_summary = params["reason"]
    case.resolved_at = utcnow()
    return {"status": case.status}


def _finding(db: Session, case: Case, finding_id: str) -> RiskFinding | None:
    return db.scalar(select(RiskFinding).where(RiskFinding.id == finding_id, RiskFinding.case_id == case.id))


def _pre_dismiss(db: Session, case: Case, params: dict[str, Any]) -> str | None:
    finding = _finding(db, case, params["finding_id"])
    if finding is None:
        return "finding not found in this case"
    if finding.status in ("resolved", "dismissed"):
        return f"finding is already {finding.status}"
    return None


def _do_dismiss(db: Session, case: Case, params: dict[str, Any], actor: User) -> dict[str, Any]:
    finding = _finding(db, case, params["finding_id"])
    assert finding is not None
    finding.status, finding.status_note = "dismissed", params["reason"]
    finding.reviewed_by_id, finding.reviewed_at = actor.id, utcnow()
    return {"finding_id": finding.id, "status": "dismissed"}


def _pre_paid(db: Session, case: Case, params: dict[str, Any]) -> str | None:
    obligation = db.scalar(select(FinancialObligation).where(FinancialObligation.id == params["obligation_id"], FinancialObligation.case_id == case.id))
    if obligation is None:
        return "obligation not found in this case"
    if params["transaction_id"]:
        txn = db.scalar(select(Transaction).where(Transaction.id == params["transaction_id"], Transaction.case_id == case.id))
        if txn is None:
            return "payment not found in this case"
    return None


def _do_paid(db: Session, case: Case, params: dict[str, Any], actor: User) -> dict[str, Any]:
    obligation = db.scalar(select(FinancialObligation).where(FinancialObligation.id == params["obligation_id"], FinancialObligation.case_id == case.id))
    assert obligation is not None
    obligation.status = "paid"
    db.add(Decision(
        case_id=case.id, title=f"Obligation {obligation.reference or obligation.id[:8]} recorded as paid",
        decision="Recorded as paid by manual reconciliation", rationale=params["note"], decided_by_id=actor.id,
        related={"obligation_id": obligation.id, "transaction_id": params["transaction_id"] or None},
    ))
    return {"obligation_id": obligation.id, "status": "paid"}


def _pre_request(db: Session, case: Case, params: dict[str, Any]) -> str | None:
    if params["assignee_id"]:
        user = db.get(User, params["assignee_id"])
        if user is None or user.organization_id != case.organization_id:
            return "assignee is not in this organisation"
    return None


def _do_request(db: Session, case: Case, params: dict[str, Any], actor: User) -> dict[str, Any]:
    task = Task(
        case_id=case.id, title=params["title"], description=params["description"], kind="evidence_request",
        assignee_id=params["assignee_id"] or None, created_by_id=actor.id,
    )
    db.add(task)
    db.flush()
    return {"task_id": task.id}


def _pre_passport(db: Session, case: Case, params: dict[str, Any]) -> str | None:
    from app.domain.land.regions import LENDERS
    from app.domain.land.service import parcel_of, records_of

    if params["lender"] not in LENDERS:
        return f"lender must be one of {', '.join(LENDERS)}"
    if params["loan_amount_inr"] < 0:
        return "loan_amount_inr cannot be negative"
    if parcel_of(db, case) is None or not records_of(db, case):
        return "the case has no land parcel or land records"
    return None


def _do_passport(db: Session, case: Case, params: dict[str, Any], actor: User) -> dict[str, Any]:
    from app.domain.land.service import issue_passport

    return issue_passport(db, case, actor, lender=params["lender"], loan_amount_inr=params["loan_amount_inr"] or None)


REGISTRY: dict[str, ActionSpec] = {
    spec.id: spec
    for spec in [
        ActionSpec(
            "case.advance_stage", "Move the case to another stage of the lifecycle", {"to_stage": str},
            ("attorney", "admin"), True, _pre_advance, _do_advance, lambda p: f"move the case to {p['to_stage']}",
        ),
        ActionSpec(
            "case.resolve", "Mark the case resolved with a written resolution", {"resolution": str},
            ("attorney", "admin"), False, _pre_resolve, _do_resolve, lambda p: "resolve the case",
        ),
        ActionSpec(
            "case.close_unresolved", "Close the case without resolution (withdrawn, referred, unreachable)", {"reason": str},
            ("attorney", "admin"), False, _pre_close, _do_close, lambda p: "close the case unresolved",
        ),
        ActionSpec(
            "finding.dismiss", "Dismiss a finding as not applicable, with a reason", {"finding_id": str, "reason": str},
            ("attorney", "admin"), True, _pre_dismiss, _do_dismiss, lambda p: f"dismiss finding {p['finding_id'][:8]}",
        ),
        ActionSpec(
            "obligation.record_paid", "Record an obligation as paid by manual reconciliation",
            {"obligation_id": str, "transaction_id": str, "note": str},
            ("attorney", "admin"), True, _pre_paid, _do_paid, lambda p: f"record obligation {p['obligation_id'][:8]} as paid",
        ),
        ActionSpec(
            "evidence.request", "Open an evidence-request task (e.g. obtain a probate order)",
            {"title": str, "description": str, "assignee_id": str},
            ("paralegal", "attorney", "admin"), True, _pre_request, _do_request, lambda p: f"request evidence: {p['title']}",
        ),
        ActionSpec(
            "land.issue_passport", "Issue a land credit passport (a hashed, verifiable dossier) for a lender",
            {"lender": str, "loan_amount_inr": int},
            ("attorney", "admin"), True, _pre_passport, _do_passport, lambda p: f"issue a land credit passport for {p['lender'].replace('_', ' ')}",
        ),
    ]
}


def validate_params(action: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = REGISTRY.get(action)
    if spec is None:
        raise ParamError(f"action '{action}' is not allowlisted")
    clean: dict[str, Any] = {}
    for key, expected in spec.params_schema.items():
        value = params.get(key, "" if expected is str else None)
        if not isinstance(value, expected) or isinstance(value, bool):
            raise ParamError(f"param {key} must be {expected.__name__}")
        clean[key] = value.strip() if isinstance(value, str) else value
    unexpected = set(params) - set(spec.params_schema)
    if unexpected:
        raise ParamError(f"unexpected param(s): {', '.join(sorted(unexpected))}")
    for key in ("reason", "resolution", "title", "note"):
        if key in clean and not clean[key]:
            raise ParamError(f"param {key} is required")
    return clean


def confirmation_phrase(seq: int) -> str:
    """What an approver must type: Beacon's 'approve fix 1', for case actions."""
    return f"approve action {seq}"


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "description": spec.description,
            "params": {k: v.__name__ for k, v in spec.params_schema.items()},
            "approver_roles": list(spec.approver_roles),
            "reversible": spec.reversible,
        }
        for spec in REGISTRY.values()
    ]


LAND_REQUESTS = {
    "land_holder_deceased": "Obtain the legal heir certificate and apply for mutation into the heirs' names",
    "land_name_mismatch": "Request a name correction in the record of rights",
    "land_extent_mismatch": "Request a survey sketch copy and re-measurement from the survey office",
    "land_survey_mismatch": "Obtain the subdivision history of the survey number from the survey office",
    "land_boundary_conflict": "Request a boundary survey with notice to the neighbouring holders",
    "land_class_conflict": "Confirm the land classification with the revenue office",
    "land_government_land": "Confirm with the revenue and survey offices whether the land is government land",
    "land_encumbrance_open": "Obtain a release, or the status, of the open entry on the encumbrance certificate",
}


def suggest_actions(db: Session, case: Case) -> list[dict[str, Any]]:
    """Rule-based evidence requests for open findings (proposals only; a human approves)."""
    suggestions = []
    for finding in db.scalars(select(RiskFinding).where(RiskFinding.case_id == case.id, RiskFinding.status.in_(("open", "acknowledged")))):
        if finding.finding_type in ("unprobated_estate", "unprobated_will"):
            title = "Obtain probate order or affidavit of heirship"
        elif finding.finding_type in ("broken_chain_of_title", "conveyance_by_decedent"):
            title = "Obtain the missing conveyance or a title search for the gap"
        elif finding.finding_type == "parcel_id_conflict":
            title = "Request the assessor's parcel history"
        elif finding.finding_type == "unreconciled_obligation":
            title = "Request a current payoff statement from the creditor"
        elif finding.finding_type in LAND_REQUESTS:
            title = LAND_REQUESTS[finding.finding_type]
        else:
            continue
        suggestions.append({
            "action": "evidence.request",
            "params": {"title": title, "description": f"{finding.title}. {finding.description}"[:1000], "assignee_id": ""},
            "rationale": f"Suggested by detection rules for finding {finding.id}",
            "finding_id": finding.id,
        })
    return suggestions
