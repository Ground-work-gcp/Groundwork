"""Per-user context-graph memory.

Nothing new is stored. The graph is rebuilt on demand from what Groundwork already records
about the user — their own entries in the hash-chained audit log and the case questions they
asked — so memory can never drift from the system of record, inherits tenant isolation (every
query is filtered to the user's organisation *and* the user as actor), and is erased by the
same retention as the audit log.

Graph:  user ──(time-decayed actions)──▶ case / document / finding / action / property
        user ──(asked about)───────────▶ topic ──▶ case
        document / finding / action ───▶ case          (structural edges)

Ranking: **personalized PageRank** (Haveliwala, *Topic-Sensitive PageRank*, 2002) with the
restart vector on the user node and edge weights decayed with a 14-day half-life, so recent
and repeated work ranks highest. Bounded for memory: the last ``MAX_EVENTS`` actions, the top
``TOP_K`` nodes returned, labels resolved only for those.

Suggestions come from the ranked cases' live workflow (next best action) and from prior-years
data (tax years present in earlier evidence that have no counterpart for the current year).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    CaseQuestion,
    EvidenceDocument,
    FinancialObligation,
    Property,
    RiskFinding,
    User,
    WorkflowAction,
)

MAX_EVENTS = 500
MAX_QUESTIONS = 100
TOP_K = 24
HALF_LIFE_DAYS = 14.0
DAMPING = 0.85
ITERATIONS = 40
STOP = set(
    ["the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "been", "what", "who", "which", "when", "where", "why", "how", "does", "did", "do", "this", "that", "with", "from", "about", "there", "their", "they", "it", "its", "as", "at", "by", "any", "all", "can", "could", "should", "would", "case", "property", "please", "tell", "me", "show"]
)


def decay(occurred: str | datetime | None, now: datetime) -> float:
    if occurred is None:
        return 0.5
    if isinstance(occurred, str):
        try:
            occurred = datetime.fromisoformat(occurred)
        except ValueError:
            return 0.5
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    age_days = max(0.0, (now - occurred).total_seconds() / 86400)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def topics(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
    return [w for w in words if w not in STOP][:8]


def personalized_pagerank(edges: dict[str, dict[str, float]], seed: str) -> dict[str, float]:
    """Power iteration on the (undirected, weighted) graph with restart to ``seed``."""
    nodes = set(edges)
    for targets in edges.values():
        nodes.update(targets)
    if seed not in nodes:
        return {}
    neighbours: dict[str, dict[str, float]] = defaultdict(dict)
    for a, targets in edges.items():
        for b, w in targets.items():
            neighbours[a][b] = neighbours[a].get(b, 0.0) + w
            neighbours[b][a] = neighbours[b].get(a, 0.0) + w
    out_weight = {n: sum(neighbours[n].values()) for n in nodes}
    rank = {n: (1.0 if n == seed else 0.0) for n in nodes}
    for _ in range(ITERATIONS):
        nxt = {n: (1 - DAMPING) * (1.0 if n == seed else 0.0) for n in nodes}
        for n in nodes:
            total = out_weight[n]
            if total <= 0:
                nxt[seed] += DAMPING * rank[n]
                continue
            share = DAMPING * rank[n] / total
            for m, w in neighbours[n].items():
                nxt[m] += share * w
        delta = sum(abs(nxt[n] - rank[n]) for n in nodes)
        rank = nxt
        if delta < 1e-9:
            break
    return rank


def build(db: Session, user: User) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    me = f"user:{user.id}"
    edges: dict[str, dict[str, float]] = defaultdict(dict)

    def link(a: str, b: str, w: float) -> None:
        edges[a][b] = edges[a].get(b, 0.0) + w

    events = list(db.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == user.organization_id, AuditEvent.actor_id == user.id)
        .order_by(AuditEvent.seq.desc())
        .limit(MAX_EVENTS)
    ))
    activity: Counter[str] = Counter()
    by_month: Counter[str] = Counter()
    for e in events:
        w = decay(e.occurred_at, now)
        activity[e.event.split(".")[0]] += 1
        by_month[e.occurred_at[:7]] += 1
        case_node = f"case:{e.case_id}" if e.case_id else None
        if e.entity_type and e.entity_id and e.entity_type not in ("case", "user", "organization", "case_question"):
            entity = f"{e.entity_type}:{e.entity_id}"
            link(me, entity, w)
            if case_node:
                link(entity, case_node, 0.5)
        if case_node:
            link(me, case_node, w)

    questions = list(db.scalars(
        select(CaseQuestion).join(Case, Case.id == CaseQuestion.case_id)
        .where(Case.organization_id == user.organization_id, CaseQuestion.asked_by_id == user.id)
        .order_by(CaseQuestion.created_at.desc())
        .limit(MAX_QUESTIONS)
    ))
    topic_count: Counter[str] = Counter()
    for q in questions:
        w = decay(q.created_at, now)
        for t in topics(q.question):
            topic_count[t] += 1
            link(me, f"topic:{t}", w)
            link(f"topic:{t}", f"case:{q.case_id}", 0.5 * w)

    rank = personalized_pagerank(edges, me)
    ranked = sorted(((n, r) for n, r in rank.items() if n != me and r > 0), key=lambda x: -x[1])[:TOP_K]
    labels = _labels(db, user, [n for n, _ in ranked])
    nodes = [{"id": me, "kind": "user", "label": user.full_name, "score": 1.0, "href": None}]
    kept = {me}
    for n, r in ranked:
        if n not in labels:  # entity deleted or not visible to this organisation
            continue
        nodes.append({"id": n, "kind": n.split(":", 1)[0], "label": labels[n]["label"], "score": round(r, 5), "href": labels[n].get("href")})
        kept.add(n)
    graph_edges = [
        {"from": a, "to": b, "weight": round(w, 4)}
        for a, targets in edges.items() for b, w in targets.items() if a in kept and b in kept
    ]
    return {
        "nodes": nodes,
        "edges": graph_edges,
        "activity": dict(activity.most_common()),
        "by_month": dict(sorted(by_month.items())),
        "topics": [t for t, _ in topic_count.most_common(8)],
        "events_considered": len(events),
        "questions_considered": len(questions),
        "algorithm": f"personalized PageRank (damping {DAMPING}, restart on you, {HALF_LIFE_DAYS:g}-day half-life decay)",
        "generated_at": now.isoformat(),
    }


def _labels(db: Session, user: User, node_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids: dict[str, list[str]] = defaultdict(list)
    for n in node_ids:
        kind, _, ident = n.partition(":")
        ids[kind].append(ident)
    org = user.organization_id
    out: dict[str, dict[str, Any]] = {}
    if ids.get("case"):
        for c in db.scalars(select(Case).where(Case.organization_id == org, Case.id.in_(ids["case"]))):
            out[f"case:{c.id}"] = {"label": f"{c.reference} · {c.title}", "href": f"#case/{c.id}"}
    if ids.get("document"):
        for d, cid in db.execute(
            select(EvidenceDocument, Case.id).join(Case, Case.id == EvidenceDocument.case_id)
            .where(Case.organization_id == org, EvidenceDocument.id.in_(ids["document"]))
        ).all():
            out[f"document:{d.id}"] = {"label": d.filename, "href": f"#case/{cid}/evidence/{d.id}"}
    if ids.get("finding"):
        for f, cid in db.execute(
            select(RiskFinding, Case.id).join(Case, Case.id == RiskFinding.case_id).where(Case.organization_id == org, RiskFinding.id.in_(ids["finding"]))
        ).all():
            out[f"finding:{f.id}"] = {"label": f.title, "href": f"#case/{cid}/findings/{f.id}"}
    if ids.get("workflow_action"):
        for a, _cid in db.execute(
            select(WorkflowAction, Case.id).join(Case, Case.id == WorkflowAction.case_id)
            .where(Case.organization_id == org, WorkflowAction.id.in_(ids["workflow_action"]))
        ).all():
            out[f"workflow_action:{a.id}"] = {"label": f"Action {a.seq}: {a.action.replace('_', ' ')}", "href": "#approvals"}
    if ids.get("property"):
        for p, cid in db.execute(
            select(Property, Case.id).join(Case, Case.id == Property.case_id).where(Case.organization_id == org, Property.id.in_(ids["property"]))
        ).all():
            out[f"property:{p.id}"] = {"label": p.address_line or p.parcel_number or "property", "href": f"#case/{cid}"}
    for t in ids.get("topic", []):
        out[f"topic:{t}"] = {"label": t, "href": None}
    return out


def suggestions(db: Session, user: User, memory: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    from app.domain.flow import case_nodes, critical_path, next_action

    out: list[dict[str, Any]] = []
    case_ids = [n["id"].split(":", 1)[1] for n in memory["nodes"] if n["kind"] == "case"][:3]
    cases = {c.id: c for c in db.scalars(select(Case).where(Case.organization_id == user.organization_id, Case.id.in_(case_ids)))}
    current_year = datetime.now(tz=UTC).year
    for cid in case_ids:
        case = cases.get(cid)
        if case is None or case.status != "open":
            continue
        nodes = case_nodes(db, case)
        step = next_action(nodes, critical_path(nodes))
        if step:
            out.append({"text": f"{case.reference}: {step['step']}", "href": step["href"], "reason": f"you worked on this recently · {step['remaining']} item(s) on its critical path"})
        years = sorted({o.tax_year for o in db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == cid)) if o.tax_year})
        if years and years[-1] < current_year:
            missing = list(range(years[-1] + 1, current_year + 1))
            out.append({
                "text": f"{case.reference}: tax years {', '.join(map(str, years))} are on file but {', '.join(map(str, missing))} is not — request the current assessment.",
                "href": f"#case/{cid}/financials", "reason": "based on prior years' obligations in this case",
            })
    for topic in memory.get("topics", [])[:2]:
        out.append({"text": f"Ask again about “{topic}” with the newest evidence", "prompt": f"What does the evidence say about {topic}?", "reason": "a topic you ask about often"})
    return out[:limit]


def context_lines(memory: dict[str, Any], limit: int = 8) -> list[str]:
    """A compact, label-only summary of the user's memory for a model prompt (no document text)."""
    lines = [f"- {n['kind']}: {n['label']}" for n in memory["nodes"][1 : limit + 1]]
    if memory.get("topics"):
        lines.append("- frequent topics: " + ", ".join(memory["topics"][:5]))
    return lines
