"""Case-scoped retrieval: BM25 over page chunks of one case's own documents.

Replaces InHeir's Azure AI Search index. The decision (``docs/architecture/decisions.md``):
a case's corpus is tens of pages, permissions are per case, and every hit must cite a
document and page — so retrieval is computed from the database on demand, scoped by the
same tenant check as every other read. No separate index can drift out of permission sync.
Vertex AI Search remains the documented path for an org-wide legal knowledge base.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import DocumentPage, EvidenceDocument

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "was", "were", "be", "by", "with",
    "as", "at", "this", "that", "it", "from", "who", "what", "which", "when", "does", "do", "did", "has", "have",
}


@dataclass
class Hit:
    document_id: str
    filename: str
    page: int
    text: str
    score: float


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def chunks(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = " ".join(text.split())
    if len(text) <= size:
        return [text] if text else []
    out = []
    start = 0
    while start < len(text):
        out.append(text[start : start + size])
        start += size - overlap
    return out


def search_case(db: Session, case_id: str, query: str, k: int = 5) -> list[Hit]:
    rows = db.execute(
        select(DocumentPage, EvidenceDocument)
        .join(EvidenceDocument, EvidenceDocument.id == DocumentPage.document_id)
        .where(EvidenceDocument.case_id == case_id)
    ).all()
    corpus: list[tuple[DocumentPage, EvidenceDocument, str, list[str]]] = []
    for page, document in rows:
        for chunk in chunks(page.text):
            corpus.append((page, document, chunk, tokens(chunk)))
    query_terms = tokens(query)
    if not corpus or not query_terms:
        return []
    n = len(corpus)
    avg = sum(len(t) for *_, t in corpus) / n
    df = Counter(term for *_, toks in corpus for term in set(toks))
    k1, b = 1.5, 0.75
    hits: list[Hit] = []
    for page, document, chunk, toks in corpus:
        tf = Counter(toks)
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avg))
        if score > 0:
            hits.append(Hit(document.id, document.filename, page.page_number, chunk, round(score, 4)))
    hits.sort(key=lambda h: -h.score)
    return hits[:k]
