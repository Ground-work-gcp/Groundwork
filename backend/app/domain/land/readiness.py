"""Collateral readiness: what the local branch will ask for, what is missing, and the fastest way to fix it.

Each lender profile lists the requirements a branch checks before it accepts land as collateral
(``regions.LENDERS``). Each requirement is checked against the case's own records — never ticked
by hand — and every unmet one names the service that fixes it. Services depend on each other
(no mutation without a legal heir certificate, no legal heir certificate without a death
certificate), so the fix plan is a small dependency graph scheduled by the critical path method:
the family sees the order to do things in, the offices to visit, the documents to carry, the
indicative official fees and roughly how many days it will take.

Two facts often change the answer completely, so they are stated first:
* agricultural loans below the RBI collateral-free limit need no land collateral at all;
* what *this* branch asks for, as recorded on field visits, is added to the checklist.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .regions import LENDERS, REQUIREMENTS, Region, services

CRITICAL = {"no_deceased_holder", "not_government_land", "co_heir_consent", "encumbrance_13y", "encumbrance_30y"}


def _has(records: list[Any], source: str) -> list[Any]:
    return [r for r in records if r.source_type == source]


def check(
    region: Region,
    lender: str,
    *,
    loan_amount_inr: int | None,
    records: list[Any],
    concordance: dict[str, Any],
    succession: dict[str, Any] | None,
    valuation: dict[str, Any] | None,
    local_requirements: list[str] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    profile = LENDERS.get(lender) or LENDERS["bank_crop_loan"]
    issues = concordance.get("issues", [])
    types = {i["type"] for i in issues}
    deceased = bool(concordance.get("deceased_holders"))
    ror = _has(records, "ror")
    shares = (succession or {}).get("shares", [])
    living = [s for s in shares if s["alive"]]
    unconsented = [s["name"] for s in living if s["consent_status"] != "consented"]
    ec_years = float(concordance.get("ec_years") or 0)
    open_charge = any(i["type"] == "encumbrance" for i in issues)

    collateral_free = bool(profile["collateral_free_limit_inr"]) and loan_amount_inr is not None and loan_amount_inr <= profile["collateral_free_limit_inr"]
    needed = list(profile["requirements"]) + ([] if collateral_free else list(profile["collateral_requirements"]))

    def recent(rows: list[Any], months: int) -> bool:
        for r in rows:
            d = r.record_date or ""
            year = (r.extra or {}).get("tax_year") if isinstance(r.extra, dict) else None
            if year and int(str(year)[:4]) >= today.year - 1:
                return True
            if d and (today.year - int(d[:4])) * 12 + today.month - int(d[5:7] or 1) <= months:
                return True
            if not d and not year:
                return True
        return False

    status: dict[str, tuple[str, str]] = {}
    for req in needed:
        if req == "ror_in_applicant_name":
            ok = bool(ror) and not deceased and "names" not in {i["type"] for i in issues if i["severity"] in ("high", "critical")}
            status[req] = ("met" if ok else "missing" if not ror else "blocked", "record of rights on file" if ok else "no record of rights on file" if not ror else "the record is not in a living applicant's name")
        elif req == "no_deceased_holder":
            status[req] = ("blocked", "still recorded in the name of " + ", ".join(concordance["deceased_holders"])) if deceased else ("met", "no deceased holder on the record") if ror else ("missing", "no record of rights to check")
        elif req == "legal_heir_certificate":
            if not deceased:
                status[req] = ("not_needed", "no deceased holder")
            else:
                status[req] = ("met", "legal heir certificate on file") if _has(records, "legal_heir") else ("missing", "needed because the holder has died")
        elif req == "co_heir_consent":
            if len(living) <= 1:
                status[req] = ("not_needed", "a single owner")
            elif unconsented:
                status[req] = ("blocked", f"{len(unconsented)} of {len(living)} co-heirs have not consented: " + ", ".join(unconsented))
            else:
                status[req] = ("met", f"all {len(living)} co-heirs consented")
        elif req in ("encumbrance_13y", "encumbrance_30y"):
            years = 13 if req == "encumbrance_13y" else 30
            if open_charge:
                status[req] = ("blocked", "an open charge appears on the EC")
            elif ec_years >= years:
                status[req] = ("met", f"EC covers {ec_years:g} years")
            else:
                status[req] = ("missing", f"EC covers {ec_years:g} of {years} years" if ec_years else "no EC on file")
        elif req == "title_chain":
            ok = bool(_has(records, "sale_deed") or _has(records, "legal_heir"))
            status[req] = ("met", "parent documents on file") if ok else ("missing", "no sale deed or other parent document on file")
        elif req == "sketch_matches":
            sketch = _has(records, "sketch")
            mismatch = any(i["type"] == "extents" and any(x["source_type"] in ("sketch", "field_survey") for x in i["records"]) for i in issues)
            status[req] = ("missing", "no survey sketch on file") if not sketch else ("blocked", "the sketch and the recorded extent disagree") if mismatch else ("met", "sketch agrees with the record")
        elif req == "boundaries_undisputed":
            status[req] = ("blocked", "a boundary is disputed or one-sided") if "boundaries" in types else ("met", "no boundary conflict found")
        elif req == "cultivation_record":
            rows = _has(records, "cultivation")
            status[req] = ("met", "current cultivation record") if rows and recent(rows, 24) else ("missing", "no cultivation record from the last two years")
        elif req == "land_tax_paid":
            rows = _has(records, "tax_receipt")
            status[req] = ("met", "tax receipt for the current or last year") if rows and recent(rows, 15) else ("missing", "no recent land tax receipt")
        elif req == "not_government_land":
            gov = any(i["type"] == "land_class" and i["severity"] == "critical" for i in issues)
            status[req] = ("blocked", "a record classifies the land as government land") if gov else ("met", "no government classification found")
        elif req == "valuation":
            v = valuation or {}
            if v.get("status") != "estimated":
                status[req] = ("missing", "no valuation yet")
            elif v.get("confidence") == "low":
                status[req] = ("partial", "estimated with low confidence — a valuer's report will be needed")
            else:
                status[req] = ("met", f"estimated with {v['confidence']} confidence")
        elif req == "legal_scrutiny":
            status[req] = ("met", "legal scrutiny report on file") if _has(records, "legal_opinion") else ("missing", "lender's advocate has not reported yet")

    svc = services(region)
    service_done = {
        "death_certificate": not deceased or bool(_has(records, "death_certificate")),
        "legal_heir_certificate": not deceased or bool(_has(records, "legal_heir")),
        "co_heir_consent": status.get("co_heir_consent", ("met",))[0] in ("met", "not_needed") if "co_heir_consent" in status else len(living) <= 1 or not unconsented,
        "mutation": not deceased,
        "encumbrance_certificate": not open_charge and ec_years >= (30 if "encumbrance_30y" in needed else 13),
        "sketch_copy": bool(_has(records, "sketch")),
        "title_documents": bool(_has(records, "sale_deed") or _has(records, "legal_heir")),
    }

    items = []
    weights_total = weights_met = 0.0
    for req in needed:
        st, why = status[req]
        w = 2.0 if req in CRITICAL else 1.0
        if st != "not_needed":
            weights_total += w
            weights_met += w if st == "met" else w * 0.5 if st == "partial" else 0
        items.append({"id": req, **REQUIREMENTS[req], "status": st, "detail": why, "critical": req in CRITICAL})

    # the fix plan: unmet requirements → services → their unmet prerequisites, scheduled by the critical path
    wanted: list[str] = []

    def want(sid: str) -> None:
        if sid in wanted or service_done.get(sid, False):
            return
        for dep in svc[sid].needs:
            want(dep)
        wanted.append(sid)

    for it in items:
        if it["status"] in ("missing", "blocked", "partial"):
            want(it["fix"])
    finish: dict[str, int] = {}
    plan = []
    for sid in wanted:
        s = svc[sid]
        start = max((finish.get(d, 0) for d in s.needs), default=0)
        finish[sid] = start + s.days
        plan.append({
            "service": sid, "name": s.name, "office": s.office, "fee_inr": s.fee_inr, "days": s.days, "start_day": start, "finish_day": finish[sid],
            "needs": [d for d in s.needs if d in wanted], "documents": list(s.documents),
            "unblocks": [it["id"] for it in items if it.get("fix") == sid and it["status"] != "met"],
        })
    critical: list[str] = []
    if plan:
        cur = max(plan, key=lambda p: p["finish_day"])["service"]
        while cur:
            critical.insert(0, cur)
            deps = [d for d in svc[cur].needs if d in finish]
            cur = max(deps, key=lambda d: finish[d]) if deps else ""
    for p in plan:
        p["critical"] = p["service"] in critical

    blocked = [i for i in items if i["status"] in ("missing", "blocked")]
    verdict = "ready" if not blocked else "ready_without_collateral" if collateral_free and all(i["status"] in ("met", "not_needed", "partial") for i in items if i["id"] in profile["requirements"]) else "not_ready"
    notes = []
    if profile["collateral_free_limit_inr"]:
        limit = profile["collateral_free_limit_inr"]
        if collateral_free:
            notes.append(f"The loan asked for is within the collateral-free limit for agricultural loans (₹{limit:,}); the land is not pledged, so the collateral checks are skipped.")
        else:
            notes.append(f"Agricultural loans up to ₹{limit:,} need no land collateral (RBI collateral-free limit — confirm the current circular). Asking for that amount first avoids the title work below.")
    return {
        "lender": lender, "lender_label": profile["label"], "loan_amount_inr": loan_amount_inr, "collateral_free": collateral_free,
        "collateral_free_limit_inr": profile["collateral_free_limit_inr"],
        "score": round(100 * weights_met / weights_total) if weights_total else 100, "verdict": verdict,
        "requirements": items, "plan": plan, "critical_path": critical,
        "days_to_ready": max((p["finish_day"] for p in plan), default=0), "official_fees_inr": sum(p["fee_inr"] for p in plan),
        "local_branch_asks": local_requirements or [], "notes": notes,
        "disclaimer": "Indicative checklist and fees. The lender's own sanction terms and the revenue office decide.",
    }
