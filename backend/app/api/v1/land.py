"""Rural land records: parcel, records, family tree, analyses, costs, comparables, field research, agent."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.domain.approvals import ApprovalError, propose
from app.domain.land import agent, documents, explain, service
from app.domain.land.money import CATEGORIES, PAYEES
from app.domain.land.parse import parse_date_dmy
from app.domain.land.regions import LENDERS, REGIONS, catalog, region
from app.domain.land.units import land_class, parse_extent, survey_key
from app.domain.workflows import ParamError
from app.infrastructure.database.models import (
    ComparableSale,
    CostEntry,
    FamilyMember,
    FieldObservation,
    LandParcel,
    LandPassport,
    LandRecord,
    User,
)
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned, not_found
from app.services import ai

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["land"])
SOURCE_TYPES = ("ror", "cultivation", "sketch", "encumbrance", "sale_deed", "legal_heir", "death_certificate", "tax_receipt", "field_survey", "legal_opinion", "other")
CLASSES = ("", "wet", "dry", "garden", "residential", "government")
KINDS = ("concordance", "succession", "valuation", "readiness")


def _date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    d = parse_date_dmy(value)
    if d is None:
        raise HTTPException(422, detail=f"{field} is not a date (use YYYY-MM-DD or DD-MM-YYYY)")
    return d


# ── serializers ──────────────────────────────────────────────────────────────


def parcel_out(p: LandParcel | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "id": p.id, "region_code": p.region_code, "district": p.district, "taluk": p.taluk, "village": p.village,
        "survey_number": p.survey_number, "survey_key": p.survey_key, "extent_text": p.extent_text, "extent_sqm": p.extent_sqm,
        "land_class": p.land_class, "patta_number": p.patta_number, "guideline_inr_per_sqm": p.guideline_inr_per_sqm,
        "road_access": p.road_access, "irrigated": p.irrigated, "property_nature": p.property_nature, "personal_law": p.personal_law,
        "recorded_holder": p.recorded_holder, "notes": p.notes,
    }


def record_out(r: LandRecord) -> dict[str, Any]:
    return {
        "id": r.id, "source_type": r.source_type, "document_id": r.document_id, "page_number": r.page_number, "record_date": r.record_date,
        "issuing_office": r.issuing_office, "survey_number": r.survey_number, "survey_key": r.survey_key, "extent_text": r.extent_text,
        "extent_sqm": r.extent_sqm, "holders": r.holders, "boundaries": r.boundaries, "land_class": r.land_class, "extra": r.extra,
        "source": r.source, "snippet": r.snippet, "created_at": s.ts(r.created_at),
    }


def member_out(m: FamilyMember) -> dict[str, Any]:
    return {
        "id": m.id, "name": m.name, "sex": m.sex, "is_alive": m.is_alive, "date_of_death": m.date_of_death, "parent_ids": m.parent_ids,
        "spouse_ids": m.spouse_ids, "is_recorded_holder": m.is_recorded_holder, "lives_in_village": m.lives_in_village, "location": m.location,
        "consent_status": m.consent_status, "consent_note": m.consent_note, "consent_at": s.ts(m.consent_at),
        "consent_evidence": m.consent_evidence, "consent_document_id": m.consent_document_id,
        "consent_link_active": bool(m.consent_token_hash and m.consent_token_expires_at and _aware(m.consent_token_expires_at) > datetime.now(tz=UTC)),
    }


def analysis_out(a) -> dict[str, Any] | None:
    if a is None:
        return None
    return {"id": a.id, "kind": a.kind, "created_at": s.ts(a.created_at), "result_hash": a.result_hash, "inputs": a.inputs, "result": a.result}


def cost_out(c: CostEntry) -> dict[str, Any]:
    return {
        "id": c.id, "entry_date": c.entry_date, "category": c.category, "payee_kind": c.payee_kind, "service": c.service, "amount_inr": c.amount_inr,
        "days_spent": c.days_spent, "has_receipt": c.has_receipt, "extra": c.extra, "notes": c.notes,
    }


def comp_out(c: ComparableSale) -> dict[str, Any]:
    return {
        "id": c.id, "region_code": c.region_code, "district": c.district, "taluk": c.taluk, "village": c.village, "survey_number": c.survey_number,
        "sale_date": c.sale_date, "extent_sqm": c.extent_sqm, "consideration_inr": c.consideration_inr, "land_class": c.land_class,
        "distance_km": c.distance_km, "road_access": c.road_access, "source": c.source, "document_id": c.document_id, "notes": c.notes,
        "price_per_sqm": round(c.consideration_inr / c.extent_sqm, 2) if c.extent_sqm else None,
    }


def obs_out(o: FieldObservation) -> dict[str, Any]:
    return {
        k: getattr(o, k)
        for k in (
            "id", "region_code", "district", "taluk", "village", "observed_on", "households_surveyed", "households_deceased_holder",
            "correction_barriers", "branch_name", "lender_kind", "branch_requirements", "valuation_practices", "disagreement_counts",
            "dispute_settlers", "dispute_days_typical", "family_spend_inr_typical", "family_days_typical", "record_readers",
            "record_reading_fee_inr", "notes",
        )
    }


def passport_out(p: LandPassport) -> dict[str, Any]:
    return {"id": p.id, "code": p.code, "sha256": p.sha256, "status": p.status, "created_at": s.ts(p.created_at), "payload": p.payload}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ── catalog and overview ─────────────────────────────────────────────────────


@router.get("/land/catalog")
def land_catalog(_user: User = Depends(current_user)) -> dict:
    return catalog() | {"source_types": SOURCE_TYPES, "land_classes": CLASSES, "cost_categories": CATEGORIES, "payee_kinds": PAYEES, "languages": list(explain.LANGUAGES)}


@router.get("/cases/{case_id}/land")
def land_overview(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    passports = db.scalars(select(LandPassport).where(LandPassport.case_id == case.id).order_by(LandPassport.created_at.desc()))
    return {
        "parcel": parcel_out(service.parcel_of(db, case)),
        "records": [record_out(r) for r in service.records_of(db, case)],
        "members": [member_out(m) for m in service.members_of(db, case)],
        "latest": {k: analysis_out(service.latest(db, case, k)) for k in (*KINDS, "agent_run", "pathways")},
        "passports": [passport_out(p) for p in passports],
    }


# ── parcel ───────────────────────────────────────────────────────────────────


class ParcelIn(BaseModel):
    region_code: str = "TN"
    district: str = ""
    taluk: str = ""
    village: str = ""
    survey_number: str = ""
    extent_text: str = ""
    land_class: str = ""
    patta_number: str = ""
    guideline_inr_per_sqm: float | None = Field(default=None, ge=0)
    road_access: bool = False
    irrigated: bool = False
    property_nature: str = "self_acquired"
    personal_law: str = "hindu"
    recorded_holder: str = ""
    notes: str = ""


@router.put("/cases/{case_id}/land/parcel")
def put_parcel(case_id: str, body: ParcelIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.region_code.upper() not in REGIONS:
        raise HTTPException(422, detail=f"region_code must be one of {', '.join(REGIONS)}")
    if body.property_nature not in ("self_acquired", "ancestral"):
        raise HTTPException(422, detail="property_nature must be self_acquired or ancestral")
    if body.personal_law not in ("hindu", "christian", "muslim", "other"):
        raise HTTPException(422, detail="personal_law must be hindu, christian, muslim or other")
    reg = region(body.region_code)
    cls = body.land_class if body.land_class in CLASSES else land_class(body.land_class, reg)
    parcel = service.parcel_of(db, case) or LandParcel(case_id=case.id)
    for k, v in body.model_dump().items():
        setattr(parcel, k, v)
    parcel.region_code = reg.code
    parcel.land_class = cls
    parcel.survey_key = survey_key(body.survey_number)
    parcel.extent_sqm = parse_extent(body.extent_text, reg) if body.extent_text else None
    if body.extent_text and parcel.extent_sqm is None:
        raise HTTPException(422, detail=f"extent '{body.extent_text}' could not be read (try e.g. '1 acre 20 cents' or '0.48.56 hectare')")
    db.add(parcel)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.parcel_saved", actor=user, case_id=case.id, entity_type="land_parcel", entity_id=parcel.id, detail={"survey": parcel.survey_key, "region": reg.code})
    return {"parcel": parcel_out(parcel)}


# ── records ──────────────────────────────────────────────────────────────────


class RecordIn(BaseModel):
    source_type: str
    document_id: str | None = None
    page_number: int | None = None
    record_date: str | None = None
    issuing_office: str = ""
    survey_number: str = ""
    extent_text: str = ""
    holders: list[str] = []
    boundaries: dict[str, str] = {}
    land_class: str = ""
    extra: dict[str, Any] = {}


def _apply_record(db: Session, case, rec: LandRecord, body: RecordIn) -> None:
    if body.source_type not in SOURCE_TYPES:
        raise HTTPException(422, detail=f"source_type must be one of {', '.join(SOURCE_TYPES)}")
    if body.document_id:
        from app.infrastructure.database.models import EvidenceDocument

        doc = db.scalar(select(EvidenceDocument).where(EvidenceDocument.id == body.document_id, EvidenceDocument.case_id == case.id))
        if doc is None:
            raise HTTPException(422, detail="document is not in this case")
    reg = service.region_of(db, case)
    bad = set(body.boundaries) - {"north", "east", "south", "west"}
    if bad:
        raise HTTPException(422, detail="boundaries take north, east, south and west only")
    rec.source_type, rec.document_id, rec.page_number = body.source_type, body.document_id, body.page_number
    rec.record_date = _date(body.record_date, "record_date")
    rec.issuing_office = body.issuing_office
    rec.survey_number, rec.survey_key = body.survey_number, survey_key(body.survey_number)
    rec.extent_text = body.extent_text
    rec.extent_sqm = parse_extent(body.extent_text, reg) if body.extent_text else None
    rec.holders = [h.strip() for h in body.holders if h.strip()]
    rec.boundaries = {k: v.strip() for k, v in body.boundaries.items() if v and v.strip()}
    rec.land_class = body.land_class if body.land_class in CLASSES else land_class(body.land_class, reg)
    rec.extra = body.extra


@router.post("/cases/{case_id}/land/records", status_code=201)
def add_record(case_id: str, body: RecordIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    rec = LandRecord(case_id=case.id, source="manual")
    _apply_record(db, case, rec, body)
    db.add(rec)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.record_added", actor=user, case_id=case.id, entity_type="land_record", entity_id=rec.id, detail={"source_type": rec.source_type, "survey": rec.survey_key})
    return {"record": record_out(rec)}


@router.patch("/land/records/{record_id}")
def edit_record(record_id: str, body: RecordIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    rec, case = get_case_owned(db, user, LandRecord, record_id)
    before = record_out(rec)
    _apply_record(db, case, rec, body)
    if rec.source == "extracted":
        rec.source = "corrected"
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.record_corrected", actor=user, case_id=case.id, entity_type="land_record", entity_id=rec.id, detail={"before": {k: before[k] for k in ("survey_key", "extent_text", "holders", "boundaries", "land_class")}})
    return {"record": record_out(rec)}


@router.delete("/land/records/{record_id}")
def delete_record(record_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    rec, case = get_case_owned(db, user, LandRecord, record_id)
    audit.record(db, organization_id=case.organization_id, event="land.record_removed", actor=user, case_id=case.id, entity_type="land_record", entity_id=rec.id, detail={"source_type": rec.source_type, "survey": rec.survey_key})
    db.delete(rec)
    db.flush()
    return {"ok": True}


@router.post("/cases/{case_id}/land/records/derive")
def derive(case_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    out = service.derive_records(db, case, user)
    return {"created": out["created"], "comparables": out["comparables"], "records": [record_out(r) for r in out["records"]]}


# ── family tree and co-heir consent ──────────────────────────────────────────


class MemberIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    sex: str = "unknown"
    is_alive: bool = True
    date_of_death: str | None = None
    parent_ids: list[str] = []
    spouse_ids: list[str] = []
    is_recorded_holder: bool = False
    lives_in_village: bool = True
    location: str = ""
    consent_status: str | None = None
    consent_note: str = ""


CONSENT = ("unknown", "located", "contacted", "consented", "objected", "needs_info")


def _apply_member(db: Session, case, m: FamilyMember, body: MemberIn) -> None:
    if body.sex not in ("male", "female", "unknown"):
        raise HTTPException(422, detail="sex must be male, female or unknown")
    ids = {x.id for x in service.members_of(db, case)}
    for ref in [*body.parent_ids, *body.spouse_ids]:
        if ref not in ids or ref == m.id:
            raise HTTPException(422, detail="parents and spouses must be other people in this case's family tree")
    if body.consent_status and body.consent_status not in CONSENT:
        raise HTTPException(422, detail=f"consent_status must be one of {', '.join(CONSENT)}")
    m.name, m.sex, m.is_alive = body.name.strip(), body.sex, body.is_alive
    m.date_of_death = None if body.is_alive else _date(body.date_of_death, "date_of_death")
    m.parent_ids, m.spouse_ids = list(dict.fromkeys(body.parent_ids)), list(dict.fromkeys(body.spouse_ids))
    m.lives_in_village, m.location = body.lives_in_village, body.location
    if body.is_recorded_holder:
        for other in service.members_of(db, case):
            other.is_recorded_holder = False
    m.is_recorded_holder = body.is_recorded_holder
    if body.consent_status and body.consent_status != m.consent_status:
        m.consent_status, m.consent_note, m.consent_at = body.consent_status, body.consent_note, datetime.now(tz=UTC)


@router.post("/cases/{case_id}/land/members", status_code=201)
def add_member(case_id: str, body: MemberIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    m = FamilyMember(case_id=case.id)
    _apply_member(db, case, m, body)
    db.add(m)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.member_added", actor=user, case_id=case.id, entity_type="family_member", entity_id=m.id, detail={"alive": m.is_alive, "holder": m.is_recorded_holder})
    return {"member": member_out(m)}


@router.patch("/land/members/{member_id}")
def edit_member(member_id: str, body: MemberIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    m, case = get_case_owned(db, user, FamilyMember, member_id)
    previous = m.consent_status
    _apply_member(db, case, m, body)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.member_updated", actor=user, case_id=case.id, entity_type="family_member", entity_id=m.id, detail={"consent": [previous, m.consent_status]})
    return {"member": member_out(m)}


@router.delete("/land/members/{member_id}")
def delete_member(member_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    m, case = get_case_owned(db, user, FamilyMember, member_id)
    for other in service.members_of(db, case):
        if m.id in other.parent_ids or m.id in other.spouse_ids:
            raise HTTPException(409, detail=f"{other.name} refers to this person; edit them first")
    audit.record(db, organization_id=case.organization_id, event="land.member_removed", actor=user, case_id=case.id, entity_type="family_member", entity_id=m.id)
    db.delete(m)
    db.flush()
    return {"ok": True}


@router.post("/land/members/{member_id}/consent-link")
def consent_link(member_id: str, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    m, case = get_case_owned(db, user, FamilyMember, member_id)
    if not m.is_alive:
        raise HTTPException(409, detail="consent links are for living co-heirs")
    token = secrets.token_urlsafe(24)
    m.consent_token_hash = hashlib.sha256(token.encode()).hexdigest()
    m.consent_token_expires_at = datetime.now(tz=UTC) + timedelta(days=30)
    if m.consent_status == "unknown":
        m.consent_status = "contacted"
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.consent_link_issued", actor=user, case_id=case.id, entity_type="family_member", entity_id=m.id, detail={"expires_at": m.consent_token_expires_at.isoformat()})
    base = get_settings().public_base_url.rstrip("/")
    link = f"{base}/#consent/{token}"
    parcel = service.parcel_of(db, case)
    reg = service.region_of(db, case)
    survey, village = (parcel.survey_key, parcel.village) if parcel else ("", "")
    messages = {
        "en": f"Hello {m.name}. About the family land at survey no. {survey}, {village}: the draft shares of each heir are ready. "
              f"Please open this link to see your share and reply (it works once, for 30 days): {link}",
        "ta": f"வணக்கம் {m.name}. {village} கிராமம், சர்வே எண் {survey} குடும்ப நிலம் தொடர்பாக, ஒவ்வொரு வாரிசின் வரைவுப் பங்கும் தயாராக உள்ளது. "
              f"உங்கள் பங்கைப் பார்த்துப் பதில் அளிக்க இந்த இணைப்பைத் திறக்கவும் (ஒருமுறை மட்டும், 30 நாட்கள்): {link}",
        "hi": f"नमस्ते {m.name}। गाँव {village}, सर्वे नंबर {survey} की पारिवारिक ज़मीन के बारे में: हर वारिस का प्रस्तावित हिस्सा तैयार है। "
              f"अपना हिस्सा देखने और जवाब देने के लिए यह लिंक खोलें (एक बार, 30 दिन तक): {link}",
    }
    lang = reg.language if reg.language in messages else "en"
    return {
        "token": token, "link": link, "expires_at": m.consent_token_expires_at.isoformat(), "single_use": True,
        "message": messages[lang], "message_en": messages["en"], "language": lang,
    }


@router.get("/land/members/{member_id}/consent-letter", response_class=PlainTextResponse)
def consent_letter(member_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> str:
    m, case = get_case_owned(db, user, FamilyMember, member_id)
    succ = service.run_succession(db, case)
    share = next((x["share"] for x in succ.get("shares", []) if x["member_id"] == m.id), "—")
    parcel = service.parcel_of(db, case)
    return documents.consent_letter(
        heir=m.name, share=share, survey=parcel.survey_key if parcel else "", village=parcel.village if parcel else "",
        holder=succ.get("holder", ""), others=[x["name"] for x in succ.get("shares", []) if x["alive"]],
    )


# ── analyses ─────────────────────────────────────────────────────────────────


class AnalysisIn(BaseModel):
    lender: str = "bank_crop_loan"
    loan_amount_inr: int | None = Field(default=None, ge=0)
    claimed_value_inr: int | None = Field(default=None, ge=0)


@router.post("/cases/{case_id}/land/analyses/{kind}")
def run_analysis(case_id: str, kind: str, body: AnalysisIn = AnalysisIn(), user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if kind not in KINDS:
        raise not_found("Unknown analysis")
    if body.lender not in LENDERS:
        raise HTTPException(422, detail=f"lender must be one of {', '.join(LENDERS)}")
    if kind == "concordance":
        result = service.run_concordance(db, case)
        from app.domain.risk import run_detection

        result["detection"] = run_detection(db, case)
    elif kind == "succession":
        result = service.run_succession(db, case)
    elif kind == "valuation":
        result = service.run_valuation(db, case, claimed_value_inr=body.claimed_value_inr, lender=body.lender)
    else:
        result = service.run_readiness(db, case, lender=body.lender, loan_amount_inr=body.loan_amount_inr)
    snap = service.snapshot(db, case, kind, body.model_dump(), result, user)
    return {"analysis": analysis_out(snap)}


@router.get("/cases/{case_id}/land/analyses/{kind}")
def latest_analysis(case_id: str, kind: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if kind not in (*KINDS, "agent_run", "pathways"):
        raise not_found("Unknown analysis")
    return {"analysis": analysis_out(service.latest(db, case, kind))}


@router.get("/cases/{case_id}/land/explain")
def explain_case(case_id: str, lang: str = Query("en", max_length=8), lender: str = "bank_crop_loan", user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    lender = lender if lender in LENDERS else "bank_crop_loan"
    conc = service.run_concordance(db, case)
    succ = service.run_succession(db, case)
    val = service.run_valuation(db, case, lender=lender, conc=conc, succ=succ)
    ready = service.run_readiness(db, case, lender=lender, loan_amount_inr=None)
    parcel = service.parcel_of(db, case)
    out = explain.build(lang if lang in explain.LANGUAGES else "en", parcel=parcel, conc=conc, succ=succ, val=val, ready=ready)
    if lang not in explain.LANGUAGES:
        if ai.enabled():
            try:
                text = ai.generate_text(
                    "Translate each line into the language with ISO code '" + lang + "'. Keep numbers, ₹ amounts, names and survey numbers exactly. "
                    "Return one line per input line.\n\n" + "\n".join(out["lines"] + [out["disclaimer"]]),
                    system="You are a careful translator for rural families. Translate only; add nothing.",
                )
                lines = [x.strip() for x in text.splitlines() if x.strip()]
                if len(lines) == len(out["lines"]) + 1:
                    out = out | {"language": lang, "lines": lines[:-1], "disclaimer": lines[-1], "machine_translated": True}
            except Exception:  # noqa: BLE001 — fall back to English
                out["note"] = "Translation unavailable; showing English."
        else:
            out["note"] = f"Built-in languages are {', '.join(explain.LANGUAGES)}; other languages need AI_BACKEND=vertex."
    return out


@router.get("/cases/{case_id}/land/mutation-packet", response_class=PlainTextResponse)
def mutation_packet(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> str:
    case = get_case(db, user, case_id)
    text = documents.mutation_packet(
        case_ref=case.reference, parcel=service.parcel_of(db, case), region=service.region_of(db, case), conc=service.run_concordance(db, case),
        succ=service.run_succession(db, case), records=service.records_of(db, case),
    )
    audit.record(db, organization_id=case.organization_id, event="land.mutation_packet_prepared", actor=user, case_id=case.id, entity_type="case", entity_id=case.id, detail={"sha256": hashlib.sha256(text.encode()).hexdigest()})
    return text


# ── agent ────────────────────────────────────────────────────────────────────


@router.post("/cases/{case_id}/land/agent")
def run_agent(case_id: str, body: AnalysisIn = AnalysisIn(), user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.lender not in LENDERS:
        raise HTTPException(422, detail=f"lender must be one of {', '.join(LENDERS)}")
    from app.core.security import has_permission

    return {"run": agent.run(db, case, user, lender=body.lender, loan_amount_inr=body.loan_amount_inr, propose=has_permission(user.role, "action:propose"))}


# ── passport (issued through an approved action) ─────────────────────────────


class PassportIn(BaseModel):
    lender: str = "bank_crop_loan"
    loan_amount_inr: int = Field(default=0, ge=0)
    rationale: str = ""


@router.post("/cases/{case_id}/land/passport", status_code=201)
def request_passport(case_id: str, body: PassportIn, user: User = Depends(require("action:propose")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    try:
        row = propose(db, case, "land.issue_passport", {"lender": body.lender, "loan_amount_inr": body.loan_amount_inr}, actor=user, rationale=body.rationale or "Family asked for a lender-ready dossier")
    except (ApprovalError, ParamError) as exc:
        raise HTTPException(getattr(exc, "status", 422), detail=str(exc)) from exc
    return {"action": s.action(row, case.reference)}


@router.get("/cases/{case_id}/land/passports")
def passports(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    return {"passports": [passport_out(p) for p in db.scalars(select(LandPassport).where(LandPassport.case_id == case.id).order_by(LandPassport.created_at.desc()))]}


@router.get("/land/passports/{passport_id}/document", response_class=PlainTextResponse)
def passport_document(passport_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> str:
    p, _case = get_case_owned(db, user, LandPassport, passport_id)
    return documents.passport_markdown(p.code, p.sha256, p.payload)


# ── costs ────────────────────────────────────────────────────────────────────


class CostIn(BaseModel):
    entry_date: str
    category: str
    payee_kind: str = ""
    service: str = ""
    amount_inr: int = Field(default=0, ge=0, le=100_000_000)
    days_spent: float = Field(default=0, ge=0, le=3650)
    has_receipt: bool = False
    principal_inr: int | None = Field(default=None, ge=0)
    monthly_rate_pct: float | None = Field(default=None, ge=0, le=20)
    months: float | None = Field(default=None, ge=0, le=600)
    notes: str = ""


@router.get("/cases/{case_id}/land/costs")
def costs(case_id: str, lender: str = "bank_crop_loan", user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    entries = db.scalars(select(CostEntry).where(CostEntry.case_id == case.id).order_by(CostEntry.entry_date))
    return {"entries": [cost_out(c) for c in entries], "summary": service.run_costs(db, case, lender if lender in LENDERS else "bank_crop_loan")}


@router.post("/cases/{case_id}/land/costs", status_code=201)
def add_cost(case_id: str, body: CostIn, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.category not in CATEGORIES:
        raise HTTPException(422, detail=f"category must be one of {', '.join(CATEGORIES)}")
    if body.payee_kind and body.payee_kind not in PAYEES:
        raise HTTPException(422, detail=f"payee_kind must be one of {', '.join(PAYEES)}")
    extra = {}
    if body.category == "informal_loan":
        if not (body.principal_inr and body.monthly_rate_pct is not None and body.months):
            raise HTTPException(422, detail="an informal loan needs principal_inr, monthly_rate_pct and months")
        extra = {"principal_inr": body.principal_inr, "monthly_rate_pct": body.monthly_rate_pct, "months": body.months}
    row = CostEntry(
        case_id=case.id, entry_date=_date(body.entry_date, "entry_date"), category=body.category, payee_kind=body.payee_kind, service=body.service,
        amount_inr=body.amount_inr, days_spent=body.days_spent, has_receipt=body.has_receipt, extra=extra, notes=body.notes, created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="land.cost_logged", actor=user, case_id=case.id, entity_type="cost_entry", entity_id=row.id, detail={"category": row.category, "amount_inr": row.amount_inr, "receipt": row.has_receipt})
    return {"entry": cost_out(row)}


@router.delete("/land/costs/{entry_id}")
def delete_cost(entry_id: str, user: User = Depends(require("financials:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    row, case = get_case_owned(db, user, CostEntry, entry_id)
    audit.record(db, organization_id=case.organization_id, event="land.cost_removed", actor=user, case_id=case.id, entity_type="cost_entry", entity_id=row.id)
    db.delete(row)
    db.flush()
    return {"ok": True}


# ── comparable sales (organisation-wide) ─────────────────────────────────────


class CompIn(BaseModel):
    region_code: str = "TN"
    district: str = ""
    taluk: str = ""
    village: str = Field(min_length=1, max_length=120)
    survey_number: str = ""
    sale_date: str
    extent_text: str
    consideration_inr: int = Field(gt=0, le=10_000_000_000)
    land_class: str = ""
    distance_km: float | None = Field(default=None, ge=0, le=100)
    road_access: bool = False
    source: str = "sale_deed"
    notes: str = ""


@router.get("/land/comparables")
def list_comparables(village: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    q = select(ComparableSale).where(ComparableSale.organization_id == user.organization_id)
    if village:
        q = q.where(ComparableSale.village == village)
    return {"comparables": [comp_out(c) for c in db.scalars(q.order_by(ComparableSale.sale_date.desc()))]}


@router.post("/land/comparables", status_code=201)
def add_comparable(body: CompIn, user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    reg = region(body.region_code)
    sqm = parse_extent(body.extent_text, reg)
    if not sqm:
        raise HTTPException(422, detail=f"extent '{body.extent_text}' could not be read")
    if body.source not in ("encumbrance", "sale_deed", "field"):
        raise HTTPException(422, detail="source must be encumbrance, sale_deed or field")
    row = ComparableSale(
        organization_id=user.organization_id, region_code=reg.code, district=body.district, taluk=body.taluk, village=body.village.strip(),
        survey_number=body.survey_number, sale_date=_date(body.sale_date, "sale_date"), extent_sqm=sqm, consideration_inr=body.consideration_inr,
        land_class=body.land_class if body.land_class in CLASSES else land_class(body.land_class, reg), distance_km=body.distance_km,
        road_access=body.road_access, source=body.source, notes=body.notes, created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(db, organization_id=user.organization_id, event="land.comparable_added", actor=user, entity_type="comparable_sale", entity_id=row.id, detail={"village": row.village, "consideration_inr": row.consideration_inr})
    return {"comparable": comp_out(row)}


@router.delete("/land/comparables/{comp_id}")
def delete_comparable(comp_id: str, user: User = Depends(require("analysis:run")), db: Session = Depends(get_db, scope="function")) -> dict:
    row = db.scalar(select(ComparableSale).where(ComparableSale.id == comp_id, ComparableSale.organization_id == user.organization_id))
    if row is None:
        raise not_found("Comparable not found")
    audit.record(db, organization_id=user.organization_id, event="land.comparable_removed", actor=user, entity_type="comparable_sale", entity_id=row.id)
    db.delete(row)
    db.flush()
    return {"ok": True}


# ── field research ───────────────────────────────────────────────────────────


class ObservationIn(BaseModel):
    region_code: str = "TN"
    district: str = ""
    taluk: str = ""
    village: str = Field(min_length=1, max_length=120)
    observed_on: str
    households_surveyed: int = Field(default=0, ge=0, le=100_000)
    households_deceased_holder: int = Field(default=0, ge=0, le=100_000)
    correction_barriers: list[str] = []
    branch_name: str = ""
    lender_kind: str = ""
    branch_requirements: list[str] = []
    valuation_practices: list[str] = []
    disagreement_counts: dict[str, int] = {}
    dispute_settlers: list[str] = []
    dispute_days_typical: int | None = Field(default=None, ge=0)
    family_spend_inr_typical: int | None = Field(default=None, ge=0)
    family_days_typical: int | None = Field(default=None, ge=0)
    record_readers: list[str] = []
    record_reading_fee_inr: int | None = Field(default=None, ge=0)
    notes: str = ""


@router.get("/land/field-observations")
def list_observations(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.scalars(select(FieldObservation).where(FieldObservation.organization_id == user.organization_id).order_by(FieldObservation.observed_on.desc()))
    return {"observations": [obs_out(o) for o in rows]}


@router.post("/land/field-observations", status_code=201)
def add_observation(body: ObservationIn, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    if body.households_deceased_holder > body.households_surveyed:
        raise HTTPException(422, detail="households_deceased_holder cannot exceed households_surveyed")
    if body.lender_kind and body.lender_kind not in LENDERS:
        raise HTTPException(422, detail=f"lender_kind must be one of {', '.join(LENDERS)}")
    bad = set(body.disagreement_counts) - {"names", "extents", "boundaries", "survey_numbers", "land_class"}
    if bad:
        raise HTTPException(422, detail="disagreement_counts takes names, extents, boundaries, survey_numbers and land_class")
    clean = lambda xs: [x.strip()[:200] for x in xs if x and x.strip()][:30]  # noqa: E731
    row = FieldObservation(
        organization_id=user.organization_id, observer_id=user.id, region_code=region(body.region_code).code, district=body.district, taluk=body.taluk,
        village=body.village.strip(), observed_on=_date(body.observed_on, "observed_on"), households_surveyed=body.households_surveyed,
        households_deceased_holder=body.households_deceased_holder, correction_barriers=clean(body.correction_barriers), branch_name=body.branch_name,
        lender_kind=body.lender_kind, branch_requirements=clean(body.branch_requirements), valuation_practices=clean(body.valuation_practices),
        disagreement_counts=body.disagreement_counts, dispute_settlers=clean(body.dispute_settlers), dispute_days_typical=body.dispute_days_typical,
        family_spend_inr_typical=body.family_spend_inr_typical, family_days_typical=body.family_days_typical, record_readers=clean(body.record_readers),
        record_reading_fee_inr=body.record_reading_fee_inr, notes=body.notes,
    )
    db.add(row)
    db.flush()
    audit.record(db, organization_id=user.organization_id, event="land.field_observation", actor=user, entity_type="field_observation", entity_id=row.id, detail={"village": row.village, "households": row.households_surveyed})
    return {"observation": obs_out(row)}


@router.delete("/land/field-observations/{obs_id}")
def delete_observation(obs_id: str, user: User = Depends(require("case:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    row = db.scalar(select(FieldObservation).where(FieldObservation.id == obs_id, FieldObservation.organization_id == user.organization_id))
    if row is None:
        raise not_found("Observation not found")
    audit.record(db, organization_id=user.organization_id, event="land.field_observation_removed", actor=user, entity_type="field_observation", entity_id=row.id)
    db.delete(row)
    db.flush()
    return {"ok": True}


@router.get("/land/insights")
def land_insights(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    return service.insights(db, user.organization_id)
