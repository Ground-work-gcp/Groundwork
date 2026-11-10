"""Evidence: documents (upload, storage, extraction), extracted facts and their review, search."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.infrastructure import storage
from app.infrastructure.database.models import EvidenceDocument, ExtractedFact, User, utcnow
from app.infrastructure.database.session import get_db
from app.repositories import get_case, get_case_owned, get_pages
from app.services.extraction import ALLOWED_TYPES, process_document, sniff_content_type, valid_doc_type
from app.services.extraction.rules import FACT_TYPES, NAME_FACTS, _normalise
from app.services.search import search_case

from . import serializers as s
from .deps import current_user, require

router = APIRouter(tags=["evidence"])


class FactIn(BaseModel):
    fact_type: str
    value: str = Field(min_length=1, max_length=2000)
    document_id: str | None = None
    page_number: int | None = None
    snippet: str = ""


class FactReview(BaseModel):
    status: str  # confirmed | corrected | rejected | unreviewed
    corrected_value: str | None = None
    review_note: str = ""


@router.post("/cases/{case_id}/documents", status_code=201)
async def upload(
    case_id: str,
    file: UploadFile = File(...),
    doc_type: str | None = Form(None),
    user: User = Depends(require("evidence:write")),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    case = get_case(db, user, case_id)
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, detail=f"Files are limited to {get_settings().max_upload_mb} MB")
    if not content:
        raise HTTPException(422, detail="The file is empty")
    filename = (file.filename or "document").replace("\\", "/").split("/")[-1][:300]
    content_type = sniff_content_type(filename, file.content_type)
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, detail="Supported: PDF, plain text, CSV, PNG, JPEG, TIFF")
    digest = hashlib.sha256(content).hexdigest()
    duplicate = db.scalar(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id, EvidenceDocument.sha256 == digest))
    if duplicate is not None:
        raise HTTPException(409, detail=f"This exact file is already in the case as {duplicate.filename}")
    declared = valid_doc_type(doc_type)
    uri = storage.put(
        storage.object_key(case.id, digest, filename), content, content_type,
        metadata={"organization_id": case.organization_id, "case_id": case.id, "uploaded_by": user.id},
    )
    document = EvidenceDocument(
        case_id=case.id, filename=filename, content_type=content_type, size_bytes=len(content), sha256=digest,
        storage_uri=uri, doc_type=declared or "other", doc_type_source="declared" if declared else "classified", uploaded_by_id=user.id,
    )
    db.add(document)
    db.flush()
    audit.record(
        db, organization_id=case.organization_id, event="document.uploaded", actor=user, case_id=case.id,
        entity_type="document", entity_id=document.id, detail={"filename": filename, "sha256": digest, "size": len(content), "storage": uri.split("://")[0]},
    )
    outcome = process_document(db, document, content)
    audit.record(
        db, organization_id=case.organization_id, event="document.extracted", actor=None, actor_kind="system", case_id=case.id,
        entity_type="document", entity_id=document.id,
        detail={"status": outcome["status"], "pages": outcome["pages"], "facts": outcome["facts"], "engine": document.extraction_engine, "doc_type": document.doc_type, "ai": outcome["ai"].get("status")},
    )
    return {"document": s.document(document, outcome["facts"])}


@router.get("/cases/{case_id}/documents")
def documents(case_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    counts = dict(db.execute(select(ExtractedFact.document_id, func.count()).where(ExtractedFact.case_id == case.id).group_by(ExtractedFact.document_id)).all())
    rows = db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == case.id).order_by(EvidenceDocument.created_at))
    return {"documents": [s.document(d, counts.get(d.id, 0)) for d in rows]}


@router.get("/documents/{document_id}")
def document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    doc, _case = get_case_owned(db, user, EvidenceDocument, document_id)
    facts = db.scalars(select(ExtractedFact).where(ExtractedFact.document_id == doc.id).order_by(ExtractedFact.page_number, ExtractedFact.fact_type))
    return {"document": s.document(doc), "facts": [s.fact(f) for f in facts], "pages": [s.page(p) for p in get_pages(db, doc)]}


@router.get("/documents/{document_id}/content")
def content(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    doc, case = get_case_owned(db, user, EvidenceDocument, document_id)
    try:
        data = storage.get(doc.storage_uri, doc.sha256)
    except storage.IntegrityError:
        raise HTTPException(500, detail="Stored object does not match its recorded hash") from None
    audit.record(db, organization_id=case.organization_id, event="document.downloaded", actor=user, case_id=case.id, entity_type="document", entity_id=doc.id)
    safe = doc.filename.replace('"', "")
    return Response(data, media_type=doc.content_type, headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-Content-Type-Options": "nosniff"})


@router.post("/documents/{document_id}/reprocess")
def reprocess(document_id: str, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    doc, case = get_case_owned(db, user, EvidenceDocument, document_id)
    outcome = process_document(db, doc, storage.get(doc.storage_uri, doc.sha256))
    audit.record(db, organization_id=case.organization_id, event="document.reprocessed", actor=user, case_id=case.id, entity_type="document", entity_id=doc.id, detail={"status": outcome["status"], "facts": outcome["facts"]})
    return {"document": s.document(doc, outcome["facts"])}


@router.get("/cases/{case_id}/facts")
def facts(
    case_id: str,
    document_id: str | None = None,
    fact_type: str | None = None,
    status: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    case = get_case(db, user, case_id)
    query = select(ExtractedFact).where(ExtractedFact.case_id == case.id)
    if document_id:
        query = query.where(ExtractedFact.document_id == document_id)
    if fact_type:
        query = query.where(ExtractedFact.fact_type == fact_type)
    if status:
        query = query.where(ExtractedFact.status == status)
    rows = db.scalars(query.order_by(ExtractedFact.fact_type, ExtractedFact.created_at))
    return {"facts": [s.fact(f) for f in rows], "fact_types": FACT_TYPES}


@router.post("/cases/{case_id}/facts", status_code=201)
def manual_fact(case_id: str, body: FactIn, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    if body.fact_type not in FACT_TYPES:
        raise HTTPException(422, detail="unknown fact type")
    if body.document_id:
        doc, doc_case = get_case_owned(db, user, EvidenceDocument, body.document_id)
        if doc_case.id != case.id:
            raise HTTPException(422, detail="document belongs to another case")
    normalized = _normalise(body.fact_type, body.value)
    if not normalized:
        raise HTTPException(422, detail=f"'{body.value}' is not a valid {body.fact_type.replace('_', ' ')}")
    fact = ExtractedFact(
        case_id=case.id, document_id=body.document_id, page_number=body.page_number, fact_type=body.fact_type, value=body.value.strip(),
        normalized_value=normalized, snippet=body.snippet, confidence=1.0, extractor="manual", status="confirmed",
        reviewed_by_id=user.id, reviewed_at=utcnow(),
    )
    db.add(fact)
    db.flush()
    audit.record(db, organization_id=case.organization_id, event="fact.added", actor=user, case_id=case.id, entity_type="fact", entity_id=fact.id, detail={"fact_type": fact.fact_type, "value": fact.value})
    return {"fact": s.fact(fact)}


@router.patch("/facts/{fact_id}")
def review_fact(fact_id: str, body: FactReview, user: User = Depends(require("evidence:write")), db: Session = Depends(get_db, scope="function")) -> dict:
    fact, case = get_case_owned(db, user, ExtractedFact, fact_id)
    if body.status not in ("confirmed", "corrected", "rejected", "unreviewed"):
        raise HTTPException(422, detail="status must be confirmed, corrected, rejected or unreviewed")
    before = {"status": fact.status, "value": fact.effective_value}
    if body.status == "corrected":
        if not body.corrected_value:
            raise HTTPException(422, detail="a corrected value is required")
        normalized = _normalise(fact.fact_type, body.corrected_value)
        if not normalized:
            raise HTTPException(422, detail=f"'{body.corrected_value}' is not a valid {fact.fact_type.replace('_', ' ')}")
        # Names keep the reviewer's spelling (they are normalised when compared); dates, amounts
        # and identifiers store their normalised form. The original extraction is preserved.
        fact.corrected_value = body.corrected_value.strip() if fact.fact_type in NAME_FACTS else normalized
    fact.status = body.status
    fact.review_note = body.review_note
    fact.reviewed_by_id = user.id if body.status != "unreviewed" else None
    fact.reviewed_at = utcnow() if body.status != "unreviewed" else None
    audit.record(
        db, organization_id=case.organization_id, event="fact.reviewed", actor=user, case_id=case.id, entity_type="fact", entity_id=fact.id,
        detail={"before": before, "after": {"status": fact.status, "value": fact.effective_value}, "note": body.review_note},
    )
    return {"fact": s.fact(fact)}


@router.get("/cases/{case_id}/search")
def search(case_id: str, q: str = Query(min_length=2, max_length=300), user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    case = get_case(db, user, case_id)
    return {"hits": [hit.__dict__ for hit in search_case(db, case.id, q, k=10)]}
