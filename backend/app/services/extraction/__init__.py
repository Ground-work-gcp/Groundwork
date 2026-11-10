"""Evidence ingestion: text extraction → classification → fact extraction → PII inspection.

Text comes from Document AI when configured (``EXTRACTION_BACKEND=documentai``) or from the
file's own text layer locally (plain text, PDF text via pypdf). A scanned image with no text
layer is marked ``needs_ocr`` rather than guessed at.

Facts come from ``rules:v1`` always and, when Gemini is configured, from the model **through
a grounding gate** (Magpie's §A7 applied to documents): a model-proposed fact is kept only if
its snippet appears verbatim on the cited page and its value appears in that snippet.
Everything rejected is logged on the document with the reason.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.models import DocumentPage, EvidenceDocument, ExtractedFact
from app.services import ai, pii

from .rules import DOC_TYPES, FACT_TYPES, FactCandidate, _normalise, classify_document, extract_facts

TEXT_TYPES = {"text/plain", "text/markdown", "text/csv"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/gif", "image/bmp", "image/webp"}
ALLOWED_TYPES = TEXT_TYPES | IMAGE_TYPES | {"application/pdf"}


@dataclass
class Extracted:
    pages: list[tuple[int, str, float | None]]
    engine: str
    status: str
    error: str = ""


def extract_text(content: bytes, content_type: str) -> Extracted:
    settings = get_settings()
    if settings.extraction_backend == "documentai" and content_type in ({"application/pdf"} | IMAGE_TYPES):
        from app.infrastructure.google_cloud import documentai

        try:
            pages = documentai.process(content, content_type)
        except Exception as exc:
            return Extracted([], documentai.processor_label(), "failed", f"Document AI failed: {exc}"[:500])
        status = "extracted" if any(text.strip() for _, text, _ in pages) else "needs_ocr"
        return Extracted(pages, documentai.processor_label(), status)

    if content_type in TEXT_TYPES:
        text = content.decode("utf-8", errors="replace").replace("\r\n", "\n")
        chunks = text.split("\f")
        return Extracted([(i, chunk, None) for i, chunk in enumerate(chunks, start=1)], "local:text", "extracted")

    if content_type == "application/pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(content))
            pages = [(i, page.extract_text() or "", None) for i, page in enumerate(reader.pages, start=1)]
        except Exception as exc:
            return Extracted([], "local:pypdf", "failed", f"PDF could not be read: {exc}"[:500])
        if not any(text.strip() for _, text, _ in pages):
            return Extracted(pages, "local:pypdf", "needs_ocr", "No text layer; OCR requires EXTRACTION_BACKEND=documentai")
        return Extracted(pages, "local:pypdf", "extracted")

    if content_type in IMAGE_TYPES:
        return Extracted([], "local", "needs_ocr", "Images need OCR; set EXTRACTION_BACKEND=documentai")
    return Extracted([], "local", "failed", f"Unsupported content type {content_type}")


# ── the AI tier, with its grounding gate ─────────────────────────────────────

_AI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "fact_type": {"type": "STRING", "enum": FACT_TYPES},
                    "value": {"type": "STRING"},
                    "page": {"type": "INTEGER"},
                    "snippet": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["fact_type", "value", "page", "snippet", "confidence"],
            },
        }
    },
    "required": ["facts"],
}


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def ground(proposals: list[dict[str, Any]], pages: dict[int, str], model: str) -> tuple[list[FactCandidate], list[dict]]:
    """Keep only model facts whose snippet is on the cited page and whose value is in the snippet."""
    accepted: list[FactCandidate] = []
    rejected: list[dict] = []
    for proposal in proposals:
        fact_type = str(proposal.get("fact_type", ""))
        value = str(proposal.get("value", "")).strip()
        snippet = str(proposal.get("snippet", "")).strip()
        page = proposal.get("page")
        reason = None
        if fact_type not in FACT_TYPES:
            reason = "unknown fact type"
        elif not isinstance(page, int) or page not in pages:
            reason = f"cited page {page!r} does not exist"
        elif not snippet or _squash(snippet) not in _squash(pages[page]):
            reason = "snippet is not on the cited page"
        elif not value or _squash(value) not in _squash(snippet):
            reason = "value is not in the snippet"
        normalized = None if reason else _normalise(fact_type, value)
        if reason is None and not normalized:
            reason = "value could not be normalised"
        if reason:
            rejected.append({"fact_type": fact_type, "value": value[:120], "page": page, "reason": reason})
            continue
        try:
            confidence = min(float(proposal.get("confidence", 0.5)), 0.85)
        except (TypeError, ValueError):
            confidence = 0.5
        accepted.append(FactCandidate(fact_type, value, str(normalized), page, snippet[:300], max(0.0, confidence), f"gemini:{model}"))
    return accepted, rejected


def ai_facts(pages: list[tuple[int, str]], doc_type: str) -> tuple[list[FactCandidate], dict[str, Any]]:
    if not ai.enabled():
        return [], {"status": "not_configured"}
    redacted = {number: pii.redact(text) for number, text in pages}
    corpus = "\n\n".join(f"=== PAGE {number} ===\n{text[:12000]}" for number, text in redacted.items())
    prompt = (
        f"Document type: {doc_type}.\nExtract property-record facts from the pages below. For every fact give the "
        "page number and copy the exact snippet (verbatim, one line) that contains the value. Do not infer facts "
        "that are not written in the document.\n\n" + corpus
    )
    try:
        reply = ai.generate_json(prompt, schema=_AI_SCHEMA, system="You extract facts from US property records. Never invent values.")
    except Exception as exc:
        return [], {"status": "error", "error": str(exc)[:300]}
    proposals = reply.get("facts", []) if isinstance(reply, dict) else []
    accepted, rejected = ground(proposals, redacted, ai.model_name())
    return accepted, {"status": "ok", "model": ai.model_name(), "proposed": len(proposals), "accepted": len(accepted), "rejected": rejected}


def process_document(db: Session, document: EvidenceDocument, content: bytes) -> dict[str, Any]:
    """(Re)extract a stored document. Replaces its pages and machine facts; keeps reviewed facts."""
    extracted = extract_text(content, document.content_type)
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    db.execute(
        delete(ExtractedFact).where(
            ExtractedFact.document_id == document.id, ExtractedFact.status == "unreviewed", ExtractedFact.extractor != "manual"
        )
    )
    for number, text, confidence in extracted.pages:
        db.add(DocumentPage(document_id=document.id, page_number=number, text=text, confidence=confidence))

    pages = [(number, text) for number, text, _ in extracted.pages]
    full_text = "\n".join(text for _, text in pages)
    if document.doc_type_source != "declared":
        document.doc_type = classify_document(full_text)[0] if full_text.strip() else "other"

    candidates = extract_facts(pages, document.doc_type) if extracted.status == "extracted" else []
    model_facts, log = ai_facts(pages, document.doc_type) if extracted.status == "extracted" else ([], {"status": "skipped"})
    known = {(c.fact_type, c.normalized) for c in candidates}
    candidates += [f for f in model_facts if (f.fact_type, f.normalized) not in known]

    kept = {
        (fact.fact_type, fact.normalized_value)
        for fact in db.query(ExtractedFact).filter(ExtractedFact.document_id == document.id).all()
    }
    for candidate in candidates:
        if (candidate.fact_type, candidate.normalized) in kept:
            continue
        db.add(
            ExtractedFact(
                case_id=document.case_id,
                document_id=document.id,
                page_number=candidate.page,
                fact_type=candidate.fact_type,
                value=candidate.value,
                normalized_value=candidate.normalized,
                snippet=candidate.snippet,
                confidence=candidate.confidence,
                extractor=candidate.extractor,
            )
        )

    document.status = extracted.status
    document.page_count = len(extracted.pages)
    document.extraction_engine = extracted.engine
    document.extraction_error = extracted.error
    document.pii_summary = pii.inspect(pages) if pages else {"engine": "none", "total": 0, "by_type": {}, "pages": {}}
    document.ai_extraction_log = log
    db.flush()
    return {"status": extracted.status, "pages": len(extracted.pages), "facts": len(candidates), "ai": log}


def sniff_content_type(filename: str, declared: str | None) -> str:
    lower = filename.lower()
    by_extension = {
        ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff",
    }
    for extension, content_type in by_extension.items():
        if lower.endswith(extension):
            return content_type
    declared = (declared or "").split(";")[0].strip().lower()
    return declared if declared in ALLOWED_TYPES else "application/octet-stream"


def valid_doc_type(value: str | None) -> str | None:
    if value and re.fullmatch(r"[a-z_]+", value) and value in DOC_TYPES:
        return value
    return None
