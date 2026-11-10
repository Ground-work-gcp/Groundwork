"""Evidence-backed answers to questions about one case (InHeir's chatbot, made traceable).

Retrieval is case-scoped (``services.search``). With Gemini configured the model answers
from numbered, PII-protected sources and must cite them as ``[S1]``; citations to sources it
was not given are dropped, and an answer with no valid citation is stored as *unverified*.
Without Gemini the endpoint returns the most relevant passages and says so — it never
fabricates an answer.

Every citation carries an ``evidence_status`` computed from the case's own records, so the
reader (and the model) can tell apart:
  verified        — the facts on that page were confirmed or corrected by a reviewer
  awaiting_review — facts on that page are still unreviewed extractions
  conflicting     — an open finding cites that document
  unextracted     — the page matched the search but produced no facts
Missing information is stated when retrieval finds nothing; anything else in a model answer
is labelled as interpretation.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import ExtractedFact, RiskFinding
from app.services import ai, pii
from app.services.search import search_case

SYSTEM = (
    "You help legal-aid staff review inherited-property evidence. Answer ONLY from the numbered sources. "
    "Cite every factual claim with its source number like [S1]. Each source is labelled with its evidence status. "
    "Structure the answer under these headings, omitting empty ones: "
    "'Verified evidence' (claims from sources marked verified), 'Awaiting review' (claims from unreviewed extractions), "
    "'Conflicts' (where sources disagree or are marked conflicting), 'Missing' (what the sources do not establish), "
    "'Interpretation' (your own reasoning, clearly marked as such and never presented as fact). "
    "Do not give legal advice or ownership determinations; flag what needs an attorney's review."
)


def evidence_status(db: Session, case_id: str, refs: list[tuple[str, int]]) -> dict[tuple[str, int], str]:
    doc_ids = {d for d, _ in refs}
    if not doc_ids:
        return {}
    facts = db.execute(
        select(ExtractedFact.document_id, ExtractedFact.page_number, ExtractedFact.status).where(
            ExtractedFact.case_id == case_id, ExtractedFact.document_id.in_(doc_ids)
        )
    ).all()
    conflicted: set[str] = set()
    for finding in db.scalars(select(RiskFinding).where(RiskFinding.case_id == case_id, RiskFinding.status == "open")):
        for ref in finding.evidence_refs or []:
            if isinstance(ref, dict) and ref.get("document_id") in doc_ids:
                conflicted.add(ref["document_id"])
    out: dict[tuple[str, int], str] = {}
    for doc_id, page in refs:
        statuses = {s for d, p, s in facts if d == doc_id and p == page and s != "rejected"}
        if doc_id in conflicted:
            out[(doc_id, page)] = "conflicting"
        elif statuses and statuses <= {"confirmed", "corrected"}:
            out[(doc_id, page)] = "verified"
        elif statuses:
            out[(doc_id, page)] = "awaiting_review"
        else:
            out[(doc_id, page)] = "unextracted"
    return out


def answer(db: Session, case_id: str, question: str) -> dict[str, Any]:
    hits = search_case(db, case_id, question, k=5)
    status = evidence_status(db, case_id, [(h.document_id, h.page) for h in hits])
    sources = [
        {
            "ref": f"S{i}", "document_id": h.document_id, "filename": h.filename, "page": h.page,
            "text": pii.redact(h.text), "score": h.score, "evidence_status": status.get((h.document_id, h.page), "unextracted"),
        }
        for i, h in enumerate(hits, start=1)
    ]
    if not sources:
        return {
            "answer": "Missing: no extracted evidence in this case mentions that. Upload or extract the relevant documents first.",
            "citations": [],
            "mode": "extractive",
            "verified": False,
        }
    if not ai.enabled():
        return {
            "answer": "AI answering is not configured (AI_BACKEND=none). These are the most relevant passages from the case evidence, each with its review status.",
            "citations": sources,
            "mode": "extractive",
            "verified": True,
        }
    context = "\n\n".join(
        f"[{s['ref']}] {s['filename']}, page {s['page']} (status: {s['evidence_status']}):\n{pii.protect_for_model(s['text'])}" for s in sources
    )
    try:
        text = ai.generate_text(f"Sources:\n{context}\n\nQuestion: {pii.protect_for_model(question)}", system=SYSTEM)
    except Exception as exc:
        return {
            "answer": f"The model call failed ({type(exc).__name__}). These are the most relevant passages.",
            "citations": sources,
            "mode": "extractive",
            "verified": True,
        }
    refs = {s["ref"] for s in sources}
    cited = {f"S{n}" for n in re.findall(r"\[S(\d+)\]", text)}
    valid = [s for s in sources if s["ref"] in cited]
    text = re.sub(r"\[S(\d+)\]", lambda m: m.group(0) if f"S{m.group(1)}" in refs else "", text)
    return {"answer": pii.redact(text), "citations": valid, "mode": f"gemini:{ai.model_name()}", "verified": bool(valid)}
