"""Documents Groundwork *prepares* (never files): the mutation packet, consent letters, the land credit passport.

The mutation packet is what a family carries to the taluk office: a covering application to
the revenue authority with the draft heirs and shares, every supporting document marked as
present or still missing, and one consent letter per co-heir. The passport is what a family
hands a lender: the verified picture of the land with a SHA-256 the lender can check against
Groundwork's public verification endpoint — which is itself anchored in the audit chain.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .regions import Region
from .units import format_extent
from .valuation import inr


def mutation_packet(*, case_ref: str, parcel: Any, region: Region, conc: dict[str, Any], succ: dict[str, Any], records: list[Any]) -> str:
    have = {r.source_type for r in records}
    holders = ", ".join(conc.get("holders") or []) or (parcel.recorded_holder if parcel else "") or "the recorded holder"
    survey = conc.get("survey_key") or (parcel.survey_number if parcel else "")
    village = parcel.village if parcel else ""
    taluk = parcel.taluk if parcel else ""
    lines = [
        f"# Draft application — {region.mutation}",
        "",
        f"*Prepared by Groundwork for case {case_ref}. This is a draft for the family to review and sign; "
        "the decision on succession and mutation rests with the revenue authority.*",
        "",
        f"**To:** The {region.revenue_office}{', ' + taluk if taluk else ''}",
        f"**Through:** The {region.village_official}{', ' + village if village else ''}",
        "",
        f"**Subject:** Request for {region.mutation.lower()} of survey number {survey}, {village} village, "
        f"recorded in the name of {holders} (deceased)",
        "",
        "Respected Sir / Madam,",
        "",
        f"The land in survey number {survey} ({parcel.extent_text if parcel and parcel.extent_text else format_extent(parcel.extent_sqm if parcel else None, region)}) "
        f"stands recorded in the {region.ror} in the name of {holders}, who has died. We, the legal heirs, request that the record be "
        "changed to our names. The family tree and the shares we understand to apply are set out below for your verification.",
        "",
        "## Heirs and draft shares",
        "",
        f"Computed under {succ.get('law_name', 'the applicable law')} from the family tree as entered.",
        "",
        "| Heir | Draft share | Lives in the village | Consent |",
        "|---|---|---|---|",
    ]
    for s in succ.get("shares", []):
        if s["alive"]:
            lines.append(f"| {s['name']} | {s['share']} | {'yes' if s['lives_in_village'] else 'no'} | {s['consent_status'].replace('_', ' ')} |")
    if succ.get("steps"):
        lines += ["", "### How the shares were worked out", ""] + [f"{i}. {st['text']}" for i, st in enumerate(succ["steps"], 1)]
    if succ.get("unresolved"):
        lines += ["", "**Not resolved:** " + "; ".join(f"{u['person']} — {u['reason']}" for u in succ["unresolved"])]
    lines += ["", "## Documents enclosed", "", "| Document | Status |", "|---|---|"]
    checklist = [
        ("death_certificate", "Death certificate of the recorded holder"),
        ("legal_heir", region.legal_heir),
        ("ror", f"Current {region.ror} extract"),
        ("encumbrance", region.encumbrance),
        ("sketch", region.sketch),
        ("cultivation", region.cultivation),
    ]
    for kind, label in checklist:
        lines.append(f"| {label} | {'enclosed' if kind in have else '**to obtain**'} |")
    lines.append("| Identity proof of each heir | to enclose |")
    lines.append(f"| Consent letter from each co-heir | {sum(1 for s in succ.get('shares', []) if s['alive'] and s['consent_status'] == 'consented')} of {sum(1 for s in succ.get('shares', []) if s['alive'])} received |")
    issues = conc.get("issues") or []
    if issues:
        lines += ["", "## Differences between the records, for the office's attention", ""]
        lines += [f"- {i['title']} — {i['detail']} (*{i['office']}*)" for i in issues if i["type"] != "deceased_holder"]
    lines += [
        "",
        "We declare that the particulars above are true to the best of our knowledge.",
        "",
        "Signatures of the legal heirs: ____________________",
        "",
        f"Date: {datetime.now(tz=UTC).date().isoformat()}",
    ]
    return "\n".join(lines) + "\n"


def consent_letter(*, heir: str, share: str, survey: str, village: str, holder: str, others: list[str]) -> str:
    return (
        f"# Consent letter\n\nI, {heir}, one of the legal heirs of {holder}, consent to the {survey and 'land in survey number ' + survey} "
        f"in {village} village being recorded in the names of the legal heirs, namely {', '.join(others)}, with my share of {share} as "
        "set out in the draft family tree. This consent does not release or transfer my share to anyone.\n\n"
        "Signature: ____________________   Date: ____________\n"
    )


def passport_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"GWP-{raw[:4]}-{raw[4:]}"


def passport_payload(*, case_ref: str, parcel: Any, region: Region, conc: dict[str, Any], succ: dict[str, Any], val: dict[str, Any], ready: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "groundwork.land-passport/1",
        "issued_at": datetime.now(tz=UTC).isoformat(),
        "case_reference": case_ref,
        "region": region.code,
        "plot": {
            "district": parcel.district if parcel else "", "taluk": parcel.taluk if parcel else "", "village": parcel.village if parcel else "",
            "survey_number": conc.get("survey_key"), "extent_sqm": parcel.extent_sqm if parcel else None, "land_class": parcel.land_class if parcel else "",
        },
        "recorded_holders": conc.get("holders", []),
        "deceased_holders": conc.get("deceased_holders", []),
        "draft_co_owners": [{"name": s["name"], "share": s["share"], "consent": s["consent_status"]} for s in succ.get("shares", []) if s["alive"]],
        "succession_law": succ.get("law_name"),
        "record_disagreements": [{"type": i["type"], "severity": i["severity"], "title": i["title"]} for i in conc.get("issues", [])],
        "encumbrance_years": conc.get("ec_years"),
        "valuation": {k: val.get(k) for k in ("status", "p10_inr", "p50_inr", "p90_inr", "lending_value_inr", "confidence", "effective_comparables", "title_haircut")},
        "readiness": {"lender": ready.get("lender_label"), "score": ready.get("score"), "verdict": ready.get("verdict"), "open": [r["label"] for r in ready.get("requirements", []) if r["status"] in ("missing", "blocked")]},
        "boundaries_of_this_document": "Prepared from the records in the case. Ownership is decided by the revenue authority and the courts; the valuation is a range, not a guarantee.",
    }


def passport_markdown(code: str, sha: str, p: dict[str, Any]) -> str:
    v = p["valuation"]
    lines = [
        f"# Land credit passport {code}",
        "",
        f"Issued {p['issued_at'][:10]} · case {p['case_reference']} · SHA-256 `{sha}`",
        "",
        f"Verify: `GET /api/v1/public/passports/{code}?sha={sha}`",
        "",
        "## The plot",
        f"Survey number **{p['plot']['survey_number']}**, {p['plot']['village']} village, {p['plot']['taluk']} taluk, {p['plot']['district']} district ({p['region']}).",
        "",
        "## Holders",
        f"Recorded: {', '.join(p['recorded_holders']) or '—'}" + (f" (deceased: {', '.join(p['deceased_holders'])})" if p["deceased_holders"] else ""),
        "",
        "| Draft co-owner | Share | Consent |",
        "|---|---|---|",
        *[f"| {o['name']} | {o['share']} | {o['consent']} |" for o in p["draft_co_owners"]],
        "",
        "## Value (a range, with confidence)",
    ]
    if v.get("status") == "estimated":
        lines += [
            f"P10 {inr(v['p10_inr'])} · P50 {inr(v['p50_inr'])} · P90 {inr(v['p90_inr'])} — confidence {v['confidence']} "
            f"({v['effective_comparables']} effective comparables).",
            f"Conservative lending value: **{inr(v['lending_value_inr'])}** (20th percentile less a {round(100 * (v['title_haircut'] or 0))}% title haircut).",
        ]
    else:
        lines.append("Not estimated.")
    lines += ["", "## Readiness", f"{p['readiness']['lender']}: score {p['readiness']['score']}/100 — {p['readiness']['verdict'].replace('_', ' ')}."]
    if p["readiness"]["open"]:
        lines += ["Still open: " + "; ".join(p["readiness"]["open"]) + "."]
    if p["record_disagreements"]:
        lines += ["", "## Where the records disagree", *[f"- [{d['severity']}] {d['title']}" for d in p["record_disagreements"]]]
    lines += ["", f"*{p['boundaries_of_this_document']}*"]
    return "\n".join(lines) + "\n"
