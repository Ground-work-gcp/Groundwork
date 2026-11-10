"""The side-panel assistant and the user's context-graph memory.

The assistant never invents data. It routes each message to a grounded source:
  * inside a case → the evidence-grounded case Q&A (citations, evidence status), stored as a
    case question exactly like the Ask tab;
  * "what needs me / next"     → the live Needs-you queue (domain.flow)
  * "status / overview"        → the live organisation workflow graph
  * "find …"                   → case search by reference, title, address or parcel
  * "remember / history"       → the user's memory graph
  * anything else              → Gemini with a label-only memory summary and live counts
                                 (when configured; labelled as interpretation), otherwise help.
Each turn is written to the audit chain as ``assistant.query`` with the intent only — never the
message text — which in turn feeds the memory graph.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.domain.flow import needs_you, org_flow
from app.infrastructure.database.models import Case, CaseQuestion, Property, User
from app.infrastructure.database.session import get_db
from app.repositories import get_case
from app.services import ai, memory, pii, rag

from . import serializers as s
from .deps import current_user

router = APIRouter(tags=["assistant"])
_hits: dict[str, deque[float]] = defaultdict(deque)

INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("needs", re.compile(r"\b(needs? (me|you)|waiting|to ?do|queue|next|what should i|priorit)", re.I)),
    ("status", re.compile(r"\b(status|overview|summary|how many|pipeline|workflow|progress|outstanding)\b", re.I)),
    ("find", re.compile(r"\b(find|search|look ?up|where is|open case|parcel|address)\b", re.I)),
    ("memory", re.compile(r"\b(remember|history|previous|last time|recent(ly)?|what did i|worked on)\b", re.I)),
]


class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    case_id: str | None = None


def _limit(user: User) -> None:
    per_minute = get_settings().assistant_rate_per_minute
    now = time.monotonic()
    window = _hits[user.id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= per_minute:
        raise HTTPException(429, detail="Too many assistant requests; wait a minute")
    window.append(now)


def classify(message: str) -> str:
    for name, pattern in INTENTS:
        if pattern.search(message):
            return name
    return "general"


def _find(db: Session, user: User, message: str) -> list[dict[str, Any]]:
    terms = [t for t in re.findall(r"[\w-]{3,}", message) if t.lower() not in {"find", "search", "case", "look", "open", "where", "parcel", "address", "the", "for"}]
    if not terms:
        return []
    like = [f"%{t}%" for t in terms[:4]]
    conditions = [c for pattern in like for c in (Case.reference.ilike(pattern), Case.title.ilike(pattern), Property.address_line.ilike(pattern), Property.parcel_number.ilike(pattern))]
    rows = db.execute(
        select(Case, Property).outerjoin(Property, Property.case_id == Case.id).where(Case.organization_id == user.organization_id, or_(*conditions)).limit(8)
    ).all()
    return [
        {"title": f"{c.reference} · {c.title}", "sub": (p.address_line if p else "") + (f" · parcel {p.parcel_number}" if p and p.parcel_number else ""), "href": f"#case/{c.id}", "stage": c.stage}
        for c, p in rows
    ]


@router.get("/memory/graph")
def memory_graph(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    graph = memory.build(db, user)
    graph["suggestions"] = memory.suggestions(db, user, graph)
    return graph


@router.post("/assistant")
def assistant(body: AssistantIn, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    _limit(user)
    message = body.message.strip()
    mem = memory.build(db, user)
    out: dict[str, Any] = {"items": [], "citations": [], "mode": "grounded"}

    if body.case_id:
        case = get_case(db, user, body.case_id)
        intent = "case_question"
        reply = rag.answer(db, case.id, message)
        row = CaseQuestion(
            case_id=case.id, asked_by_id=user.id, question=message, answer=reply["answer"],
            citations=reply["citations"], mode=reply["mode"], verified=reply["verified"],
        )
        db.add(row)
        db.flush()
        out.update(reply=reply["answer"], citations=reply["citations"], mode=reply["mode"], verified=reply["verified"], question=s.question(row))
        audit.record(
            db, organization_id=case.organization_id, event="case.question_answered", actor=user, case_id=case.id,
            entity_type="case_question", entity_id=row.id, detail={"mode": row.mode, "citations": len(row.citations), "verified": row.verified, "via": "assistant"},
        )
    else:
        intent = classify(message)
        if intent == "needs":
            queue = needs_you(db, user, limit=8)
            out["items"] = [{"title": i["title"], "sub": f"{i['case_reference']} · {i['suggests']}", "href": i["href"], "severity": i["severity"]} for i in queue]
            out["reply"] = f"{len(queue)} item(s) need you, highest priority first." if queue else "Nothing is waiting on you right now."
        elif intent == "status":
            flow = org_flow(db, user)
            nodes = {n["id"]: n for n in flow["nodes"]}
            nxt = flow["next_action"]
            out["reply"] = (
                f"{flow['scope']['open_cases']} open case(s). {nodes['needs_you']['metric']} question(s) need a person, "
                f"{nodes['approvals']['metric']} approval(s) waiting, {nodes['findings']['metric']} open finding(s). "
                + (f"Bottleneck on the critical path: {nxt['label']} — {nxt['step']}" if nxt else "Nothing is blocking resolution.")
            )
            out["items"] = [{"title": f"{c['reference']} · {c['title']}", "sub": f"{c['remaining']} work item(s) · next: {(c['next_action'] or {}).get('label', '—')}", "href": f"#case/{c['id']}"} for c in flow["cases"][:6]]
        elif intent == "find":
            out["items"] = _find(db, user, message)
            out["reply"] = f"{len(out['items'])} matching case(s)." if out["items"] else "No case in your organisation matches that."
        elif intent == "memory":
            out["items"] = [{"title": n["label"], "sub": n["kind"], "href": n["href"]} for n in mem["nodes"][1:9]]
            out["reply"] = f"From your last {mem['events_considered']} action(s), ranked by how recently and often you worked on them." if out["items"] else "You have no recorded activity yet."
        elif ai.enabled():
            flow = org_flow(db, user)
            counts = ", ".join(f"{n['label']}: {n['metric']} {n.get('unit', '')}".strip() for n in flow["nodes"])
            prompt = (
                "Live workflow counts for the user's organisation:\n" + counts + "\n\nThe user's recent context (labels only):\n"
                + "\n".join(memory.context_lines(mem)) + f"\n\nUser: {pii.protect_for_model(message)}"
            )
            system = (
                "You are Groundwork's assistant for inherited-property resolution staff. Use only the counts and context given. "
                "You cannot see document contents here; for evidence questions tell the user to open the case and ask there. "
                "Never give legal advice or ownership determinations. Prefix any reasoning that is not a given count with 'Interpretation:'."
            )
            try:
                out["reply"] = pii.redact(ai.generate_text(prompt, system=system))
                out["mode"] = f"gemini:{ai.model_name()}"
            except Exception as exc:
                out["reply"] = f"The model call failed ({type(exc).__name__}). Try: “what needs me”, “status”, or “find <address>”."
        else:
            out["reply"] = "I answer from your live data: try “what needs me”, “status”, “find <address or parcel>”, or “what did I work on recently”. Open a case to ask about its evidence."
        audit.record(db, organization_id=user.organization_id, event="assistant.query", actor=user, entity_type="assistant", entity_id="", detail={"intent": intent, "chars": len(message)})

    out["intent"] = intent
    out["suggestions"] = memory.suggestions(db, user, mem)
    out["memory"] = {"nodes": mem["nodes"][:10], "topics": mem["topics"], "algorithm": mem["algorithm"]}
    return out
