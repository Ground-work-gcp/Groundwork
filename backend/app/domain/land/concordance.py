"""Record concordance: where the patta, the adangal, the sketch, the EC and the deeds disagree.

The village accountant, the taluk office, the survey office, the Sub-Registrar and the family's
own deeds each hold one piece of the picture. This module lays those pieces side by side for
one plot and reports, attribute by attribute, where they agree, where they differ, and **which
office can correct each difference** — so the family knows where to go, not just that
something is wrong.

It also checks the plot's boundaries against its neighbours' records: if our patta says the
land to the east is S.No. 124, then S.No. 124's own record should say that we lie to its west.
A one-sided boundary is the usual seed of a boundary dispute.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from typing import Any

from .names import similarity
from .regions import Region
from .units import format_extent, survey_parent, surveys_in

SOURCE_LABELS = {
    "ror": "record of rights", "cultivation": "cultivation record", "sketch": "survey sketch", "encumbrance": "encumbrance certificate",
    "sale_deed": "sale / title deed", "legal_heir": "legal heir certificate", "death_certificate": "death certificate",
    "tax_receipt": "land tax receipt", "field_survey": "field measurement", "legal_opinion": "legal scrutiny report", "other": "other record",
}
# Records whose holder field names the *owner* (a cultivation record names the cultivator).
OWNER_SOURCES = {"ror", "sale_deed", "tax_receipt", "sketch"}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
DIRECTIONS = ("north", "east", "south", "west")
MATCH = 0.86


def _label(r: Any, region: Region) -> str:
    named = {"ror": region.ror, "cultivation": region.cultivation, "sketch": region.sketch, "encumbrance": region.encumbrance}
    return named.get(r.source_type, SOURCE_LABELS.get(r.source_type, r.source_type))


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _ref(r: Any, region: Region) -> dict[str, Any]:
    return {"record_id": r.id, "source_type": r.source_type, "label": _label(r, region), "document_id": r.document_id, "page": r.page_number, "date": r.record_date}


def _names_overlap(a: list[str], b: list[str]) -> tuple[float, str]:
    best, why = 0.0, "no names"
    for x in a:
        for y in b:
            score, reason = similarity(x, y)
            if score > best:
                best, why = score, reason
    return best, why


def shoelace(points: list[list[float]]) -> float | None:
    if not points or len(points) < 3:
        return None
    try:
        area = 0.0
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
            area += float(x1) * float(y2) - float(x2) * float(y1)
    except (TypeError, ValueError):
        return None
    return abs(area) / 2


def subject_key(parcel: Any, records: list[Any]) -> str:
    if parcel is not None and parcel.survey_key:
        return parcel.survey_key
    keys = Counter(r.survey_key for r in records if r.survey_key and r.source_type == "ror") or Counter(r.survey_key for r in records if r.survey_key)
    return keys.most_common(1)[0][0] if keys else ""


def analyse(parcel: Any, records: list[Any], members: list[Any], parties: list[Any], region: Region) -> dict[str, Any]:
    key = subject_key(parcel, records)
    parent = survey_parent(key)
    own = [r for r in records if r.survey_key == key or (not r.survey_key and r.source_type in ("legal_heir", "death_certificate"))]
    variants = [r for r in records if r.survey_key and r.survey_key != key and survey_parent(r.survey_key) == parent]
    neighbours = [r for r in records if r.survey_key and survey_parent(r.survey_key) != parent]
    issues: list[dict[str, Any]] = []
    tolerance = region.extent_tolerance

    def issue(kind: str, severity: str, title: str, detail: str, office: str, refs: list[Any], fix: str, key_: str, attribute: str = "") -> None:
        issues.append({
            "type": kind, "severity": severity, "title": title, "detail": detail, "office": office, "attribute": attribute,
            "records": [_ref(r, region) for r in refs], "fix_service": fix, "key": key_,
        })

    # 1 · survey numbers and subdivisions
    for r in variants:
        issue(
            "survey_numbers", "medium", f"{_cap(_label(r, region))} cites S.No. {r.survey_key}, not {key}",
            "A different subdivision usually means a partition or sale was recorded in one office but not the other.",
            region.survey_office, [r], "sketch_copy", f"variant:{r.id}",
        )
    plot = own + variants
    ror = [r for r in plot if r.source_type == "ror"]
    reference = (ror or [r for r in plot if r.extent_sqm])[:1]

    # 2 · extents
    ext_ref = next((r for r in ror if r.extent_sqm), None) or next((r for r in plot if r.extent_sqm), None)
    if ext_ref is not None:
        for r in plot:
            if r is ext_ref or not r.extent_sqm:
                continue
            diff = (r.extent_sqm - ext_ref.extent_sqm) / ext_ref.extent_sqm
            if abs(diff) > tolerance:
                office = region.survey_office if "sketch" in (r.source_type, ext_ref.source_type) or r.source_type == "field_survey" else region.revenue_office
                issue(
                    "extents", "high" if abs(diff) >= 0.10 else "medium",
                    f"Extent differs by {diff * 100:+.1f}% between the {_label(ext_ref, region)} and the {_label(r, region)}",
                    f"{_cap(_label(ext_ref, region))}: {ext_ref.extent_text or format_extent(ext_ref.extent_sqm, region)}; "
                    f"{_label(r, region)}: {r.extent_text or format_extent(r.extent_sqm, region)} (tolerance {tolerance * 100:.0f}%).",
                    office, [ext_ref, r], "survey_measurement", f"extent:{ext_ref.id}:{r.id}",
                )
    # 3 · polygon area against the recorded extent
    for r in plot:
        area = shoelace(r.extra.get("polygon") or []) if isinstance(r.extra, dict) else None
        if area and ext_ref is not None and ext_ref.extent_sqm:
            diff = (area - ext_ref.extent_sqm) / ext_ref.extent_sqm
            if abs(diff) > tolerance:
                issue(
                    "extents", "high" if abs(diff) >= 0.10 else "medium",
                    f"Measured shape gives {area:,.0f} m², {diff * 100:+.1f}% against the recorded extent",
                    f"Area computed from the {len(r.extra['polygon'])} measured corners (shoelace formula) of the {_label(r, region)}.",
                    region.survey_office, [r, ext_ref], "survey_measurement", f"polygon:{r.id}",
                )

    # 4 · names
    holder_ref = next((r for r in ror if r.holders), None)
    holders = list(holder_ref.holders) if holder_ref else ([parcel.recorded_holder] if parcel is not None and parcel.recorded_holder else [])
    if holders:
        for r in plot:
            if r is holder_ref or not r.holders or r.source_type not in OWNER_SOURCES | {"cultivation"}:
                continue
            score, why = _names_overlap(holders, r.holders)
            if score >= MATCH:
                continue
            if r.source_type == "cultivation":
                issue(
                    "names", "low", f"The {_label(r, region)} names a different cultivator ({', '.join(r.holders)})",
                    f"Holder of record: {', '.join(holders)}. A different cultivator is common after a death or a lease; it is not ownership.",
                    region.village_official, [r] + ([holder_ref] if holder_ref else []), "cultivation_record", f"cultivator:{r.id}",
                )
            else:
                issue(
                    "names", "medium" if score >= 0.7 else "high",
                    f"Holder name differs between the {_label(holder_ref, region) if holder_ref else 'family account'} and the {_label(r, region)}",
                    f"{', '.join(holders)} vs {', '.join(r.holders)} — {why} (similarity {score:.2f}).",
                    region.revenue_office, [r] + ([holder_ref] if holder_ref else []), "mutation", f"name:{r.id}",
                )
    # 5 · a deceased person still on the record
    deceased: list[tuple[str, str, str]] = []  # (holder, matched person, date)
    for h in holders:
        for m in members:
            if not m.is_alive and similarity(h, m.name)[0] >= MATCH:
                deceased.append((h, m.name, m.date_of_death or ""))
                break
        else:
            for p in parties:
                if p.is_deceased and similarity(h, p.name)[0] >= MATCH:
                    deceased.append((h, p.name, p.date_of_death or ""))
                    break
            else:
                for r in own:
                    if r.source_type == "death_certificate" and r.holders and _names_overlap([h], r.holders)[0] >= MATCH:
                        deceased.append((h, r.holders[0], r.record_date or ""))
                        break
    for h, who, date in deceased:
        years = ""
        if date and holder_ref and holder_ref.record_date and holder_ref.record_date > date:
            years = f" The record dated {holder_ref.record_date} still shows them, {int(holder_ref.record_date[:4]) - int(date[:4])} years after the death."
        issue(
            "deceased_holder", "critical", f"Land still recorded in the name of {h}, who died{' on ' + date if date else ''}",
            f"Matched to {who}.{years} Until the heirs are recorded by {region.mutation.lower()}, no one can sell, partition or pledge the land cleanly.",
            region.revenue_office, [holder_ref] if holder_ref else [], "mutation", f"deceased:{who}",
        )

    # 6 · boundaries within the plot's own records
    statements: dict[str, list[tuple[Any, str]]] = {d: [] for d in DIRECTIONS}
    for r in plot:
        for d in DIRECTIONS:
            text = (r.boundaries or {}).get(d) if isinstance(r.boundaries, dict) else None
            if text:
                statements[d].append((r, str(text)))
    for d, said in statements.items():
        for i in range(len(said)):
            for j in range(i + 1, len(said)):
                (ra, ta), (rb, tb) = said[i], said[j]
                sa, sb = surveys_in(ta), surveys_in(tb)
                agree = bool(sa & sb) if sa and sb else similarity(ta, tb)[0] >= 0.8 or ta.strip().lower() == tb.strip().lower()
                if not agree:
                    issue(
                        "boundaries", "high", f"{_cap(d)} boundary differs between the {_label(ra, region)} and the {_label(rb, region)}",
                        f"“{ta}” vs “{tb}”.", region.survey_office, [ra, rb], "survey_measurement", f"boundary:{d}:{ra.id}:{rb.id}", attribute=d,
                    )

    # 7 · reciprocity with the neighbours' own records
    edges: list[dict[str, Any]] = []
    by_key: dict[str, list[Any]] = {}
    for r in neighbours:
        by_key.setdefault(r.survey_key, []).append(r)
    for d, said in statements.items():
        for r, text in said:
            for n in sorted(surveys_in(text)):
                status, detail = "unknown", "no record of the neighbour in this case"
                theirs = by_key.get(n) or by_key.get(survey_parent(n)) or []
                for nr in theirs:
                    back = (nr.boundaries or {}).get(OPPOSITE[d]) if isinstance(nr.boundaries, dict) else None
                    if not back:
                        continue
                    mentions = {survey_parent(s) for s in surveys_in(str(back))}
                    if parent in mentions:
                        status, detail = "reciprocal", f"S.No. {n} says {OPPOSITE[d]}: “{back}”"
                        break
                    status, detail = "conflict", f"S.No. {n} says {OPPOSITE[d]}: “{back}”, not S.No. {key}"
                    issue(
                        "boundaries", "high", f"S.No. {n} does not acknowledge this plot on its {OPPOSITE[d]} side",
                        f"Our {_label(r, region)} puts S.No. {n} to the {d}; {detail}. One-sided boundaries are where disputes start.",
                        region.survey_office, [r, nr], "survey_measurement", f"reciprocity:{d}:{n}", attribute=d,
                    )
                if not any(e["direction"] == d and e["neighbour"] == n for e in edges):
                    edges.append({"direction": d, "neighbour": n, "status": status, "detail": detail, "record_id": r.id})
            if not surveys_in(text) and not any(e["direction"] == d for e in edges):
                edges.append({"direction": d, "neighbour": text, "status": "named", "detail": "boundary given by name, not survey number", "record_id": r.id})

    # 8 · land class, and government land
    classes = {(r.land_class or ""): r for r in plot if r.land_class}
    if parcel is not None and parcel.land_class:
        classes.setdefault(parcel.land_class, None)
    if "government" in classes:
        r = classes["government"]
        issue(
            "land_class", "critical", "A record classifies the land as government land (poramboke / gairan)",
            "Government land cannot be sold, inherited as private land or mortgaged. Confirm the classification with the survey and revenue offices.",
            region.revenue_office, [r] if r is not None else [], "survey_measurement", "government",
        )
    real = {c for c in classes if c != "government"}
    if len(real) > 1:
        issue(
            "land_class", "medium", f"Records disagree on the kind of land: {', '.join(sorted(real))}",
            "Wet, dry and garden land are valued and taxed differently; the record of rights usually governs.",
            region.revenue_office, [r for r in classes.values() if r is not None], "cultivation_record", "class",
        )

    # 9 · encumbrances (EC)
    ec_years = 0.0
    for r in plot:
        if r.source_type != "encumbrance" or not isinstance(r.extra, dict):
            continue
        a, b = r.extra.get("ec_from"), r.extra.get("ec_to")
        if a and b:
            with contextlib.suppress(ValueError):
                ec_years = max(ec_years, int(str(b)[:4]) - int(str(a)[:4]) + (int(str(b)[5:7] or 1) - int(str(a)[5:7] or 1)) / 12)
        for e in r.extra.get("encumbrances") or []:
            kind = str(e.get("kind", "")).lower()
            if kind in ("mortgage", "attachment", "court_order", "lease", "agreement_of_sale") and str(e.get("status", "open")).lower() != "released":
                issue(
                    "encumbrance", "high", f"Open {kind.replace('_', ' ')} on the EC{' in favour of ' + str(e['party']) if e.get('party') else ''}",
                    f"Registered {e.get('date', '')}. A lender will not take a second charge until it is released or explained.",
                    region.registration_office, [r], "encumbrance_certificate", f"enc:{r.id}:{kind}:{e.get('date', '')}",
                )

    # the side-by-side matrix
    columns = [_ref(r, region) | {"survey": r.survey_key} for r in plot]
    rows = []
    for attr in ("survey", "extent", "holders", "land_class", *DIRECTIONS):
        cells = []
        for r in plot:
            if attr == "survey":
                v = r.survey_key
            elif attr == "extent":
                v = r.extent_text or (format_extent(r.extent_sqm, region) if r.extent_sqm else "")
            elif attr == "holders":
                v = ", ".join(r.holders or [])
            elif attr == "land_class":
                v = r.land_class
            else:
                v = (r.boundaries or {}).get(attr, "") if isinstance(r.boundaries, dict) else ""
            flagged = any(
                r.id in [x["record_id"] for x in i["records"]] and (i["attribute"] == attr if i["attribute"] else _attr_of(i["type"], attr))
                for i in issues
            )
            cells.append({"record_id": r.id, "value": v or "", "status": "missing" if not v else "differs" if flagged else "agrees"})
        rows.append({"attribute": attr, "cells": cells})

    counts = Counter(i["type"] for i in issues)
    return {
        "survey_key": key, "region": region.code, "records_compared": len(plot), "neighbour_records": len(neighbours),
        "columns": columns, "rows": rows, "issues": sorted(issues, key=lambda i: ("critical", "high", "medium", "low").index(i["severity"])),
        "counts": {k: counts.get(k, 0) for k in ("names", "extents", "boundaries", "survey_numbers", "land_class", "deceased_holder", "encumbrance")},
        "boundary_edges": edges, "ec_years": round(ec_years, 1), "deceased_holders": [d[0] for d in deceased],
        "holders": holders, "reference_record": reference[0].id if reference else None,
    }


def _attr_of(issue_type: str, attr: str) -> bool:
    return {
        "survey_numbers": attr == "survey", "extents": attr == "extent", "names": attr == "holders", "deceased_holder": attr == "holders",
        "land_class": attr == "land_class", "boundaries": attr in DIRECTIONS,
    }.get(issue_type, False)
