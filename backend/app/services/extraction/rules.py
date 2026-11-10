"""Deterministic fact extraction from page text (``rules:v1``).

Label-driven first (county records are forms: ``Grantor: …``, ``Parcel No.: …``), with a
small number of prose patterns at lower confidence. Every fact keeps the page it came from
and the exact line that produced it, so a reviewer can check it against the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.evidence import (
    DATE_PATTERN,
    normalize_address,
    normalize_name,
    normalize_parcel,
    parse_date_text,
    parse_money_text,
    split_names,
)

EXTRACTOR = "rules:v1"

NAME_FACTS = {
    "grantor", "grantee", "owner_of_record", "decedent", "heir", "devisee", "executor",
    "lien_holder", "lien_debtor", "taxing_authority", "mortgagee",
}
DATE_FACTS = {"recording_date", "document_date", "date_of_death", "due_date"}
MONEY_FACTS = {"amount_due", "lien_amount", "consideration_amount"}
REFERENCE_FACTS = {"instrument_number", "bill_number", "lien_reference"}
FACT_TYPES = sorted(
    NAME_FACTS | DATE_FACTS | MONEY_FACTS | REFERENCE_FACTS
    | {"parcel_number", "property_address", "tax_year", "tax_status", "book_page", "legal_description", "instrument_type"}
)

DOC_TYPES = (
    "deed", "will", "probate", "death_certificate", "tax_record", "lien", "mortgage",
    "financial_statement", "other",
)


@dataclass
class FactCandidate:
    fact_type: str
    value: str
    normalized: str
    page: int
    snippet: str
    confidence: float
    extractor: str = EXTRACTOR


# (label regex, fact type, confidence) — specific labels before general ones.
LABELS: list[tuple[str, str, float]] = [
    (r"grantors?(?:\s*\(s\))?", "grantor", 0.9),
    (r"grantees?(?:\s*\(s\))?", "grantee", 0.9),
    (r"(?:apn|a\.p\.n\.|parcel(?:\s+(?:id|no\.?|number|#))?|tax\s+(?:map|parcel)\s+(?:no\.?|number|id)|pin)", "parcel_number", 0.9),
    (r"(?:property|situs|site|premises)\s+address|address\s+of\s+(?:the\s+)?property|property\s+location", "property_address", 0.85),
    (r"(?:date\s+)?recorded(?:\s+on)?|recording\s+date|date\s+of\s+recording|filed(?:\s+on)?", "recording_date", 0.9),
    (r"(?:instrument|document|doc\.?|reception|recording)\s+(?:no\.?|number|#)", "instrument_number", 0.9),
    (r"lien\s+(?:no\.?|number|#|reference)", "lien_reference", 0.9),
    (r"book\s*/\s*page|book\s+and\s+page", "book_page", 0.85),
    (r"owner(?:\s*\(s\))?\s+of\s+record|record\s+owner|assessed\s+owner|owner\s+name|property\s+owner(?:\s*\(s\))?|owner(?:\s*\(s\))?", "owner_of_record", 0.85),
    (r"tax\s+year", "tax_year", 0.9),
    (r"(?:total\s+)?(?:amount|balance)\s+due|total\s+(?:tax\s+)?due|delinquent\s+amount|amount\s+owed", "amount_due", 0.9),
    (r"(?:tax\s+)?bill\s+(?:no\.?|number|#)|account\s+(?:no\.?|number|#)|statement\s+(?:no\.?|number|#)", "bill_number", 0.9),
    (r"due\s+date|payment\s+due(?:\s+date)?|due\s+by|pay\s+by", "due_date", 0.85),
    (r"(?:tax\s+|payment\s+)?status", "tax_status", 0.75),
    (r"date\s+of\s+death|died\s+on", "date_of_death", 0.95),
    (r"(?:name\s+of\s+)?(?:decedent|deceased)|testator|testatrix", "decedent", 0.9),
    (r"heirs?(?:\s+at\s+law)?", "heir", 0.85),
    (r"devisees?|beneficiar(?:y|ies)", "devisee", 0.85),
    (r"executor|executrix|personal\s+representative|administrat(?:or|rix)", "executor", 0.85),
    (r"lien\s*holder|lien\s+claimant|claimant|creditor", "lien_holder", 0.85),
    (r"(?:lien\s+)?debtor|owner\s+liable", "lien_debtor", 0.8),
    (r"lien\s+amount|amount\s+of\s+(?:the\s+)?lien|principal\s+amount", "lien_amount", 0.9),
    (r"mortgagee|lender|beneficiary\s+under\s+deed\s+of\s+trust", "mortgagee", 0.8),
    (r"taxing\s+authority|tax\s+collector|collector|treasurer", "taxing_authority", 0.8),
    (r"consideration", "consideration_amount", 0.8),
    (r"legal\s+description", "legal_description", 0.8),
    (r"dated|date\s+of\s+(?:deed|instrument|will|order|execution)|executed\s+on|date\s+signed", "document_date", 0.75),
]
_LABEL_RES = [
    (re.compile(rf"^\s*(?:{pattern})\s*[:#]\s*(?P<value>.+?)\s*$", re.IGNORECASE), fact_type, confidence)
    for pattern, fact_type, confidence in LABELS
]

_PROSE: list[tuple[re.Pattern[str], dict[str, str], float]] = [
    (
        re.compile(
            r"(?P<grantor>[A-Z][A-Za-z.' ]{2,80}?),?\s+(?:\(\s*\"?Grantors?\"?\s*\)\s*,?\s*)?(?:does|do)?\s*(?:hereby\s+)?"
            r"(?:grants?|conveys?|quitclaims?|remises?)(?:,?\s+(?:and\s+)?(?:bargains?|sells?|releases?|warrants?|conveys?|quitclaims?|forever))*\s+"
            r"(?:unto|to)\s+(?P<grantee>[A-Z][A-Za-z.' &]{2,120}?)(?:,|\s+(?:the\s+following|all\s+(?:that|of)|whose|as\s+|his\s|her\s|their\s))",
        ),
        {"grantor": "grantor", "grantee": "grantee"},
        0.65,
    ),
    (re.compile(r"\bEstate\s+of\s+(?P<decedent>[A-Z][A-Za-z.' ]{2,80}?)(?:,|\s+deceased|\n|$)"), {"decedent": "decedent"}, 0.8),
    (re.compile(rf"\bdied\s+(?:on\s+)?(?P<date_of_death>{DATE_PATTERN})", re.IGNORECASE), {"date_of_death": "date_of_death"}, 0.75),
    (re.compile(r"\bBook\s+(?P<book>\d{1,6}),?\s+Page\s+(?P<page>\d{1,6})", re.IGNORECASE), {}, 0.85),
]

_CLASSIFIERS: list[tuple[str, str, float]] = [
    ("death_certificate", r"certificate\s+of\s+death|death\s+certificate", 4),
    ("will", r"last\s+will\s+and\s+testament|\blast\s+will\b", 4),
    ("probate", r"letters\s+(?:testamentary|of\s+administration)|order\s+(?:of\s+distribution|determining\s+heirship)|affidavit\s+of\s+heirship|probate\s+court|determination\s+of\s+heirs", 4),
    ("mortgage", r"deed\s+of\s+trust|\bmortgage\b", 3),
    ("deed", r"(?:special\s+)?warranty\s+deed|quit\s*claim\s+deed|grant\s+deed|deed\s+of\s+gift|\bdeed\b", 3),
    ("tax_record", r"property\s+tax|tax\s+(?:bill|statement|notice|receipt)|ad\s+valorem|delinquent\s+tax|tax\s+year", 3),
    ("lien", r"notice\s+of\s+lien|claim\s+of\s+lien|(?:mechanic'?s|judgment|tax|water|utility)\s+lien|\blien\b", 3),
    ("financial_statement", r"bank\s+statement|statement\s+of\s+account|payment\s+receipt|transaction\s+history", 2),
]


def classify_document(text: str) -> tuple[str, float]:
    """The document type, by weighted keyword evidence; the header counts double."""
    head = text[:500]
    scores: dict[str, float] = {}
    for doc_type, pattern, weight in _CLASSIFIERS:
        regex = re.compile(pattern, re.IGNORECASE)
        hits = len(regex.findall(text))
        if not hits:
            continue
        score = weight * min(hits, 3) + (weight * 2 if regex.search(head) else 0)
        scores[doc_type] = scores.get(doc_type, 0) + score
    if "deed" in scores and "mortgage" in scores and re.search(r"deed\s+of\s+trust", head, re.IGNORECASE):
        scores.pop("deed")
    if not scores:
        return "other", 0.0
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    return best, round(scores[best] / total, 2)


def _normalise(fact_type: str, value: str) -> str | None:
    if fact_type in NAME_FACTS:
        return normalize_name(value) or None
    if fact_type in DATE_FACTS:
        return parse_date_text(value)
    if fact_type in MONEY_FACTS:
        cents = parse_money_text(value)
        return None if cents is None else str(cents)
    if fact_type == "parcel_number":
        normalized = normalize_parcel(value)
        return normalized if re.search(r"\d", normalized) and len(normalized) >= 5 else None
    if fact_type in REFERENCE_FACTS:
        normalized = normalize_parcel(value)
        return normalized if len(normalized) >= 3 else None
    if fact_type == "property_address":
        return normalize_address(value) if re.search(r"\d", value) else None
    if fact_type == "tax_year":
        match = re.search(r"\b(19|20)\d{2}\b", value)
        return match.group(0) if match else None
    if fact_type == "tax_status":
        word = value.strip().upper().split()[0] if value.strip() else ""
        return word if word in {"DELINQUENT", "PAID", "UNPAID", "CURRENT", "PARTIAL", "DUE", "OVERDUE"} else None
    return " ".join(value.split())[:500] or None


def _candidates(fact_type: str, raw_value: str, page: int, snippet: str, confidence: float) -> list[FactCandidate]:
    value = raw_value.strip().strip(" ,;")
    if fact_type in NAME_FACTS:
        out = []
        for name in split_names(value):
            normalized = _normalise(fact_type, name)
            if normalized:
                out.append(FactCandidate(fact_type, name, normalized, page, snippet, confidence))
        return out
    if fact_type in DATE_FACTS:
        match = re.search(DATE_PATTERN, value, re.IGNORECASE)
        value = match.group(0) if match else value
    if fact_type in MONEY_FACTS:
        match = re.search(r"\$?\s*[0-9][0-9,]*(?:\.[0-9]{2})?", value)
        value = match.group(0).strip() if match else value
    if fact_type in REFERENCE_FACTS or fact_type == "parcel_number":
        value = value.split()[0] if value.split() else value
    normalized = _normalise(fact_type, value)
    if not normalized:
        return []
    return [FactCandidate(fact_type, value, normalized, page, snippet, confidence)]


def extract_facts(pages: list[tuple[int, str]], doc_type: str) -> list[FactCandidate]:
    found: list[FactCandidate] = []
    seen: set[tuple[str, str]] = set()

    def keep(candidates: list[FactCandidate]) -> None:
        for candidate in candidates:
            key = (candidate.fact_type, candidate.normalized)
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)

    for page_number, text in pages:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for regex, fact_type, confidence in _LABEL_RES:
                match = regex.match(stripped)
                if not match:
                    continue
                if fact_type == "owner_of_record" and doc_type in {"deed", "will", "probate", "death_certificate"}:
                    confidence = 0.6  # "Owner:" on a deed is usually the preparer's form field
                keep(_candidates(fact_type, match.group("value"), page_number, stripped[:300], confidence))
                break

        flat = " ".join(text.split())
        # Prose patterns never cross a paragraph break: a heading or address line above a
        # sentence is not part of the grantor's name.
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
        for regex, groups, confidence in _PROSE:
            for match in (m for para in paragraphs for m in regex.finditer(para)):
                flat_para = match.string
                snippet = flat_para[max(0, match.start() - 40) : match.end() + 40].strip()
                if not groups:  # book/page
                    keep(
                        [
                            FactCandidate(
                                "book_page",
                                f"Book {match.group('book')}, Page {match.group('page')}",
                                f"{int(match.group('book'))}/{int(match.group('page'))}",
                                page_number,
                                snippet,
                                confidence,
                            )
                        ]
                    )
                    continue
                for group, fact_type in groups.items():
                    keep(_candidates(fact_type, match.group(group), page_number, snippet[:300], confidence))

        if doc_type == "deed":
            head = flat[:400].upper()
            kind = "QUITCLAIM" if re.search(r"QUIT\s*CLAIM", head) else "WARRANTY" if "WARRANTY" in head else "DEED"
            keep([FactCandidate("instrument_type", kind.title(), kind, page_number, flat[:120], 0.8)])
    return found
