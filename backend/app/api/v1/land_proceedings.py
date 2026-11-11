"""Office proceedings on a land case, family pathways, and consent evidence."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.land import lifecycle, proceedings, service
from app.domain.land.parse import parse_date_dmy
from app.domain.land.regions import PROCEEDING_KINDS, STATUS_LABELS, process
from app.infrastructure.database.models import (
    EvidenceDocument,
    FamilyMember,
    LandProceeding,
    ProceedingEvent,
    User,
)
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned, not_found

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["land"])


def _date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    d = parse_date_dmy(value)
    if d is None:
        raise HTTPException(422, detail=f"{field} is not a date (use YYYY-MM-DD or DD-MM-YYYY)")
    return d


def event_out(e: ProceedingEvent) -> dict[str, Any]:
    return {"id": e.id, "event_date": e.event_date, "kind": e.kind, "note": e.note, "document_id": e.document_id, "status_after": e.status_after, "created_at": s.ts(e.created_at)}


def proceeding_out(p: LandProceeding, events: list[ProceedingEvent]) -> dict[str, Any]:
    return {
        "id": p.id, "kind": p.kind, "label": PROCEEDING_KINDS[p.kind]["label"], "office": p.office, "application_no": p.application_no, "status": p.status,
        "filed_on": p.filed_on, "sla_days": p.sla_days, "due_on": p.due_on, "objection_until": p.objection_until, "hearing_on": p.hearing_on,
        "decided_on": p.decided_on, "outcome": p.outcome, "parent_id": p.parent_id, "escalation_level": p.escalation_level, "applicants": p.applicants,
        "notes": p.notes, "events": [event_out(e) for e in events], "health": proceedings.health(p, events),
    }


def _events(db: Session, p: LandProceeding) -> list[ProceedingEvent]:
    return list(db.scalars(select(ProceedingEvent).where(ProceedingEvent.proceeding_id == p.id).order_by(ProceedingEvent.event_date, ProceedingEvent.created_at)))


def _doc(db: Session, case_id: str, document_id: str | None) -> str | None:
    if not document_id:
        return None
    if db.scalar(select(EvidenceDocument.id).where(EvidenceDocument.id == document_id, EvidenceDocument.case_id == case_id)) is None:
        raise HTTPException(422, detail="document is not in this case")
    return document_id


@router.get("/cases/{case_id}/land/proceedings")
def list_proceedings(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    reg = service.region_of(db, case)
    proc = process(reg.code)
    return {
        "proceedings": [proceeding_out(p, ev) for p, ev in lifecycle.proceedings_of(db, case)],
        "kinds": {k: {"label": v["label"], "flow": v["flow"], **proceedings.defaults(k, reg)} for k, v in PROCEEDING_KINDS.items()},
        "status_labels": STATUS_LABELS, "event_kinds": list(proceedings.EVENT_KINDS),
        "region": {"code": reg.code, "objection_days": proc["objection_days"], "appellate_office": proc["appellate_office"], "collector": proc["collector"],
                   "grievance_portal": proc["grievance_portal"], "rts_law": proc["rts_law"], "online": proc["online"]},
        "note": "Timelines are indicative service timelines; check the notified timeline with the office.",
    }


class ProceedingIn(BaseModel):
    kind: str
    office: str = ""
    application_no: str = Field(default="", max_length=80)
    applicants: list[str] = []
    notes: str = ""
    filed_on: str | None = None
    parent_id: str | None = None


@router.post("/cases/{case_id}/land/proceedings", status_code=201)
def add_proceeding(case_id: str, body: ProceedingIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    reg = service.region_of(db, case)
    try:
        d = proceedings.defaults(body.kind, reg)
    except proceedings.ProceedingError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if body.parent_id:
        get_case_owned(db, user, LandProceeding, body.parent_id)
    p = LandProceeding(
        case_id=case.id, kind=body.kind, office=body.office.strip() or d["office"], application_no=body.application_no.strip(),
        sla_days=d["sla_days"], applicants=[a.strip() for a in body.applicants if a.strip()], notes=body.notes, parent_id=body.parent_id, created_by_id=user.id,
    )
    db.add(p)
    db.flush()
    if body.filed_on:
        when = _date(body.filed_on, "filed_on")
        proceedings.apply_event(p, "filed", when, reg)
        db.add(ProceedingEvent(proceeding_id=p.id, event_date=when, kind="filed", note=f"Application {p.application_no}".strip(), status_after=p.status, actor_id=user.id))
        db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.proceeding_opened", actor=user, case_id=case.id, entity_type="land_proceeding", entity_id=p.id,
                 detail={"kind": p.kind, "office": p.office, "status": p.status, "application_no": p.application_no})
    return {"proceeding": proceeding_out(p, _events(db, p))}


class PatchIn(BaseModel):
    application_no: str | None = Field(default=None, max_length=80)
    office: str | None = None
    notes: str | None = None
    outcome: str | None = None


@router.patch("/land/proceedings/{proceeding_id}")
def edit_proceeding(proceeding_id: str, body: PatchIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    p, case = get_case_owned(db, user, LandProceeding, proceeding_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.proceeding_updated", actor=user, case_id=case.id, entity_type="land_proceeding", entity_id=p.id, detail=body.model_dump(exclude_none=True))
    return {"proceeding": proceeding_out(p, _events(db, p))}


class EventIn(BaseModel):
    kind: str
    event_date: str
    note: str = Field(default="", max_length=2000)
    document_id: str | None = None
    hearing_on: str | None = None


@router.post("/land/proceedings/{proceeding_id}/events", status_code=201)
def add_event(proceeding_id: str, body: EventIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    p, case = get_case_owned(db, user, LandProceeding, proceeding_id)
    reg = service.region_of(db, case)
    when = _date(body.event_date, "event_date")
    if when and date.fromisoformat(when) > date.today():
        raise HTTPException(422, detail="an event cannot be in the future; use hearing_on for a scheduled date")
    before = p.status
    try:
        proceedings.apply_event(p, body.kind, when or "", reg, hearing_on=_date(body.hearing_on, "hearing_on"))
    except proceedings.ProceedingError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if body.kind == "order_passed" and body.note:
        p.outcome = body.note
    ev = ProceedingEvent(proceeding_id=p.id, event_date=when, kind=body.kind, note=body.note.strip(), document_id=_doc(db, case.id, body.document_id), status_after=p.status, actor_id=user.id)
    db.add(ev)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.proceeding_event", actor=user, case_id=case.id, entity_type="land_proceeding", entity_id=p.id,
                 detail={"kind": body.kind, "date": when, "from": before, "to": p.status})
    out = {"proceeding": proceeding_out(p, _events(db, p))}
    if p.kind == "mutation" and p.status == "ordered":
        out["next"] = "Upload the updated record of rights to the Evidence tab and read it in; Groundwork re-compares the records and clears the deceased-holder finding."
    if body.kind == "objection_received":
        out["next"] = "An objection usually means a hearing. Consider pre-litigation mediation through the District Legal Services Authority (free legal aid) before it becomes a suit."
    return out


@router.delete("/land/proceedings/{proceeding_id}")
def delete_proceeding(proceeding_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    p, case = get_case_owned(db, user, LandProceeding, proceeding_id)
    if p.status != "draft":
        raise HTTPException(409, detail="only a draft can be deleted; a filed application is withdrawn with an event")
    audit.record(db, organization_id=case.organization_id, event="land.proceeding_deleted", actor=user, case_id=case.id, entity_type="land_proceeding", entity_id=p.id)
    db.delete(p)
    db.flush()
    return {"ok": True}


@router.get("/land/proceedings/{proceeding_id}/filing-check")
def filing_check(proceeding_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    p, case = get_case_owned(db, user, LandProceeding, proceeding_id)
    items = proceedings.filing_check(
        p.kind, records=service.records_of(db, case), conc=service.run_concordance(db, case), succ=service.run_succession(db, case),
        proceedings=[x for x, _ in lifecycle.proceedings_of(db, case)],
    )
    return {"items": items, "ready": all(i["ok"] for i in items), "note": "An application missing any of these is usually returned; file it anyway only if the office says so."}


@router.post("/land/proceedings/{proceeding_id}/escalate")
def escalate(proceeding_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    p, case = get_case_owned(db, user, LandProceeding, proceeding_id)
    if p.status in ("draft", "ordered", "rejected", "withdrawn"):
        raise HTTPException(409, detail=f"a {p.status} application is not escalated" + ("; appeal against a rejection instead" if p.status == "rejected" else ""))
    events = _events(db, p)
    h = proceedings.health(p, events)
    if h["signal"] not in ("overdue", "stalled", "at_risk"):
        raise HTTPException(409, detail=f"the application is {h['signal'].replace('_', ' ')}; escalate when it is overdue or stalled")
    parcel = service.parcel_of(db, case)
    reg = service.region_of(db, case)
    draft = proceedings.escalation_letter(
        p, events, reg, case_ref=case.reference, survey=parcel.survey_key if parcel else "", village=parcel.village if parcel else "",
        applicants=p.applicants or [m.name for m in service.members_of(db, case) if m.is_alive][:3],
    )
    child = LandProceeding(
        case_id=case.id, kind="grievance" if draft["level"] >= 3 else "appeal", office=draft["authority"], sla_days=process(reg.code)["sla"]["appeal"],
        parent_id=p.id, applicants=p.applicants, notes=f"Escalation of {PROCEEDING_KINDS[p.kind]['label'].lower()} ({draft['basis']})", created_by_id=user.id,
        escalation_level=draft["level"],
    )
    db.add(child)
    p.escalation_level = draft["level"]
    today = date.today().isoformat()
    db.add(ProceedingEvent(proceeding_id=p.id, event_date=today, kind="escalated", note=f"Representation drafted to {draft['authority']}", status_after=p.status, actor_id=user.id))
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.proceeding_escalated", actor=user, case_id=case.id, entity_type="land_proceeding", entity_id=p.id,
                 detail={"to": draft["authority"], "level": draft["level"], "elapsed_days": h["elapsed_days"], "child": child.id})
    return {"letter": draft["letter"], "authority": draft["authority"], "basis": draft["basis"], "level": draft["level"], "escalation": proceeding_out(child, [])}


# ── pathways ─────────────────────────────────────────────────────────────────


class PathwaysIn(BaseModel):
    lender: str = "bank_term_loan"
    beneficiary_id: str | None = None


@router.post("/cases/{case_id}/land/pathways")
def pathways(case_id: str, body: PathwaysIn = PathwaysIn(), user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    result = service.run_pathways(db, case, lender=body.lender, beneficiary_id=body.beneficiary_id)
    if result.get("status") != "compared":
        return {"analysis": {"result": result}}
    snap = service.snapshot(db, case, "pathways", body.model_dump(), result, user)
    return {"analysis": {"id": snap.id, "result_hash": snap.result_hash, "created_at": s.ts(snap.created_at), "result": result}}


# ── consent evidence: what was received on paper ─────────────────────────────


class EvidenceIn(BaseModel):
    consent_evidence: str
    document_id: str | None = None
    note: str = ""


@router.post("/land/members/{member_id}/consent-evidence")
def consent_evidence(member_id: str, body: EvidenceIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    m, case = get_case_owned(db, user, FamilyMember, member_id)
    if body.consent_evidence not in ("none", "online", "signed_letter", "registered_deed"):
        raise HTTPException(422, detail="consent_evidence must be none, online, signed_letter or registered_deed")
    if body.consent_evidence in ("signed_letter", "registered_deed") and not body.document_id:
        raise HTTPException(422, detail="attach the scanned letter or deed (upload it to Evidence first)")
    m.consent_document_id = _doc(db, case.id, body.document_id)
    m.consent_evidence = body.consent_evidence
    if body.consent_evidence in ("signed_letter", "registered_deed"):
        m.consent_status = "consented"
    if body.note:
        m.consent_note = body.note
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.consent_evidence", actor=user, case_id=case.id, entity_type="family_member", entity_id=m.id,
                 detail={"evidence": m.consent_evidence, "document_id": m.consent_document_id})
    from .land import member_out

    return {"member": member_out(m)}


@router.get("/cases/{case_id}/land/journey")
def journey(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    """The land case at a glance: lifecycle gates, active files with their health, and the next step."""
    case = get_case(db, user, case_id)
    if case.kind != "rural_land":
        raise not_found("Not a rural land case")
    from app.domain.flow import case_flow
    from app.domain.workflows import stages_for

    f = lifecycle.facts(db, case)
    gates = lifecycle.land_checklist(db, case, f)
    flow = case_flow(db, case, user)
    return {
        "stage": case.stage, "status": case.status, "stages": [{"key": k, "label": label} for k, label in stages_for(case)], "checklist": gates,
        "proceedings": [proceeding_out(p, ev) for p, ev in f["procs"]],
        "next_action": flow["next_action"], "critical_path": flow["critical_path"], "shortest_path": flow["shortest_path"],
        "needs_you": flow["needs_you"][:8],
    }
