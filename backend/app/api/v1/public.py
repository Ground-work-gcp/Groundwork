"""Unauthenticated endpoints, deliberately few and narrow.

* A co-heir who left the village opens a single-use link, sees their draft share and the plot,
  and records consent, an objection, or a request for more information. Only the SHA-256 of
  the token is stored; the link expires; every reply goes into the audit chain as ``heir``.
* A lender checks a land credit passport by its code and SHA-256. The answer is whether it is
  genuine and current, with the valuation band and readiness — no names.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.land import documents, service
from app.infrastructure.database.models import Case, FamilyMember, LandPassport
from app.infrastructure.database.session import get_db

from .deps import RateLimiter

router = APIRouter(prefix="/public", tags=["public"])
public_limiter = RateLimiter(limit=40, seconds=300)


def _member(db: Session, token: str) -> tuple[FamilyMember, Case]:
    if not token or len(token) > 100:
        raise HTTPException(404, detail="This link is not valid")
    digest = hashlib.sha256(token.encode()).hexdigest()
    m = db.scalar(select(FamilyMember).where(FamilyMember.consent_token_hash == digest))
    if m is None or m.consent_token_expires_at is None:
        raise HTTPException(404, detail="This link is not valid or has already been used")
    expires = m.consent_token_expires_at if m.consent_token_expires_at.tzinfo else m.consent_token_expires_at.replace(tzinfo=UTC)
    if expires <= datetime.now(tz=UTC):
        raise HTTPException(410, detail="This link has expired; ask for a new one")
    case = db.get(Case, m.case_id)
    if case is None:
        raise HTTPException(404, detail="This link is not valid")
    return m, case


@router.get("/consent/{token}")
def view_consent(token: str, request: Request, db: Session = Depends(get_db, scope="function")) -> dict:
    public_limiter.check(request)
    m, case = _member(db, token)
    parcel = service.parcel_of(db, case)
    reg = service.region_of(db, case)
    succ = service.run_succession(db, case)
    mine = next((x for x in succ.get("shares", []) if x["member_id"] == m.id), None)
    return {
        "name": m.name,
        "plot": {"survey_number": parcel.survey_key if parcel else "", "village": parcel.village if parcel else "", "taluk": parcel.taluk if parcel else "", "region": reg.name},
        "recorded_holder": succ.get("holder") or (parcel.recorded_holder if parcel else ""),
        "law": succ.get("law_name"),
        "your_share": mine["share"] if mine else None,
        "co_owners": [{"name": x["name"], "share": x["share"]} for x in succ.get("shares", []) if x["alive"]],
        "how": [st["text"] for st in succ.get("steps", [])],
        "status": m.consent_status,
        "letter": documents.consent_letter(
            heir=m.name, share=mine["share"] if mine else "—", survey=parcel.survey_key if parcel else "", village=parcel.village if parcel else "",
            holder=succ.get("holder", ""), others=[x["name"] for x in succ.get("shares", []) if x["alive"]],
        ),
        "disclaimer": succ.get("disclaimer"),
        "what_consent_means": (
            "Consenting lets the family apply to record all the heirs' names. It does not give your share to anyone. "
            "You can object, or ask for more information, instead."
        ),
    }


class ReplyIn(BaseModel):
    decision: str
    note: str = Field(default="", max_length=1000)


@router.post("/consent/{token}")
def reply_consent(token: str, body: ReplyIn, request: Request, db: Session = Depends(get_db, scope="function")) -> dict:
    public_limiter.check(request)
    if body.decision not in ("consented", "objected", "needs_info"):
        raise HTTPException(422, detail="decision must be consented, objected or needs_info")
    m, case = _member(db, token)
    m.consent_status, m.consent_note, m.consent_at = body.decision, body.note.strip(), datetime.now(tz=UTC)
    m.consent_token_hash, m.consent_token_expires_at = None, None  # single use
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event="land.consent_reply", actor_kind="heir", case_id=case.id, entity_type="family_member",
        entity_id=m.id, detail={"decision": body.decision, "note_chars": len(m.consent_note)},
    )
    return {"ok": True, "status": m.consent_status}


@router.get("/passports/{code}")
def verify_passport(code: str, request: Request, sha: str = "", db: Session = Depends(get_db, scope="function")) -> dict:
    public_limiter.check(request)
    p = db.scalar(select(LandPassport).where(LandPassport.code == code.strip().upper()))
    if p is None:
        return {"valid": False, "reason": "No passport has this code."}
    intact = service.digest(p.payload) == p.sha256
    matches = (not sha) or sha.strip().lower() == p.sha256
    v = p.payload.get("valuation", {})
    return {
        "valid": intact and matches and p.status == "active",
        "status": p.status,
        "sha256_matches": matches if sha else None,
        "intact": intact,
        "issued_at": p.payload.get("issued_at"),
        "region": p.payload.get("region"),
        "plot": {k: p.payload.get("plot", {}).get(k) for k in ("village", "taluk", "district", "survey_number")},
        "valuation_band": {k: v.get(k) for k in ("p10_inr", "p50_inr", "p90_inr", "lending_value_inr", "confidence")},
        "readiness": {k: p.payload.get("readiness", {}).get(k) for k in ("lender", "score", "verdict")},
        "co_owners": len(p.payload.get("draft_co_owners", [])),
        "note": "Ask the family for the full passport. Ownership is decided by the revenue authority; the value is a range.",
    }
