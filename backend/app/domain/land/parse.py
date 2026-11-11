"""Reading Indian land records out of uploaded documents (``land-rules:v1``).

Patta / chitta, adangal, RTC, 7/12, khatauni, FMB sketches, encumbrance certificates, sale
deeds, death and legal heir certificates are forms, so labels carry most of the meaning:
``Survey No:``, ``Extent:``, ``Name of the Pattadar:``, ``North:``. Each derived record keeps the
document, the page and the line it came from, and is marked ``extracted`` so a person reviews
it before it is relied on. Dates are read day-first, as Indian records write them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .regions import Region
from .units import land_class, parse_extent, survey_key

KINDS: list[tuple[str, tuple[str, ...]]] = [
    ("encumbrance", ("encumbrance certificate", "statement of encumbrance", "index-ii", "index ii", "form no. 15", "form no. 16")),
    ("legal_heir", ("legal heir certificate", "legal heirship", "family member certificate", "varasat", "survivorship certificate")),
    ("death_certificate", ("certificate of death", "death certificate", "name of the deceased")),
    ("legal_opinion", ("legal scrutiny", "legal opinion", "title opinion")),
    ("sketch", ("field measurement book", "fmb", "survey sketch", "hissa", "tippani", "shajra", "bhu-naksha", "bhu naksha")),
    ("cultivation", ("adangal", "a-register", "khasra", "name of the cultivator", "crop")),
    ("tax_receipt", ("kist", "land revenue receipt", "lagan", "land tax receipt", "receipt for land revenue")),
    ("sale_deed", ("sale deed", "deed of sale", "conveyance deed", "purchaser", "vendee")),
    ("ror", ("patta", "chitta", "record of rights", "rtc", "pahani", "7/12", "satbara", "khatauni", "ror-1b", "1-b", "jamabandi")),
]

_L = r"^\s*(?:\d+[.)]\s*)?"
LABELS: dict[str, str] = {
    "survey": _L + r"(?:r\.?\s*s\.?|s\.?\s*f\.?|sy\.?|survey|khasra|gat|re-?survey)\s*(?:no\.?|number)\s*[:.\-]?\s*(.+)$",
    "subdivision": _L + r"(?:sub-?division|hissa)\s*(?:no\.?)?\s*[:.\-]\s*(.+)$",
    "extent": _L + r"(?:extent|area|total\s+extent)\s*(?:\([^)]*\))?\s*[:.\-]\s*(.+)$",
    "holder": _L + r"(?:name\s+of\s+(?:the\s+)?(?:pattadar|holder|owner|khatedar|occupant)|pattadar(?:'s)?\s+name|pattadar|holder|owner|khatedar|occupant)\s*(?:\(s\))?\s*[:.\-]\s*(.+)$",
    "cultivator": _L + r"(?:name\s+of\s+(?:the\s+)?cultivator|cultivator)\s*[:.\-]\s*(.+)$",
    "deceased": _L + r"(?:name\s+of\s+(?:the\s+)?deceased|deceased)\s*[:.\-]\s*(.+)$",
    "purchaser": _L + r"(?:purchaser|vendee|buyer|claimant)\s*(?:\(s\))?\s*[:.\-]\s*(.+)$",
    "vendor": _L + r"(?:vendor|seller|executant)\s*(?:\(s\))?\s*[:.\-]\s*(.+)$",
    "class": _L + r"(?:classification|nature\s+of\s+land|land\s+(?:type|class|classification)|tharam)\s*[:.\-]\s*(.+)$",
    "date": _L + r"(?:date\s+of\s+(?:issue|death|execution|registration)|date|dated|issued\s+on)\s*[:.\-]\s*(.+)$",
    "office": _L + r"(?:issued\s+by|office|issuing\s+office)\s*[:.\-]\s*(.+)$",
    "village": r"\bvillage\s*[:.\-]\s*([^:]+?)(?:\s{2,}|\s+taluk\b|\s+district\b|$)",
    "taluk": r"\btaluk\s*[:.\-]\s*([^:]+?)(?:\s{2,}|\s+village\b|\s+district\b|$)",
    "district": r"\bdistrict\s*[:.\-]\s*([^:]+?)(?:\s{2,}|\s+taluk\b|$)",
    "patta": _L + r"(?:patta|khata|khatauni|account)\s*(?:no\.?|number)\s*[:.\-]\s*(.+)$",
    "consideration": _L + r"(?:consideration|sale\s+price|market\s+value\s+declared)\s*[:.\-]\s*(.+)$",
    "period": _L + r"(?:period|ec\s+period|search\s+period)\s*[:.\-]?\s*(?:from\s+)?(.+?)\s+(?:to|till|-)\s+(.+)$",
    "tax_year": _L + r"(?:tax\s+year|fasli|year)\s*[:.\-]\s*(.+)$",
    "heirs": _L + r"(?:legal\s+heirs?|heirs?)\s*[:.\-]\s*(.*)$",
    "corners": _L + r"(?:corners|coordinates|vertices)\s*(?:\([^)]*\))?\s*[:.\-]\s*(.+)$",
}
BOUNDARY = re.compile(_L + r"(north|east|south|west)(?:ern)?\s*(?:side|boundary)?\s*(?:by|[:.\-])\s*(.+)$", re.I)
ENTRY = re.compile(
    r"(agreement\s+of\s+sale|mortgage|lease|sale|gift|settlement|partition|release|attachment|court\s+order)\s+(?:deed\s+)?(?:by\s+(.+?)\s+)?in\s+favou?r\s+of\s+(.+?)\s+dated\s+([0-9./-]+)(.*)$",
    re.I,
)


def parse_date_dmy(text: str) -> str | None:
    t = (text or "").strip()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", t)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_inr(text: str) -> int | None:
    m = re.search(r"(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*)(?:\.\d{1,2})?\s*(lakhs?|lacs?|crores?)?", (text or "").lower())
    if not m:
        return None
    n = int(m.group(1).replace(",", ""))
    unit = m.group(2) or ""
    return n * (10_000_000 if unit.startswith("crore") else 100_000 if unit.startswith(("lakh", "lac")) else 1)


def classify(text: str) -> str:
    t = text.lower()
    best, score = "other", 0
    for kind, words in KINDS:
        s = sum(t.count(w) * (3 if i == 0 else 1) for i, w in enumerate(words))
        if s > score:
            best, score = kind, s
    return best


def _names(value: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\band\b|&)\s*", value.strip())
    return [p.strip(" .") for p in parts if len(p.strip(" .")) >= 2]


def parse(pages: list[tuple[int, str]], region: Region) -> dict[str, Any] | None:
    """One record from a document's pages, or None if it does not look like a land record."""
    full = "\n".join(text for _, text in pages)
    kind = classify(full)
    if kind == "other":
        return None
    found: dict[str, tuple[str, int, str]] = {}
    boundaries: dict[str, str] = {}
    encumbrances: list[dict[str, Any]] = []
    heirs: list[str] = []
    in_heirs = False
    for page, text in pages:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                in_heirs = False
                continue
            b = BOUNDARY.match(line)
            if b and b.group(1).lower() not in boundaries:
                boundaries[b.group(1).lower()] = b.group(2).strip()
                continue
            e = ENTRY.search(line)
            if e and kind == "encumbrance":
                tail = e.group(5) or ""
                released = re.search(r"(released|discharged|satisfied|cancelled|redeemed)\s*(?:on)?\s*([0-9./-]+)?", tail, re.I)
                encumbrances.append({
                    "kind": re.sub(r"\s+", "_", e.group(1).lower()), "by": (e.group(2) or "").strip(" ,"), "party": e.group(3).strip(" ,"),
                    "date": parse_date_dmy(e.group(4)), "status": "released" if released else "open",
                    "released_on": parse_date_dmy(released.group(2)) if released and released.group(2) else None,
                })
                continue
            if in_heirs:
                m = re.match(r"^\s*(?:\d+[.)]\s*)?([A-Za-z஀-௿ऀ-ॿ .]+?)\s*(?:[-–(,]|$)", line)
                if m and len(m.group(1).strip()) >= 2:
                    heirs.append(m.group(1).strip())
                    continue
                in_heirs = False
            for label, pattern in LABELS.items():
                if label in found and label not in ("date",):
                    continue
                m = re.search(pattern, line, re.I)
                if m:
                    if label == "heirs":
                        in_heirs = True
                        heirs.extend(_names(m.group(1)) if m.group(1).strip() else [])
                    elif label == "period":
                        found["period"] = (f"{m.group(1)}|{m.group(2)}", page, line)
                    elif label == "date" and "date" in found:
                        pass
                    else:
                        found[label] = (m.group(1).strip(), page, line)
    if "survey" not in found and kind not in ("death_certificate", "legal_heir"):
        return None

    def val(label: str) -> str:
        return found[label][0] if label in found else ""

    survey = val("survey")
    if val("subdivision") and "/" not in survey:
        survey = f"{survey}/{val('subdivision')}"
    holders: list[str]
    if kind == "cultivation":
        holders = _names(val("cultivator") or val("holder"))
    elif kind == "death_certificate" or kind == "legal_heir":
        holders = _names(val("deceased"))
    elif kind == "sale_deed":
        holders = _names(val("purchaser"))
    else:
        holders = _names(val("holder"))
    extra: dict[str, Any] = {}
    if encumbrances:
        extra["encumbrances"] = encumbrances
    if "period" in found:
        a, b = found["period"][0].split("|")
        extra["ec_from"], extra["ec_to"] = parse_date_dmy(a), parse_date_dmy(b)
    if heirs:
        extra["heirs"] = heirs
    if val("vendor"):
        extra["vendors"] = _names(val("vendor"))
    if val("consideration"):
        extra["consideration_inr"] = parse_inr(val("consideration"))
    if val("tax_year"):
        extra["tax_year"] = val("tax_year")
    if val("patta"):
        extra["patta_number"] = val("patta")
    for label in ("village", "taluk", "district"):
        if val(label):
            extra[label] = val(label).strip(" .,")
    if val("corners"):
        pts = re.findall(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", val("corners"))
        if len(pts) >= 3:
            extra["polygon"] = [[float(x), float(y)] for x, y in pts]
    first = found.get("survey") or found.get("deceased") or next(iter(found.values()), ("", pages[0][0] if pages else 1, ""))
    extent_text = val("extent")
    return {
        "source_type": kind,
        "page_number": first[1],
        "snippet": first[2][:300],
        "survey_number": survey,
        "survey_key": survey_key(survey),
        "extent_text": extent_text,
        "extent_sqm": parse_extent(extent_text, region),
        "holders": holders,
        "boundaries": boundaries,
        "land_class": land_class(val("class") or (full if kind in ("ror", "cultivation") else ""), region),
        "record_date": parse_date_dmy(val("date")),
        "issuing_office": val("office"),
        "extra": extra,
    }
