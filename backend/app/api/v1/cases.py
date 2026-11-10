"""Cases (Stage 1 intake through Stage 8 history), parties, questions, reports, referrals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.cases import StageError, add_party, create_case, move_stage, overview
from app.domain.reconciliation.service import latest_run, summary
from app.domain.workflows import STAGES, checklist
from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    CaseQuestion,
    EvidenceDocument,
    ExtractedFact,
    Party,
    Property,
    Referral,
    RiskFinding,
    User,
    WorkflowAction,
)
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned, get_org_user, get_referral
from app.services import rag
from app.services.reporting import case_report

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["cases"])


class PropertyIn(BaseModel):
    address_line: str = ""
    city: str = ""
    county: str = ""
    state: str = ""
    postal_code: str = ""
    parcel_number: str = ""
    legal_description: str = ""


class PartyIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    party_type: str | None = None
    roles: list[str] = []
    is_deceased: bool = False
    date_of_death: str | None = None
    notes: str = ""


class CaseIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = ""
    property: PropertyIn = PropertyIn()
    parties: list[PartyIn] = []


class CasePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    assigned_to_id: str | None = None


class StageIn(BaseModel):
    to_stage: str


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class ReferralIn(BaseModel):
    address: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=5, max_length=5000)
    full_name: str = ""
    email: str = ""


class VerdictIn(BaseModel):
    reason: str = ""


def detail(db: Session, case: Case) -> dict[str, Any]:
    prop = db.scalar(select(Property).where(Property.case_id == case.id))
    parties = list(db.scalars(select(Party).where(Party.case_id == case.id).order_by(Party.name)))

    def count(model: Any, *where: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    return {
        "case": s.case(case),
        "property": s.prop(prop) if prop else None,
        "parties": [s.party(p) for p in parties],
        "stages": [{"key": k, "label": label} for k, label in STAGES],
        "checklist": checklist(db, case),
        "counts": {
            "documents": count(EvidenceDocument, EvidenceDocument.case_id == case.id),
            "facts": count(ExtractedFact, ExtractedFact.case_id == case.id),
            "facts_unreviewed": count(ExtractedFact, ExtractedFact.case_id == case.id, ExtractedFact.status == "unreviewed"),
            "findings_open": count(RiskFinding, RiskFinding.case_id == case.id, RiskFinding.status == "open"),
            "actions_pending": count(WorkflowAction, WorkflowAction.case_id == case.id, WorkflowAction.status == "proposed"),
        },
        "financials": summary(db, case, latest_run(db, case)),
    }


@router.get("/overview")
def org_overview(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    data = overview(db, user.organization_id)
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    refs = {c.id: c.reference for c in db.scalars(select(Case).where(Case.organization_id == user.organization_id))}
    data["recent"] = [s.audit_event(e, names) | {"case_reference": refs.get(e.case_id or "")} for e in data["recent"]]
    data["stages"] = [{"key": k, "label": label} for k, label in STAGES]
    return data


@router.get("/cases")
def list_cases(
    status: str | None = None,
    stage: str | None = None,
    q: str | None = Query(None, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    query = select(Case).where(Case.organization_id == user.organization_id)
    if status:
        query = query.where(Case.status == status)
    if stage:
        query = query.where(Case.stage == stage)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(func.lower(Case.title).like(like) | func.lower(Case.reference).like(like))
    cases = list(db.scalars(query.order_by(Case.created_at.desc())))
    props = {p.case_id: p for p in db.scalars(select(Property).where(Property.case_id.in_([c.id for c in cases])))} if cases else {}
    open_findings = dict(
        db.execute(
            select(RiskFinding.case_id, func.count()).where(RiskFinding.case_id.in_([c.id for c in cases]), RiskFinding.status == "open").group_by(RiskFinding.case_id)
        ).all()
    ) if cases else {}
    return {
        "cases": [
            s.case(c) | {"property": s.prop(props[c.id]) if c.id in props else None, "findings_open": open_findings.get(c.id, 0)}
            for c in cases
        ]
    }


@router.post("/cases", status_code=201)
def new_case(body: CaseIn, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = create_case(
        db, user, title=body.title, description=body.description, prop=body.property.model_dump(), parties=[p.model_dump() for p in body.parties]
    )
    return detail(db, case)


@router.get("/cases/{case_id}")
def get_one(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return detail(db, get_case(db, user, case_id))


@router.patch("/cases/{case_id}")
def patch_case(case_id: str, body: CasePatch, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    changes = body.model_dump(exclude_none=True)
    if "assigned_to_id" in changes:
        get_org_user(db, user, changes["assigned_to_id"])
    for key, value in changes.items():
        setattr(case, key, value.strip() if isinstance(value, str) else value)
    audit.record(db, organization_id=case.organization_id, event="case.updated", actor=user, case_id=case.id, entity_type="case", entity_id=case.id, detail=changes)
    return detail(db, case)


@router.post("/cases/{case_id}/stage")
def change_stage(case_id: str, body: StageIn, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    try:
        move_stage(db, case, user, body.to_stage)
    except StageError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return detail(db, case)


@router.get("/cases/{case_id}/timeline")
def timeline(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.organization_id == user.organization_id))}
    rows = db.scalars(select(AuditEvent).where(AuditEvent.case_id == case.id).order_by(AuditEvent.seq))
    return {"events": [s.audit_event(e, names) for e in rows]}


@router.get("/cases/{case_id}/report", response_class=PlainTextResponse)
def report(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> PlainTextResponse:
    case = get_case(db, user, case_id)
    audit.record(db, organization_id=case.organization_id, event="case.report_generated", actor=user, case_id=case.id, entity_type="case", entity_id=case.id)
    return PlainTextResponse(case_report(db, case), media_type="text/markdown; charset=utf-8")


@router.get("/cases/{case_id}/questions")
def questions(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    rows = db.scalars(select(CaseQuestion).where(CaseQuestion.case_id == case.id).order_by(CaseQuestion.created_at))
    return {"questions": [s.question(q) for q in rows]}


@router.post("/cases/{case_id}/questions", status_code=201)
def ask(case_id: str, body: QuestionIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    reply = rag.answer(db, case.id, body.question)
    row = CaseQuestion(
        case_id=case.id, asked_by_id=user.id, question=body.question.strip(), answer=reply["answer"],
        citations=reply["citations"], mode=reply["mode"], verified=reply["verified"],
    )
    db.add(row)
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event="case.question_answered", actor=user, case_id=case.id,
        entity_type="case_question", entity_id=row.id, detail={"mode": row.mode, "citations": len(row.citations), "verified": row.verified},
    )
    return {"question": s.question(row)}


# ── parties ──────────────────────────────────────────────────────────────────


@router.post("/cases/{case_id}/parties", status_code=201)
def new_party(case_id: str, body: PartyIn, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    try:
        party = add_party(db, case, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    audit.record(db, organization_id=case.organization_id, event="party.added", actor=user, case_id=case.id, entity_type="party", entity_id=party.id, detail={"name": party.name, "roles": party.roles})
    return {"party": s.party(party)}


@router.patch("/parties/{party_id}")
def patch_party(party_id: str, body: PartyIn, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    party, case = get_case_owned(db, user, Party, party_id)
    party.roles = body.roles
    party.is_deceased = body.is_deceased
    party.date_of_death = body.date_of_death
    party.notes = body.notes
    if body.party_type:
        party.party_type = body.party_type
    audit.record(db, organization_id=case.organization_id, event="party.updated", actor=user, case_id=case.id, entity_type="party", entity_id=party.id, detail=body.model_dump())
    return {"party": s.party(party)}


@router.delete("/parties/{party_id}")
def delete_party(party_id: str, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    party, case = get_case_owned(db, user, Party, party_id)
    audit.record(db, organization_id=case.organization_id, event="party.removed", actor=user, case_id=case.id, entity_type="party", entity_id=party.id, detail={"name": party.name})
    db.delete(party)
    return {"ok": True}


# ── referrals (InHeir's "report", as intake) ─────────────────────────────────


@router.get("/referrals")
def referrals(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.scalars(select(Referral).where(Referral.organization_id == user.organization_id).order_by(Referral.created_at.desc()))
    return {"referrals": [s.referral(r) for r in rows]}


@router.post("/referrals", status_code=201)
def new_referral(body: ReferralIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    row = Referral(organization_id=user.organization_id, submitted_by_id=user.id, **body.model_dump())
    db.add(row)
    db.flush()
    audit.record(db, organization_id=user.organization_id, event="referral.received", actor=user, entity_type="referral", entity_id=row.id, detail={"address": row.address})
    return {"referral": s.referral(row)}


@router.post("/referrals/{referral_id}/{verdict}")
def judge_referral(
    referral_id: str, verdict: str, body: VerdictIn, user: User = Depends(require("decision:record")), db: Session = Depends(get_db, scope="function")
) -> dict:
    if verdict not in ("verify", "unverify", "convert"):
        raise HTTPException(404, detail="Not found")
    row = get_referral(db, user, referral_id)
    if verdict == "convert":
        if row.case_id:
            raise HTTPException(409, detail="This referral already has a case")
        case = create_case(db, user, title=f"Referral: {row.address}", description=row.description, prop={"address_line": row.address})
        row.case_id, row.verdict = case.id, "verified"
    else:
        row.verdict, row.reason = ("verified" if verdict == "verify" else "not_verified"), body.reason
    audit.record(db, organization_id=user.organization_id, event=f"referral.{verdict}", actor=user, case_id=row.case_id, entity_type="referral", entity_id=row.id, detail={"reason": body.reason})
    return {"referral": s.referral(row)}
