"""Sensitive Data Protection (Cloud DLP): inspection counts and text de-identification.

Inspection uses ``include_quote=False`` so finding values never leave Google; only counts by
info-type and page are kept. De-identification replaces each finding with ``[INFO_TYPE]`` and
is used on text that goes to the model or into generated output. Both are bounded by timeout
and retried only on transient errors.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.resilience import call_provider

PROVIDER = "sensitive_data_protection"
INFO_TYPES = [
    "US_SOCIAL_SECURITY_NUMBER",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "FINANCIAL_ACCOUNT_NUMBER",
    "DATE_OF_BIRTH",
    "US_DRIVERS_LICENSE_NUMBER",
    "PERSON_NAME",
    "STREET_ADDRESS",
]
# Names and street addresses are the substance of a property case; they are counted but not
# masked when text is prepared for the model.
DEIDENTIFY_TYPES = [t for t in INFO_TYPES if t not in {"PERSON_NAME", "STREET_ADDRESS"}]
MAX_ITEM_CHARS = 500_000


@lru_cache(maxsize=1)
def _client():
    from google.cloud import dlp_v2

    return dlp_v2.DlpServiceClient()


def _parent() -> str:
    return f"projects/{get_settings().gcp_project}/locations/global"


def inspect_pages(pages: list[tuple[int, str]]) -> dict[str, Any]:
    settings = get_settings()
    by_type: dict[str, int] = {}
    by_page: dict[str, dict[str, int]] = {}
    for page_number, text in pages:
        if not text.strip():
            continue
        request = {
            "parent": _parent(),
            "inspect_config": {"info_types": [{"name": n} for n in INFO_TYPES], "include_quote": False, "min_likelihood": "POSSIBLE"},
            "item": {"value": text[:MAX_ITEM_CHARS]},
        }
        response = call_provider(
            PROVIDER, "inspect", lambda request=request: _client().inspect_content(request=request, timeout=settings.provider_timeout_seconds),
            attempts=settings.provider_attempts,
        )
        for finding in response.result.findings:
            name = finding.info_type.name
            by_type[name] = by_type.get(name, 0) + 1
            page = by_page.setdefault(str(page_number), {})
            page[name] = page.get(name, 0) + 1
    return {"engine": "google:sensitive-data-protection", "total": sum(by_type.values()), "by_type": by_type, "pages": by_page}


def deidentify(text: str) -> str:
    """Replace sensitive values with ``[INFO_TYPE]`` using Sensitive Data Protection."""
    if not text.strip():
        return text
    settings = get_settings()
    request = {
        "parent": _parent(),
        "inspect_config": {"info_types": [{"name": n} for n in DEIDENTIFY_TYPES], "min_likelihood": "LIKELY"},
        "deidentify_config": {"info_type_transformations": {"transformations": [{"primitive_transformation": {"replace_with_info_type_config": {}}}]}},
        "item": {"value": text[:MAX_ITEM_CHARS]},
    }
    response = call_provider(
        PROVIDER, "deidentify", lambda: _client().deidentify_content(request=request, timeout=settings.provider_timeout_seconds),
        attempts=settings.provider_attempts,
    )
    return response.item.value
