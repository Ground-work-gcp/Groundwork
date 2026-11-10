"""Document AI OCR/layout extraction (replaces InHeir's Azure Document Intelligence).

Online (synchronous) processing only: the request is bounded by size and page count before it
is sent, carries a timeout, and is retried only on transient errors. Each page comes back with
its own page number and layout confidence so every extracted fact keeps page-level provenance.
A processing failure raises ``ProviderError``; the caller records the document as ``failed``
with the error type — it is never reported as extracted.
"""

from __future__ import annotations

import io
from functools import lru_cache

from app.core.config import get_settings
from app.core.resilience import call_provider

PROVIDER = "document_ai"
SUPPORTED = {"application/pdf", "image/png", "image/jpeg", "image/tiff", "image/gif", "image/bmp", "image/webp"}


class DocumentRejected(ValueError):
    """The document cannot be sent to the online processor (type, size, page count)."""


@lru_cache(maxsize=1)
def _client():
    from google.api_core.client_options import ClientOptions
    from google.cloud import documentai

    location = get_settings().documentai_location
    return documentai.DocumentProcessorServiceClient(client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com"))


def processor_label() -> str:
    return f"documentai:{get_settings().documentai_processor_id}"


def processor_name() -> str:
    settings = get_settings()
    return f"projects/{settings.gcp_project}/locations/{settings.documentai_location}/processors/{settings.documentai_processor_id}"


def validate(content: bytes, mime_type: str) -> None:
    settings = get_settings()
    if mime_type not in SUPPORTED:
        raise DocumentRejected(f"Document AI does not accept {mime_type}")
    if len(content) > settings.documentai_max_mb * 1024 * 1024:
        raise DocumentRejected(f"documents over {settings.documentai_max_mb} MB need batch processing")
    if mime_type == "application/pdf":
        try:
            from pypdf import PdfReader

            pages = len(PdfReader(io.BytesIO(content)).pages)
        except Exception:
            pages = 0  # let Document AI judge an unparseable PDF
        if pages > settings.documentai_max_pages:
            raise DocumentRejected(f"{pages} pages exceeds the online limit of {settings.documentai_max_pages}")


def process(content: bytes, mime_type: str) -> list[tuple[int, str, float | None]]:
    """(page number, text, layout confidence) for every page Document AI returns."""
    from google.cloud import documentai

    settings = get_settings()
    validate(content, mime_type)
    request = documentai.ProcessRequest(
        name=processor_name(), raw_document=documentai.RawDocument(content=content, mime_type=mime_type), skip_human_review=True
    )
    result = call_provider(
        PROVIDER, "process", lambda: _client().process_document(request=request, timeout=settings.provider_timeout_seconds),
        attempts=settings.provider_attempts, budget_seconds=settings.provider_timeout_seconds * 2,
    )
    document = result.document
    pages: list[tuple[int, str, float | None]] = []
    for index, page in enumerate(document.pages, start=1):
        segments = page.layout.text_anchor.text_segments
        text = "".join(document.text[int(seg.start_index or 0) : int(seg.end_index)] for seg in segments)
        confidence = float(page.layout.confidence) if page.layout.confidence else None
        pages.append((int(page.page_number or index), text, confidence))
    return pages


def describe_processor() -> dict:
    """The configured processor's type and state, read from Document AI (live tests, ops)."""
    settings = get_settings()
    proc = call_provider(
        PROVIDER, "get_processor", lambda: _client().get_processor(name=processor_name(), timeout=settings.provider_timeout_seconds), attempts=2
    )
    return {"name": proc.name, "type": proc.type_, "state": proc.state.name, "display_name": proc.display_name}
