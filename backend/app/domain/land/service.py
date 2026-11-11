"""The land module's glue: load a case's land records, run each analysis, and keep a hashed snapshot.

Analyses are pure functions of the records (``concordance``, ``succession``, ``valuation``,
``readiness``); this module feeds them from the database, records every run in the audit chain
with the SHA-256 of its result, and turns concordance issues into ordinary Groundwork findings
so they flow through review, the Needs-you queue and approvals like everything else.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain import audit
from app.infrastructure.database.models import (
    Case,
    ComparableSale,
    CostEntry,
    DocumentPage,
    EvidenceDocument,
    FamilyMember,
    FieldObservation,
    LandAnalysis,
    LandParcel,
    LandPassport,
    LandRecord,
    Party,
    User,
)

from . import concordance, documents, money, readiness, succession, valuation
from .names import similarity
from .parse import parse
from .regions import LENDERS, Region, region
from .units import parse_extent, survey_key

EXTRACTOR = "land-rules:v1"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ── loading ──────────────────────────────────────────────────────────────────


def parcel_of(db: Session, case: Case) -> LandParcel | None:
    return db.scalar(select(LandParcel).where(LandParcel.case_id == case.id))


def region_of(db: Session, case: Case) -> Region:
    p = parcel_of(db, case)
    return region(p.region_code if p else "IN")


def records_of(db: Session, case: Case) -> list[LandRecord]:
    return list(db.scalars(select(LandRecord).where(LandRecord.case_id == case.id).order_by(LandRecord.record_date, LandRecord.created_at)))


def members_of(db: Session, case: Case) -> list[FamilyMember]:
    return list(db.scalars(select(FamilyMember).where(FamilyMember.case_id == case.id).order_by(FamilyMember.created_at)))


def parties_of(db: Session, case: Case) -> list[Party]:
    return list(db.scalars(select(Party).where(Party.case_id == case.id)))


def latest(db: Session, case: Case, kind: str) -> LandAnalysis | None:
    return db.scalar(
        select(LandAnalysis).where(LandAnalysis.case_id == case.id, LandAnalysis.kind == kind).order_by(LandAnalysis.created_at.desc()).limit(1)
    )


def snapshot(db: Session, case: Case, kind: str, inputs: dict[str, Any], result: dict[str, Any], user: User | None, *, actor_kind: str | None = None) -> LandAnalysis:
    row = LandAnalysis(case_id=case.id, kind=kind, inputs=json.loads(canonical(inputs)), result=json.loads(canonical(result)), result_hash=digest(result), created_by_id=user.id if user else None)
    db.add(row)
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event=f"land.{kind}", actor=user, actor_kind=actor_kind, case_id=case.id,
        entity_type="land_analysis", entity_id=row.id, detail={"result_hash": row.result_hash, **_headline(kind, result)},
    )
    return row


def _headline(kind: str, r: dict[str, Any]) -> dict[str, Any]:
    if kind == "concordance":
        return {"issues": len(r.get("issues", [])), "counts": r.get("counts", {})}
    if kind == "succession":
        return {"status": r.get("status"), "co_owners": r.get("co_owners"), "law": r.get("law")}
    if kind == "valuation":
        return {"status": r.get("status"), "p10": r.get("p10_inr"), "p50": r.get("p50_inr"), "p90": r.get("p90_inr"), "confidence": r.get("confidence")}
    if kind == "readiness":
        return {"lender": r.get("lender"), "score": r.get("score"), "verdict": r.get("verdict")}
    if kind == "agent_run":
        return {"steps": len(r.get("steps", [])), "handoffs": len(r.get("handoffs", []))}
    return {}


# ── analyses ─────────────────────────────────────────────────────────────────


def run_concordance(db: Session, case: Case) -> dict[str, Any]:
    return concordance.analyse(parcel_of(db, case), records_of(db, case), members_of(db, case), parties_of(db, case), region_of(db, case))


def holder_member(db: Session, case: Case, members: list[FamilyMember] | None = None) -> FamilyMember | None:
    members = members if members is not None else members_of(db, case)
    flagged = [m for m in members if m.is_recorded_holder]
    if flagged:
        return flagged[0]
    names = []
    parcel = parcel_of(db, case)
    if parcel and parcel.recorded_holder:
        names.append(parcel.recorded_holder)
    for r in records_of(db, case):
        if r.source_type == "ror" and r.holders and (not parcel or not parcel.survey_key or r.survey_key == parcel.survey_key):
            names.extend(r.holders)
    for n in names:
        for m in members:
            if similarity(n, m.name)[0] >= concordance.MATCH:
                return m
    return None


def run_succession(db: Session, case: Case) -> dict[str, Any]:
    members = members_of(db, case)
    parcel = parcel_of(db, case)
    holder = holder_member(db, case, members)
    people = [
        succession.Person(
            m.id, m.name, m.sex, m.is_alive, m.date_of_death, [p for p in m.parent_ids if isinstance(p, str)],
            [s for s in m.spouse_ids if isinstance(s, str)], m.lives_in_village, m.consent_status,
        )
        for m in members
    ]
    if holder is None:
        return {
            "status": "not_computed", "shares": [], "steps": [], "assumptions": [], "unresolved": [],
            "warnings": ["Mark the recorded holder in the family tree (or enter the holder's name on the parcel) to compute shares."],
            "disclaimer": succession.DISCLAIMER,
        }
    return succession.compute(people, holder.id, law=parcel.personal_law if parcel else "hindu", nature=parcel.property_nature if parcel else "self_acquired")


def comparables_for(db: Session, case: Case, parcel: LandParcel | None) -> list[dict[str, Any]]:
    if parcel is None:
        return []
    q = select(ComparableSale).where(ComparableSale.organization_id == case.organization_id)
    scope = [ComparableSale.village == parcel.village] if parcel.village else []
    if parcel.taluk:
        scope.append(ComparableSale.taluk == parcel.taluk)
    if scope:
        q = q.where(or_(*scope))
    out = []
    for c in db.scalars(q):
        if survey_key(c.survey_number) and survey_key(c.survey_number) == parcel.survey_key:
            continue  # a sale of the subject plot itself is not a comparable
        out.append({
            "id": c.id, "sale_date": c.sale_date, "extent_sqm": c.extent_sqm, "consideration_inr": c.consideration_inr, "land_class": c.land_class,
            "distance_km": c.distance_km, "road_access": c.road_access, "same_village": bool(parcel.village) and c.village == parcel.village,
            "survey_number": c.survey_number, "village": c.village,
        })
    return out


def _extent(parcel: LandParcel | None, records: list[LandRecord]) -> float | None:
    if parcel and parcel.extent_sqm:
        return parcel.extent_sqm
    ror = [r for r in records if r.source_type == "ror" and r.extent_sqm and (not parcel or not parcel.survey_key or r.survey_key == parcel.survey_key)]
    return ror[0].extent_sqm if ror else None


def run_valuation(db: Session, case: Case, *, claimed_value_inr: int | None = None, lender: str = "bank_crop_loan", conc: dict[str, Any] | None = None, succ: dict[str, Any] | None = None) -> dict[str, Any]:
    parcel = parcel_of(db, case)
    reg = region_of(db, case)
    records = records_of(db, case)
    conc = conc or run_concordance(db, case)
    succ = succ or run_succession(db, case)
    missing = len([s for s in succ.get("shares", []) if s["alive"] and s["consent_status"] != "consented"]) if len(succ.get("shares", [])) > 1 else 0
    cls = (parcel.land_class if parcel else "") or next((r.land_class for r in records if r.source_type == "ror" and r.land_class), "")
    subject = {
        "extent_sqm": _extent(parcel, records), "land_class": cls, "road_access": parcel.road_access if parcel else False,
        "guideline_inr_per_sqm": parcel.guideline_inr_per_sqm if parcel else None,
    }
    return valuation.estimate(
        subject, comparables_for(db, case, parcel), growth=reg.annual_growth, issues=conc.get("counts", {}), consents_missing=missing,
        claimed_value_inr=claimed_value_inr, ltv=LENDERS.get(lender, LENDERS["bank_crop_loan"])["ltv"],
    )


def local_requirements(db: Session, case: Case, lender: str) -> list[str]:
    parcel = parcel_of(db, case)
    if parcel is None:
        return []
    scope = [FieldObservation.village == parcel.village] if parcel.village else []
    if parcel.taluk:
        scope.append(FieldObservation.taluk == parcel.taluk)
    if not scope:
        return []
    out: list[str] = []
    for obs in db.scalars(select(FieldObservation).where(FieldObservation.organization_id == case.organization_id, or_(*scope))):
        if obs.lender_kind and obs.lender_kind != lender:
            continue
        for item in obs.branch_requirements or []:
            text = f"{item} ({obs.branch_name})" if obs.branch_name else str(item)
            if text not in out:
                out.append(text)
    return out


def run_readiness(db: Session, case: Case, *, lender: str, loan_amount_inr: int | None) -> dict[str, Any]:
    conc = run_concordance(db, case)
    succ = run_succession(db, case)
    val = run_valuation(db, case, lender=lender, conc=conc, succ=succ)
    out = readiness.check(
        region_of(db, case), lender, loan_amount_inr=loan_amount_inr, records=records_of(db, case), concordance=conc,
        succession=succ, valuation=val, local_requirements=local_requirements(db, case, lender),
    )
    out["valuation"] = {k: val.get(k) for k in ("status", "p10_inr", "p50_inr", "p90_inr", "lending_value_inr", "loan_ceiling_inr", "confidence")}
    return out


def run_costs(db: Session, case: Case, lender: str = "bank_crop_loan") -> dict[str, Any]:
    entries = list(db.scalars(select(CostEntry).where(CostEntry.case_id == case.id).order_by(CostEntry.entry_date)))
    return money.summarise(entries, region_of(db, case), lender)


# ── deriving records from uploaded documents ─────────────────────────────────


def derive_records(db: Session, case: Case, user: User | None) -> dict[str, Any]:
    reg = region_of(db, case)
    parcel = parcel_of(db, case)
    done = {r.document_id for r in db.scalars(select(LandRecord).where(LandRecord.case_id == case.id, LandRecord.document_id.is_not(None)))}
    created, comps = [], 0
    for doc in db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id, EvidenceDocument.status == "extracted")):
        if doc.id in done:
            continue
        pages = [(p.page_number, p.text) for p in db.scalars(select(DocumentPage).where(DocumentPage.document_id == doc.id).order_by(DocumentPage.page_number))]
        parsed = parse(pages, reg)
        if parsed is None:
            continue
        rec = LandRecord(case_id=case.id, document_id=doc.id, source="extracted", **parsed)
        db.add(rec)
        db.flush()
        created.append(rec)
        extra = parsed["extra"]
        if parsed["source_type"] == "sale_deed" and extra.get("consideration_inr") and parsed["extent_sqm"] and parsed["record_date"]:
            exists = db.scalar(select(func.count()).select_from(ComparableSale).where(ComparableSale.document_id == doc.id))
            subject = parcel.survey_key if parcel else ""
            if not exists and parsed["survey_key"] != subject:
                db.add(ComparableSale(
                    organization_id=case.organization_id, region_code=reg.code, district=extra.get("district", parcel.district if parcel else ""),
                    taluk=extra.get("taluk", parcel.taluk if parcel else ""), village=extra.get("village", parcel.village if parcel else ""),
                    survey_number=parsed["survey_number"], sale_date=parsed["record_date"], extent_sqm=parsed["extent_sqm"],
                    consideration_inr=int(extra["consideration_inr"]), land_class=parsed["land_class"], source="sale_deed", document_id=doc.id,
                    notes=f"From {doc.filename}", created_by_id=user.id if user else None,
                ))
                comps += 1
    # Fill blank parcel fields from the first record of rights for the plot.
    if parcel is not None:
        ror = next((r for r in records_of(db, case) if r.source_type == "ror" and (not parcel.survey_key or r.survey_key == parcel.survey_key)), None)
        if ror is not None:
            parcel.survey_number = parcel.survey_number or ror.survey_number
            parcel.survey_key = parcel.survey_key or ror.survey_key
            parcel.extent_text = parcel.extent_text or ror.extent_text
            parcel.extent_sqm = parcel.extent_sqm or ror.extent_sqm
            parcel.land_class = parcel.land_class or ror.land_class
            parcel.recorded_holder = parcel.recorded_holder or ", ".join(ror.holders)
            for f in ("village", "taluk", "district"):
                if not getattr(parcel, f) and ror.extra.get(f):
                    setattr(parcel, f, ror.extra[f])
            parcel.patta_number = parcel.patta_number or ror.extra.get("patta_number", "")
    db.flush()
    if created:
        audit.record(
            db, organization_id=case.organization_id, event="land.records_derived", actor=user, case_id=case.id, entity_type="case", entity_id=case.id,
            detail={"records": len(created), "comparables": comps, "extractor": EXTRACTOR, "types": sorted({r.source_type for r in created})},
        )
    return {"created": len(created), "comparables": comps, "records": created}


def normalise_record_fields(data: dict[str, Any], reg: Region) -> dict[str, Any]:
    out = dict(data)
    if "survey_number" in out:
        out["survey_key"] = survey_key(out["survey_number"] or "")
    if "extent_text" in out and out.get("extent_sqm") is None:
        out["extent_sqm"] = parse_extent(out["extent_text"] or "", reg)
    return out


# ── findings (the bridge into domain.risk) ───────────────────────────────────

FINDING_TYPES = {
    "deceased_holder": "land_holder_deceased", "names": "land_name_mismatch", "extents": "land_extent_mismatch",
    "survey_numbers": "land_survey_mismatch", "boundaries": "land_boundary_conflict", "land_class": "land_class_conflict",
    "encumbrance": "land_encumbrance_open",
}


def finding_candidates(db: Session, case: Case) -> list[Any]:
    from app.domain.risk import Candidate, Evidence

    if not db.scalar(select(func.count()).select_from(LandRecord).where(LandRecord.case_id == case.id)):
        return []
    conc = run_concordance(db, case)
    ev = Evidence(db, case)
    records = {r.id: r for r in records_of(db, case)}
    out = []
    for i in conc["issues"]:
        refs = []
        for ref in i["records"]:
            r = records.get(ref["record_id"])
            refs.append(ev.document(ref["document_id"], ref["page"], ref["label"], (r.snippet if r else "")[:200]))
        ftype = FINDING_TYPES.get(i["type"], "land_record_issue")
        if i["type"] == "land_class" and i["severity"] == "critical":
            ftype = "land_government_land"
        out.append(Candidate(
            ftype, i["severity"], i["title"], f"{i['detail']} Who can correct it: {i['office']}.", f"land:{i['key']}", refs,
            0.85 if refs else 0.7, uncertainty="Derived from the land records entered or extracted; confirm against certified copies.",
        ))
    return out


# ── organisation-wide answers to the field questions ─────────────────────────


def insights(db: Session, organization_id: str) -> dict[str, Any]:
    from collections import Counter

    obs = list(db.scalars(select(FieldObservation).where(FieldObservation.organization_id == organization_id).order_by(FieldObservation.observed_on)))
    surveyed = sum(o.households_surveyed for o in obs)
    deceased = sum(o.households_deceased_holder for o in obs)
    barriers = Counter(b for o in obs for b in (o.correction_barriers or []))
    asks: dict[str, Counter] = {}
    for o in obs:
        asks.setdefault(o.lender_kind or "unspecified", Counter()).update(o.branch_requirements or [])
    practices = Counter(p for o in obs for p in (o.valuation_practices or []))
    field_disagree: Counter = Counter()
    for o in obs:
        for k, v in (o.disagreement_counts or {}).items():
            try:
                field_disagree[k] += int(v)
            except (TypeError, ValueError):
                continue
    settlers = Counter(s for o in obs for s in (o.dispute_settlers or []))
    readers = Counter(s for o in obs for s in (o.record_readers or []))

    # What the cases themselves show (latest snapshot per case).
    cases = list(db.scalars(select(Case).where(Case.organization_id == organization_id)))
    case_disagree: Counter = Counter()
    land_cases = with_deceased = 0
    spends, days = [], []
    for c in cases:
        snap = latest(db, c, "concordance")
        if snap is None:
            continue
        land_cases += 1
        case_disagree.update({k: v for k, v in snap.result.get("counts", {}).items() if v})
        if snap.result.get("deceased_holders"):
            with_deceased += 1
        total = db.scalar(select(func.sum(CostEntry.amount_inr)).where(CostEntry.case_id == c.id, CostEntry.category != "informal_loan"))
        spent_days = db.scalar(select(func.sum(CostEntry.days_spent)).where(CostEntry.case_id == c.id))
        if total:
            spends.append(float(total))
        if spent_days:
            days.append(float(spent_days))
    return {
        "observations": len(obs),
        "villages": sorted({o.village for o in obs}),
        "q1_deceased_holder": {
            "field_share": round(deceased / surveyed, 3) if surveyed else None, "households_surveyed": surveyed, "households_deceased_holder": deceased,
            "case_share": round(with_deceased / land_cases, 3) if land_cases else None, "land_cases": land_cases,
            "barriers": barriers.most_common(10),
        },
        "q2_branch_asks": {k: v.most_common(12) for k, v in asks.items()},
        "q3_valuation_practices": practices.most_common(10),
        "q4_disagreements": {"field": dict(field_disagree), "cases": dict(case_disagree)},
        "q5_disputes": {
            "settlers": settlers.most_common(10),
            "median_days": money.med([o.dispute_days_typical for o in obs if o.dispute_days_typical is not None]),
        },
        "q6_family_cost": {
            "field_median_inr": money.med([o.family_spend_inr_typical for o in obs if o.family_spend_inr_typical is not None]),
            "field_median_days": money.med([o.family_days_typical for o in obs if o.family_days_typical is not None]),
            "case_median_inr": money.med(spends), "case_median_days": money.med(days), "cases_with_costs": len(spends),
        },
        "following_the_money": {
            "record_readers": readers.most_common(10),
            "median_reading_fee_inr": money.med([o.record_reading_fee_inr for o in obs if o.record_reading_fee_inr is not None]),
        },
    }



# ── the land credit passport (issued only through an approved workflow action) ──


def issue_passport(db: Session, case: Case, actor: User, *, lender: str, loan_amount_inr: int | None) -> dict[str, Any]:
    reg = region_of(db, case)
    parcel = parcel_of(db, case)
    conc = run_concordance(db, case)
    succ = run_succession(db, case)
    val = run_valuation(db, case, lender=lender, conc=conc, succ=succ)
    ready = run_readiness(db, case, lender=lender, loan_amount_inr=loan_amount_inr)
    payload = documents.passport_payload(case_ref=case.reference, parcel=parcel, region=reg, conc=conc, succ=succ, val=val, ready=ready)
    sha = digest(payload)
    for old in db.scalars(select(LandPassport).where(LandPassport.case_id == case.id, LandPassport.status == "active")):
        old.status = "superseded"
    code = documents.passport_code()
    while db.scalar(select(func.count()).select_from(LandPassport).where(LandPassport.code == code)):
        code = documents.passport_code()
    row = LandPassport(case_id=case.id, organization_id=case.organization_id, code=code, sha256=sha, payload=json.loads(canonical(payload)), issued_by_id=actor.id)
    db.add(row)
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event="land.passport_issued", actor=actor, case_id=case.id, entity_type="land_passport",
        entity_id=row.id, detail={"code": code, "sha256": sha, "lender": lender, "score": ready["score"], "verdict": ready["verdict"]},
    )
    return {"passport_id": row.id, "code": code, "sha256": sha}
