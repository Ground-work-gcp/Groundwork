"""PII inspection and redaction (InHeir's Azure Text Analytics PII step, replaced).

``PII_BACKEND=local`` uses high-precision patterns for identifiers that must never leave
the case unredacted (SSNs, account numbers, phone numbers, email). ``PII_BACKEND=dlp``
inspects with Google Cloud Sensitive Data Protection. Either way only *counts by type and
page* are stored; the values themselves stay in the document.

Text sent to the model for question answering is always redacted with the local patterns
first, whichever backend produced the inspection summary.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.config import get_settings

PATTERNS: dict[str, re.Pattern[str]] = {
    "US_SOCIAL_SECURITY_NUMBER": re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    "EMAIL_ADDRESS": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PHONE_NUMBER": re.compile(r"(?<!\d)(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),
    "FINANCIAL_ACCOUNT_NUMBER": re.compile(r"(?i)\b(?:acct|account|routing)\s*(?:no\.?|number|#)?\s*[:#]?\s*\d{8,17}\b"),
    "DATE_OF_BIRTH": re.compile(r"(?i)\b(?:dob|date\s+of\s+birth)\s*[:#]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"),
}


def redact(text: str) -> str:
    for info_type, pattern in PATTERNS.items():
        text = pattern.sub(f"[REDACTED:{info_type}]", text)
    return text


def _local_inspect(pages: list[tuple[int, str]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_page: dict[str, dict[str, int]] = {}
    for page_number, text in pages:
        for info_type, pattern in PATTERNS.items():
            count = len(pattern.findall(text))
            if count:
                by_type[info_type] = by_type.get(info_type, 0) + count
                by_page.setdefault(str(page_number), {})[info_type] = count
    return {"engine": "local:patterns", "total": sum(by_type.values()), "by_type": by_type, "pages": by_page}


def protect_for_model(text: str) -> str:
    """Text that may leave the case (model prompts, generated output).

    Local high-precision redaction always runs; with ``PII_BACKEND=dlp`` Sensitive Data
    Protection de-identifies the result as well. If that call fails the locally redacted text
    is used — the fallback is the stricter local pass, never the raw text.
    """
    text = redact(text)
    if get_settings().pii_backend == "dlp":
        from app.infrastructure.google_cloud import dlp

        try:
            return dlp.deidentify(text)
        except Exception:
            return text
    return text


def inspect(pages: list[tuple[int, str]]) -> dict[str, Any]:
    if get_settings().pii_backend == "dlp":
        from app.infrastructure.google_cloud import dlp

        try:
            return dlp.inspect_pages(pages)
        except Exception as exc:  # recorded, never silently treated as "no PII"
            summary = _local_inspect(pages)
            summary["engine"] = "local:patterns (Sensitive Data Protection failed)"
            summary["error"] = str(exc)[:300]
            return summary
    return _local_inspect(pages)
