"""Normalisation shared by every module that compares facts from different documents.

Names, dates, money, parcel numbers and addresses arrive in as many shapes as there are
county clerks. Each is reduced here to one comparable form, and the matching rules are
written to *under*-match: a missed match becomes a reviewable finding, a false one hides
a real discrepancy.
"""

from __future__ import annotations

import re

from app.domain.reconciliation.engine.candidates import edit_distance, normalise_reference
from app.domain.reconciliation.engine.parse import parse_date, parse_minor_units

ORG_TOKENS = {
    "LLC", "INC", "CORP", "CORPORATION", "COMPANY", "CO", "LP", "LLP", "LTD", "BANK", "TRUST",
    "AUTHORITY", "COUNTY", "CITY", "DEPARTMENT", "ASSOCIATION", "PARTNERS", "TREASURER",
    "COLLECTOR", "UTILITY", "UTILITIES", "BOARD", "STATE", "MORTGAGE", "FINANCIAL", "SERVICES",
}
_NOISE = re.compile(
    r"\b(?:ET\s*AL|ET\s*UX|ET\s*VIR|ETAL|ETUX|JTWROS|L/E|LIFE\s+ESTATE|HEIRS?\s+OF|ESTATE\s+OF|"
    r"MR|MRS|MS|DR|DECEASED|DEC'?D)\b\.?",
    re.IGNORECASE,
)
_DESCRIPTORS = re.compile(
    r",?\s*\b(?:husband\s+and\s+wife|wife\s+and\s+husband|a\s+married\s+(?:man|woman|couple)|"
    r"an?\s+(?:single|unmarried)\s+(?:man|woman|person)|as\s+joint\s+tenants(?:\s+with\s+(?:full\s+)?right\s+of\s+survivorship)?|"
    r"as\s+tenants\s+(?:in\s+common|by\s+the\s+entirety)|(?:his|her|their|its)\s+heirs(?:,)?\s+(?:successors\s+)?and\s+assigns|"
    r"forever|individually)\b",
    re.IGNORECASE,
)


def is_organization(name: str) -> bool:
    tokens = set(re.sub(r"[^A-Z ]", " ", name.upper()).split())
    return bool(tokens & ORG_TOKENS)


def normalize_name(name: str) -> str:
    text = _NOISE.sub(" ", name.upper())
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return " ".join(text.split())


def name_tokens(name: str) -> set[str]:
    return set(normalize_name(name).split())


def names_match(a: str, b: str) -> bool:
    """Same person or organisation? Order-insensitive ("PARKER ERNEST L" == "Ernest L. Parker").

    Initials are ignored when one side omits them, and a single-character typo is tolerated
    on long names. A surname alone never matches a full name.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    full_a = {t for t in ta if len(t) > 1}
    full_b = {t for t in tb if len(t) > 1}
    if full_a and full_a == full_b:
        return True
    smaller, larger = (full_a, full_b) if len(full_a) <= len(full_b) else (full_b, full_a)
    if len(smaller) >= 2 and smaller <= larger and len(larger) - len(smaller) <= 1 and not is_organization(a + " " + b):
        return True
    joined_a, joined_b = " ".join(sorted(full_a)), " ".join(sorted(full_b))
    if min(len(joined_a), len(joined_b)) >= 10:
        return edit_distance(joined_a, joined_b, 1) is not None
    return False


def split_names(text: str) -> list[str]:
    """``"Ernest L. Parker and Lula M. Parker, husband and wife"`` → two names."""
    cleaned = _DESCRIPTORS.sub("", text).strip(" ,;.")
    if not cleaned:
        return []
    parts = re.split(r"\s*;\s*", cleaned)
    names: list[str] = []
    for part in parts:
        if is_organization(part):
            names.append(part.strip(" ,."))
            continue
        for piece in re.split(r"\s+(?:and|&)\s+|,\s+(?=[A-Z][a-z]+\s+[A-Z])", part):
            piece = piece.strip(" ,.")
            if len(re.sub(r"[^A-Za-z]", "", piece)) >= 3:
                names.append(piece)
    return names


_MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("JAN", "JANUARY"), ("FEB", "FEBRUARY"), ("MAR", "MARCH"), ("APR", "APRIL"), ("MAY",),
            ("JUN", "JUNE"), ("JUL", "JULY"), ("AUG", "AUGUST"), ("SEP", "SEPT", "SEPTEMBER"),
            ("OCT", "OCTOBER"), ("NOV", "NOVEMBER"), ("DEC", "DECEMBER"),
        ],
        start=1,
    )
    for name in names
}
DATE_PATTERN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*,?\s+\d{4})"
)


def parse_date_text(text: str) -> str | None:
    """US-format dates to ISO. Numeric dates are read month-first (US county records)."""
    text = text.strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        decoded = parse_date(match.group(0), "ISO")
        return decoded.value if decoded.ok else None
    match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b", text)
    if match:
        decoded = parse_date(match.group(0), "MDY")
        return decoded.value if decoded.ok else None
    match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", text)
    if match and match.group(1).upper() in _MONTHS:
        return _iso(int(match.group(3)), _MONTHS[match.group(1).upper()], int(match.group(2)))
    match = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([A-Za-z]{3,9}),?\s+(\d{4})", text)
    if match and match.group(2).upper() in _MONTHS:
        return _iso(int(match.group(3)), _MONTHS[match.group(2).upper()], int(match.group(1)))
    return None


def _iso(year: int, month: int, day: int) -> str | None:
    decoded = parse_date(f"{year:04d}-{month:02d}-{day:02d}", "ISO")
    return decoded.value if decoded.ok else None


def parse_money_text(text: str) -> int | None:
    match = re.search(r"\(?-?\$?\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?\)?", text)
    if not match:
        return None
    decoded = parse_minor_units(match.group(0))
    return decoded.value if decoded.ok else None


def normalize_parcel(text: str) -> str:
    return normalise_reference(text)


_ADDRESS_ABBREVIATIONS = {
    "STREET": "ST", "ROAD": "RD", "AVENUE": "AVE", "DRIVE": "DR", "LANE": "LN", "BOULEVARD": "BLVD",
    "HIGHWAY": "HWY", "COURT": "CT", "PLACE": "PL", "CIRCLE": "CIR", "PARKWAY": "PKWY", "TERRACE": "TER",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "ROUTE": "RTE", "APARTMENT": "APT", "SUITE": "STE",
}


def normalize_address(text: str) -> str:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", text.upper()).split()
    return " ".join(_ADDRESS_ABBREVIATIONS.get(token, token) for token in tokens)


def street_part(normalized_address: str) -> str:
    """House number and street name: the part two records of one property must share."""
    tokens = normalized_address.split()
    for suffix in ("ST", "RD", "AVE", "DR", "LN", "BLVD", "HWY", "CT", "PL", "CIR", "PKWY", "TER", "RTE", "WAY"):
        if suffix in tokens:
            return " ".join(tokens[: tokens.index(suffix) + 1])
    return " ".join(tokens[:3])
