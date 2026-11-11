"""Area, survey-number and land-class normalisation across regional conventions.

Extents arrive as ``2 acres 35 cents``, ``0.81.50`` (Tamil Nadu hectare-are-centiare),
``2-15`` (Karnataka acres-guntas), ``1 H 20 R`` (Maharashtra hectare-are) or ``3 bigha 5 biswa``.
Everything is converted to square metres for comparison; the original text is always kept.
"""

from __future__ import annotations

import re

from .regions import SQM, Region

_UNIT_WORDS: list[tuple[str, str]] = [
    (r"hectares?|hect\.?|ha\.?|h\b", "hectare"),
    (r"ares?|r\b", "are"),
    (r"centiares?|ca\b", "sqm"),
    (r"acres?|ac\.?|a\b", "acre"),
    (r"cents?|ct\.?|c\b", "cent"),
    (r"guntas?|guntha?s?|g\b", "gunta"),
    (r"sq\.?\s*(?:ft|feet)|square\s+feet|sft", "sqft"),
    (r"sq\.?\s*(?:yd|yards?)|square\s+yards?", "sqyd"),
    (r"sq\.?\s*m(?:etres?|eters?)?|square\s+m(?:etres?|eters?)|m2|sqm", "sqm"),
    (r"grounds?", "ground"),
    (r"bighas?", "bigha"),
    (r"biswas?", "biswa"),
    (r"kanals?", "kanal"),
    (r"marlas?", "marla"),
]
_NUM = r"\d+(?:\.\d+)?"
_PAIR = re.compile(rf"({_NUM})\s*(" + "|".join(p for p, _ in _UNIT_WORDS) + r")", re.I)


def _unit(word: str) -> str | None:
    for pattern, unit in _UNIT_WORDS:
        if re.fullmatch(pattern, word.strip(), re.I):
            return unit
    return None


def parse_extent(text: str, region: Region | None = None) -> float | None:
    """Square metres for an extent string, or None when it cannot be read with confidence."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    bigha = (region.bigha_sqm if region and region.bigha_sqm else 2529.29)
    units = dict(SQM, bigha=bigha, biswa=bigha / 20)

    # Tamil Nadu style "0.81.50" (hectare . are . centiare)
    m = re.fullmatch(r"(\d+)\.(\d{1,2})\.(\d{1,2})(?:\s*[a-z][a-z.\s-]*)?", t)
    if m:
        return int(m.group(1)) * 10_000 + int(m.group(2)) * 100 + int(m.group(3))
    # Karnataka / Maharashtra style "2-15" (acres-guntas) or "1-20 H.R" (hectare-are)
    m = re.fullmatch(r"(\d+)\s*-\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*(h\.?\s*r\.?|ha\.?\s*-?\s*ares?)?", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if m.group(4) or (region and region.units and region.units[0] == "hectare"):
            return a * 10_000 + b * 100 + (int(m.group(3)) if m.group(3) else 0)
        if region and "gunta" in region.units:
            return a * SQM["acre"] + b * SQM["gunta"]
        return None
    pairs = _PAIR.findall(t)
    if pairs:
        total = 0.0
        for number, word in pairs:
            unit = _unit(word)
            if unit is None:
                return None
            total += float(number) * units[unit]
        return round(total, 2)
    m = re.fullmatch(rf"({_NUM})", t)
    if m and region:
        # A bare number is read in the region's first unit, only when the region is known.
        return round(float(m.group(1)) * units[region.units[0]], 2)
    return None


def format_extent(sqm: float | None, region: Region | None = None) -> str:
    if sqm is None:
        return "—"
    first = region.units[0] if region else "acre"
    if first in ("cent", "acre") or (region and "cent" in region.units):
        acres = sqm / SQM["acre"]
        return f"{acres:.2f} acres ({sqm / SQM['cent']:.1f} cents)"
    if first == "hectare":
        return f"{sqm / 10_000:.4f} ha ({sqm / SQM['acre']:.2f} acres)"
    if first == "bigha" and region and region.bigha_sqm:
        return f"{sqm / region.bigha_sqm:.2f} bigha ({sqm / SQM['acre']:.2f} acres)"
    if "gunta" in (region.units if region else ()):
        whole = int(sqm // SQM["acre"])
        return f"{whole} acres {round((sqm - whole * SQM['acre']) / SQM['gunta'])} guntas"
    return f"{sqm / SQM['acre']:.2f} acres"


_SURVEY_PREFIX = re.compile(
    r"^\s*(?:(?:r\.?\s*s\.?|s\.?\s*f\.?|sy\.?|s\.?|re-?survey|survey|khasra|gat|hissa|khata)\s*(?:no\.?|number|#)?\s*[:.\-]?\s*)+",
    re.I,
)


def survey_key(text: str) -> str:
    """``S.F. No. 123 / 4-A`` → ``123/4A``. Leading zeros are dropped from numeric parts."""
    if not text:
        return ""
    t = _SURVEY_PREFIX.sub("", text.strip())
    t = re.sub(r"\s+", "", t.upper()).replace("-", "").replace("\\", "/")
    t = re.sub(r"[^0-9A-Z/]", "", t)
    parts = [re.sub(r"^0+(?=\d)", "", p) for p in t.split("/") if p]
    return "/".join(parts)


def survey_parent(key: str) -> str:
    """The main survey number without subdivisions: ``123/4A`` → ``123``."""
    return key.split("/")[0] if key else ""


SURVEY_IN_TEXT = re.compile(r"(?:(?:r\.?\s*s\.?|s\.?\s*f\.?|sy\.?|survey)\s*(?:no\.?|number)?\s*[:.\-]?\s*)(\d+(?:\s*/\s*[0-9A-Za-z\-]+)*)", re.I)


def surveys_in(text: str) -> set[str]:
    return {survey_key(m.group(1)) for m in SURVEY_IN_TEXT.finditer(text or "")}


_CLASS_WORDS = {
    "wet": ("wet", "irrigated", "nanja", "nanjai", "tari", "magani", "paddy land"),
    "dry": ("dry", "rainfed", "punja", "punjai", "manavari", "khushki", "metta", "jirayat"),
    "garden": ("garden", "thottam", "bagayat", "plantation", "orchard"),
    "residential": ("residential", "natham", "abadi", "house site", "gramatana"),
    "government": ("poramboke", "government", "gairan", "gomala", "kharab", "banjar", "gram samaj", "assessed waste"),
}


def land_class(text: str, region: Region | None = None) -> str:
    t = (text or "").lower()
    if not t:
        return ""
    if region:
        for term, cls in region.local_terms.items():
            if term in t:
                return cls
    for cls, words in _CLASS_WORDS.items():
        if any(w in t for w in words):
            return cls
    return ""
