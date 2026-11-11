"""The centralized workflow graph: one live, computed view of a case (or a whole organisation).

Every node is a stage of the real Groundwork pipeline and every number on it is counted from
the database at request time — nothing is stored, cached or hard-coded:

    evidence layer   sources → extraction → pii ─┐
                                   │              │
                                   ├→ ownership ──┼→ findings → needs_you → approvals → resolution
    finance layer    obligations ──┼→ reconciliation ┘                ↑
                     payments ─────┘   obligations ─(mark paid)───────┘
    control layer    audit (every node writes to it) · report

Two classic scheduling algorithms run over that DAG, with each node weighted by the work it
still has outstanding (unreviewed facts, open exceptions, open serious findings, …):

* **Critical Path Method** (Kelley & Walker, 1959): the longest weighted path to resolution —
  the chain of work that actually bounds how soon the case can resolve. Its first unfinished
  node is the bottleneck, which is what "next best action" points at.
* **Dijkstra's shortest path** (1959): the least-effort route to resolution when the graph
  offers alternatives (e.g. settling an obligation through reconciliation vs. an approved
  ``mark_obligation_paid`` action).

``needs_you`` is the human queue (the "Needs you" card), built for the signed-in user from
their permissions and ranked by weighted priority (severity × money × age).
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import has_permission
from app.domain.ownership import analyze as ownership_analyze
from app.domain.reconciliation.service import latest_run
from app.domain.reconciliation.service import summary as money_summary
from app.domain.workflows import checklist
from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    Decision,
    EvidenceDocument,
    ExtractedFact,
    FinancialObligation,
    OwnershipEvent,
    ReconciliationResult,
    ReconciliationRun,
    RiskFinding,
    Task,
    Transaction,
    User,
    WorkflowAction,
)

SEVERITY_WEIGHT = {"critical": 8.0, "high": 4.0, "medium": 2.0, "low": 1.0, "info": 0.5}

# (from, to, label) — the dependency DAG. "alt" edges are alternative routes Dijkstra may take.
EDGES: list[tuple[str, str, str]] = [
    ("sources", "extraction", "text + pages"),
    ("extraction", "pii", "inspect"),
    ("extraction", "ownership", "reviewed facts"),
    ("extraction", "obligations", "tax / lien facts"),
    ("obligations", "reconciliation", "match"),
    ("payments", "reconciliation", "match"),
    ("ownership", "findings", "chain issues"),
    ("reconciliation", "findings", "exceptions"),
    ("pii", "findings", "protected"),
    ("findings", "needs_you", "unsure / serious"),
    ("needs_you", "approvals", "proposals"),
    ("obligations", "approvals", "alt: mark paid"),
    ("approvals", "resolution", "after your OK"),
    ("resolution", "report", "history"),
]
LAYERS = {
    "sources": "evidence", "extraction": "evidence", "pii": "evidence", "ownership": "evidence",
    "obligations": "finance", "payments": "finance", "reconciliation": "finance",
    "findings": "analysis", "needs_you": "control", "approvals": "control", "resolution": "control",
    "report": "control", "audit": "system",
}
# Grid position (column, row) for the canvas; the frontend lays out from these.
POSITIONS = {
    "sources": (0, 0), "extraction": (1, 0), "pii": (2, 0), "ownership": (2, 1),
    "obligations": (1, 2), "payments": (0, 2), "reconciliation": (2, 2),
    "findings": (3, 1), "needs_you": (4, 0), "approvals": (4, 1), "resolution": (5, 1), "report": (5, 2), "audit": (4, 2),
}


def _n(db: Session, model: Any, *where: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _status(remaining: int, done: bool, blocked: bool = False) -> str:
    if blocked:
        return "blocked"
    if remaining > 0:
        return "attention"
    return "done" if done else "idle"


# ── per-case graph ───────────────────────────────────────────────────────────


def case_nodes(db: Session, case: Case) -> dict[str, dict[str, Any]]:
    cid = case.id
    docs = list(db.scalars(select(EvidenceDocument).where(EvidenceDocument.case_id == cid)))
    by_status: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    pii_total = 0
    pii_docs = 0
    for d in docs:
        by_status[d.status] += 1
        by_type[d.doc_type] += 1
        total = int((d.pii_summary or {}).get("total") or 0)
        pii_total += total
        pii_docs += 1 if total else 0
    fact_rows = dict(db.execute(select(ExtractedFact.status, func.count()).where(ExtractedFact.case_id == cid).group_by(ExtractedFact.status)).all())
    facts_total = sum(fact_rows.values())
    unreviewed = int(fact_rows.get("unreviewed", 0))
    reviewed = int(fact_rows.get("confirmed", 0)) + int(fact_rows.get("corrected", 0))

    events = _n(db, OwnershipEvent, OwnershipEvent.case_id == cid)
    chain = ownership_analyze(db, case) if events else {"issues": [], "current_owners": [], "deceased_current_owners": []}

    obligations = list(db.scalars(select(FinancialObligation).where(FinancialObligation.case_id == cid)))
    txns = db.execute(select(func.count(), func.coalesce(func.sum(Transaction.amount_cents), 0)).where(Transaction.case_id == cid)).one()
    run = latest_run(db, case)
    money = money_summary(db, case, run)
    outcomes: dict[str, int] = defaultdict(int)
    open_review = 0
    if run is not None:
        for outcome, review, count in db.execute(
            select(ReconciliationResult.outcome, ReconciliationResult.review_status, func.count())
            .where(ReconciliationResult.run_id == run.id)
            .group_by(ReconciliationResult.outcome, ReconciliationResult.review_status)
        ).all():
            outcomes[outcome] += count
            if outcome in ("PROPOSED", "EXCEPTION") and review == "open":
                open_review += count

    sev = dict(db.execute(select(RiskFinding.severity, func.count()).where(RiskFinding.case_id == cid, RiskFinding.status == "open").group_by(RiskFinding.severity)).all())
    serious = int(sev.get("critical", 0)) + int(sev.get("high", 0))
    proposed = _n(db, WorkflowAction, WorkflowAction.case_id == cid, WorkflowAction.status == "proposed")
    executed = _n(db, WorkflowAction, WorkflowAction.case_id == cid, WorkflowAction.status == "executed")
    open_tasks = _n(db, Task, Task.case_id == cid, Task.status.in_(("open", "in_progress")))
    decisions = _n(db, Decision, Decision.case_id == cid)
    audit_count = _n(db, AuditEvent, AuditEvent.case_id == cid)
    last_audit = db.scalar(select(AuditEvent.occurred_at).where(AuditEvent.case_id == cid).order_by(AuditEvent.seq.desc()).limit(1))
    unreadable = by_status.get("needs_ocr", 0) + by_status.get("failed", 0)
    gates = {g["stage"]: g for g in checklist(db, case)}
    needs = unreviewed + open_review + serious + proposed + unreadable
    ownership_left = len(chain["issues"]) + (0 if events else 1)
    recon_left = open_review + (0 if run or not obligations else 1)
    findings_left = serious + (0 if case.detection_ran_at else 1)
    resolution_left = open_tasks + (0 if gates["resolution"]["done"] or case.status != "open" else 1)

    href = f"#case/{cid}"
    return {
        "sources": {
            "label": "Sources", "metric": len(docs), "unit": "documents", "remaining": 0 if docs else 1,
            "sub": " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(by_type.items())) or "no evidence uploaded",
            "stats": dict(by_status), "status": _status(0 if docs else 1, bool(docs)), "href": f"{href}/evidence",
        },
        "extraction": {
            "label": "Extraction", "metric": facts_total, "unit": "facts", "remaining": unreviewed + unreadable,
            "sub": f"{reviewed} reviewed · {unreviewed} awaiting review" + (f" · {unreadable} unreadable" if unreadable else ""),
            "stats": {**{k: int(v) for k, v in fact_rows.items()}, "unreadable_documents": unreadable},
            "status": _status(unreviewed + unreadable, facts_total > 0, blocked=not docs), "href": f"{href}/evidence",
        },
        "pii": {
            "label": "PII protection", "metric": pii_total, "unit": "detections", "remaining": 0,
            "sub": f"{pii_docs} document(s) contain sensitive data · values never stored", "stats": {"documents": pii_docs},
            "status": "done" if docs else "idle", "href": f"{href}/evidence",
        },
        "ownership": {
            "label": "Ownership chain", "metric": events, "unit": "events", "remaining": ownership_left,
            "sub": ("current: " + ", ".join(chain["current_owners"][:3])) if chain["current_owners"] else "chain not reconstructed",
            "stats": {"issues": len(chain["issues"]), "deceased_current_owners": len(chain["deceased_current_owners"])},
            "status": _status(ownership_left, events > 0, blocked=reviewed == 0 and events == 0), "href": f"{href}/ownership",
        },
        "obligations": {
            "label": "Obligations", "metric": len(obligations), "unit": "obligations", "remaining": 0,
            "sub": f"{_money(sum(o.amount_cents for o in obligations))} owed · {sum(1 for o in obligations if o.status != 'paid')} open",
            "stats": {"total_cents": sum(o.amount_cents for o in obligations)}, "status": "done" if obligations else "idle", "href": f"{href}/financials",
        },
        "payments": {
            "label": "Payments", "metric": int(txns[0]), "unit": "payments", "remaining": 0,
            "sub": f"{_money(int(txns[1]))} received", "stats": {"total_cents": int(txns[1])}, "status": "done" if txns[0] else "idle", "href": f"{href}/financials",
        },
        "reconciliation": {
            "label": "Reconciliation", "metric": outcomes.get("AUTO_MATCHED", 0), "unit": "auto-matched", "remaining": recon_left,
            "sub": f"{outcomes.get('PROPOSED', 0)} proposed · {outcomes.get('EXCEPTION', 0)} exceptions · {_money(money['outstanding_cents'])} outstanding"
            if run else ("not run" if obligations else "nothing to reconcile"),
            "stats": {**dict(outcomes), "open_review": open_review, **{k: v for k, v in money.items() if k.endswith("_cents")}},
            "status": _status(recon_left, run is not None, blocked=not obligations), "href": f"{href}/financials",
        },
        "findings": {
            "label": "Findings", "metric": sum(int(v) for v in sev.values()), "unit": "open", "remaining": findings_left,
            "sub": " · ".join(f"{int(sev[k])} {k}" for k in ("critical", "high", "medium", "low", "info") if sev.get(k))
            or ("none open" if case.detection_ran_at else "detection not run"),
            "stats": {k: int(v) for k, v in sev.items()}, "status": _status(findings_left, case.detection_ran_at is not None), "href": f"{href}/findings",
        },
        "needs_you": {
            "label": "Needs you", "metric": needs, "unit": "questions", "remaining": needs,
            "sub": f"{unreviewed} facts · {open_review} matches · {serious} serious findings · {proposed} approvals",
            "stats": {"facts": unreviewed, "matches": open_review, "findings": serious, "approvals": proposed, "unreadable": unreadable},
            "status": "attention" if needs else "done", "href": f"{href}/workflow",
        },
        "approvals": {
            "label": "Approvals", "metric": proposed, "unit": "waiting", "remaining": proposed,
            "sub": f"{executed} executed · single-use, two-person", "stats": {"proposed": proposed, "executed": executed},
            "status": _status(proposed, executed > 0), "href": "#approvals",
        },
        "resolution": {
            "label": "Resolution", "metric": decisions, "unit": "decisions", "remaining": resolution_left,
            "sub": f"stage: {case.stage} · {open_tasks} open task(s) · case {case.status}",
            "stats": {"open_tasks": open_tasks, "stage": case.stage},
            "status": "done" if case.status != "open" else _status(resolution_left, gates["resolution"]["done"]), "href": f"{href}/workflow",
        },
        "report": {
            "label": "Report", "metric": 1 if docs else 0, "unit": "report", "remaining": 0, "sub": "markdown case report with citations",
            "stats": {}, "status": "done" if case.status != "open" else "idle", "href": f"{href}/overview",
        },
        "audit": {
            "label": "Audit chain", "metric": audit_count, "unit": "events", "remaining": 0,
            "sub": f"hash-chained · last {last_audit[:19].replace('T', ' ') if last_audit else '—'}",
            "stats": {}, "status": "done" if audit_count else "idle", "href": f"{href}/history",
        },
    }


def critical_path(nodes: dict[str, dict[str, Any]], target: str = "resolution", edges: list[tuple[str, str, str]] | None = None) -> list[str]:
    """CPM: the longest remaining-work path ending at ``target`` (topological DP over the DAG)."""
    preds: dict[str, list[str]] = defaultdict(list)
    for a, b, label in edges or EDGES:
        if not label.startswith("alt"):
            preds[b].append(a)
    memo: dict[str, tuple[float, list[str]]] = {}

    def best(node: str) -> tuple[float, list[str]]:
        if node in memo:
            return memo[node]
        weight = float(nodes.get(node, {}).get("remaining", 0))
        options = [best(p) for p in preds.get(node, [])]
        prev = max(options, key=lambda o: o[0], default=(0.0, []))
        memo[node] = (prev[0] + weight, [*prev[1], node])
        return memo[node]

    return best(target)[1]


def shortest_path(
    nodes: dict[str, dict[str, Any]], source: str = "sources", target: str = "resolution", edges: list[tuple[str, str, str]] | None = None
) -> tuple[float, list[str]]:
    """Dijkstra over all edges (alternatives included); entering a node costs 1 + its remaining work."""
    adj: dict[str, list[str]] = defaultdict(list)
    for a, b, _ in edges or EDGES:
        adj[a].append(b)
    dist = {source: float(nodes.get(source, {}).get("remaining", 0))}
    prev: dict[str, str] = {}
    heap = [(dist[source], source)]
    while heap:
        d, node = heapq.heappop(heap)
        if node == target:
            break
        if d > dist.get(node, math.inf):
            continue
        for nxt in adj[node]:
            nd = d + 1 + float(nodes.get(nxt, {}).get("remaining", 0))
            if nd < dist.get(nxt, math.inf):
                dist[nxt], prev[nxt] = nd, node
                heapq.heappush(heap, (nd, nxt))
    if target not in dist:
        return math.inf, []
    path, node = [target], target
    while node in prev:
        node = prev[node]
        path.append(node)
    return dist[target], path[::-1]


NEXT_STEP = {
    "sources": "Upload the deed, death certificate and tax records for this property.",
    "extraction": "Review the extracted facts (confirm, correct or reject) and re-run OCR on unreadable files.",
    "ownership": "Reconstruct the ownership chain from reviewed facts and resolve the chain issues it reports.",
    "obligations": "Derive tax and lien obligations from the evidence, or add them manually.",
    "payments": "Import the payment history (CSV) or record payments.",
    "reconciliation": "Run reconciliation and accept or reject the proposed matches and exceptions.",
    "findings": "Run discrepancy detection, then review the critical and high findings.",
    "needs_you": "Clear the questions waiting on you.",
    "approvals": "An approver must confirm or reject the proposed actions.",
    "resolution": "Record the decisions and close the open tasks, then propose resolution.",
}


def next_action(nodes: dict[str, dict[str, Any]], path: list[str], steps: dict[str, str] | None = None) -> dict[str, Any] | None:
    for key in path:
        if nodes[key]["remaining"] > 0:
            return {"node": key, "label": nodes[key]["label"], "remaining": nodes[key]["remaining"], "step": (steps or NEXT_STEP).get(key, ""), "href": nodes[key]["href"]}
    return None


HEIRS_SPEC = {"edges": EDGES, "layers": LAYERS, "positions": POSITIONS, "source": "sources", "steps": NEXT_STEP}


def land_spec() -> dict[str, Any]:
    from app.domain.land.lifecycle import LAND_EDGES, LAND_LAYERS, LAND_NEXT_STEP, LAND_POSITIONS

    return {"edges": LAND_EDGES, "layers": LAND_LAYERS, "positions": LAND_POSITIONS, "source": "documents", "steps": LAND_NEXT_STEP}


def _graph(nodes: dict[str, dict[str, Any]], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = spec or HEIRS_SPEC
    cpath = critical_path(nodes, edges=spec["edges"])
    cost, spath = shortest_path(nodes, source=spec["source"], edges=spec["edges"])
    out_nodes = [{"id": k, "layer": spec["layers"][k], "col": spec["positions"][k][0], "row": spec["positions"][k][1], **v} for k, v in nodes.items()]
    crit_edges = set(zip(cpath, cpath[1:], strict=False))
    short_edges = set(zip(spath, spath[1:], strict=False))
    edges = [
        {"from": a, "to": b, "label": label, "critical": (a, b) in crit_edges, "shortest": (a, b) in short_edges, "alt": label.startswith("alt")}
        for a, b, label in spec["edges"]
    ]
    edges += [{"from": k, "to": "audit", "label": "", "critical": False, "shortest": False, "alt": False, "audit": True} for k in ("needs_you", "approvals")]
    return {
        "nodes": out_nodes,
        "edges": edges,
        "critical_path": cpath,
        "shortest_path": {"cost": None if math.isinf(cost) else cost, "nodes": spath},
        "remaining_work": sum(n["remaining"] for n in nodes.values()),
        "next_action": next_action(nodes, cpath, spec["steps"]),
        "algorithms": {
            "critical_path": "Critical Path Method (Kelley & Walker 1959): longest remaining-work path to resolution",
            "shortest_path": "Dijkstra (1959): least-effort route to resolution, alternatives included",
            "needs_you": "weighted priority = severity × (1 + log10(1 + amount/$1,000)) × age factor",
        },
    }


def case_flow(db: Session, case: Case, user: User) -> dict[str, Any]:
    if case.kind == "rural_land":
        from app.domain.land.lifecycle import land_nodes

        graph = _graph(land_nodes(db, case), land_spec())
        graph["algorithms"]["shortest_path"] = "Dijkstra (1959): least-effort route to credit — including the collateral-free crop loan as an alternative to waiting for mutation"
    else:
        graph = _graph(case_nodes(db, case))
    graph["needs_you"] = needs_you(db, user, case_id=case.id)
    graph["generated_at"] = datetime.now(tz=UTC).isoformat()
    graph["scope"] = {"kind": "case", "id": case.id, "reference": case.reference, "title": case.title}
    return graph


# ── organisation graph: the same nodes, summed over open cases ───────────────


def org_flow(db: Session, user: User) -> dict[str, Any]:
    cases = list(db.scalars(
        select(Case).where(Case.organization_id == user.organization_id, Case.status == "open").order_by(Case.updated_at.desc()).limit(200)
    ))
    total: dict[str, dict[str, Any]] = {}
    per_case = []
    for case in cases:
        if case.kind == "rural_land":
            from app.domain.land.lifecycle import land_nodes

            spec = land_spec()
            lnodes = land_nodes(db, case)
            per_case.append({
                "id": case.id, "reference": case.reference, "title": case.title, "stage": case.stage, "kind": case.kind,
                "remaining": sum(n["remaining"] for n in lnodes.values()),
                "next_action": next_action(lnodes, critical_path(lnodes, edges=spec["edges"]), spec["steps"]),
            })
            continue
        nodes = case_nodes(db, case)
        per_case.append({
            "id": case.id, "reference": case.reference, "title": case.title, "stage": case.stage,
            "remaining": sum(n["remaining"] for n in nodes.values()), "next_action": next_action(nodes, critical_path(nodes)),
        })
        for key, node in nodes.items():
            agg = total.setdefault(key, {**node, "metric": 0, "remaining": 0, "stats": defaultdict(int)})
            agg["metric"] += node["metric"]
            agg["remaining"] += node["remaining"]
            for sk, sv in node["stats"].items():
                if isinstance(sv, int | float):
                    agg["stats"][sk] += sv
    if not total:
        total = {k: {"label": k.replace("_", " ").title(), "metric": 0, "remaining": 0, "stats": {}, "unit": ""} for k in POSITIONS}
    for key, node in total.items():
        node["stats"] = dict(node["stats"])
        node["href"] = {"approvals": "#approvals", "findings": "#findings", "audit": "#audit"}.get(key, "#cases")
        node["status"] = "attention" if node["remaining"] else ("done" if node["metric"] else "idle")
        node["sub"] = f"across {len(cases)} open case(s)" if cases else "no open cases"
    graph = _graph(total)
    per_case.sort(key=lambda c: -c["remaining"])
    graph["cases"] = per_case[:25]
    graph["needs_you"] = needs_you(db, user)
    graph["generated_at"] = datetime.now(tz=UTC).isoformat()
    graph["scope"] = {"kind": "organization", "id": user.organization_id, "open_cases": len(cases)}
    return graph


# ── the human queue ──────────────────────────────────────────────────────────


def _age_factor(created: datetime | None) -> float:
    if created is None:
        return 1.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    days = max(0.0, (datetime.now(tz=UTC) - created).total_seconds() / 86400)
    return 1.0 + min(days, 30) / 30  # up to 2× after a month waiting


def priority(severity: str, amount_cents: int, created: datetime | None) -> float:
    return round(SEVERITY_WEIGHT.get(severity, 1.0) * (1 + math.log10(1 + abs(amount_cents) / 100_000)) * _age_factor(created), 3)


def needs_you(db: Session, user: User, *, case_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    org = user.organization_id
    scope = [Case.organization_id == org, Case.status == "open"] + ([Case.id == case_id] if case_id else [])
    items: list[dict[str, Any]] = []

    if has_permission(user.role, "evidence:write"):
        rows = db.execute(
            select(Case.id, Case.reference, EvidenceDocument.id, EvidenceDocument.filename, func.count(ExtractedFact.id), func.min(ExtractedFact.created_at))
            .join(ExtractedFact, ExtractedFact.case_id == Case.id)
            .join(EvidenceDocument, EvidenceDocument.id == ExtractedFact.document_id)
            .where(*scope, ExtractedFact.status == "unreviewed")
            .group_by(Case.id, Case.reference, EvidenceDocument.id, EvidenceDocument.filename)
        ).all()
        for cid, ref, doc_id, filename, count, created in rows:
            items.append({
                "kind": "fact_review", "case_id": cid, "case_reference": ref, "title": f"{count} extracted fact(s) awaiting review in {filename}",
                "suggests": "Confirm, correct or reject", "severity": "medium", "amount_cents": 0,
                "href": f"#case/{cid}/evidence/{doc_id}", "created_at": created, "count": int(count), "id": doc_id,
            })
        for cid, ref, doc_id, filename, status, created in db.execute(
            select(Case.id, Case.reference, EvidenceDocument.id, EvidenceDocument.filename, EvidenceDocument.status, EvidenceDocument.created_at)
            .join(EvidenceDocument, EvidenceDocument.case_id == Case.id)
            .where(*scope, EvidenceDocument.status.in_(("needs_ocr", "failed")))
        ).all():
            items.append({
                "kind": "unreadable_document", "case_id": cid, "case_reference": ref, "title": f"{filename} could not be read ({status.replace('_', ' ')})",
                "suggests": "Re-run with Document AI or upload a better copy", "severity": "medium", "amount_cents": 0,
                "href": f"#case/{cid}/evidence/{doc_id}", "created_at": created, "count": 1, "id": doc_id,
            })

    if has_permission(user.role, "finding:review"):
        latest = (
            select(ReconciliationRun.case_id, func.max(ReconciliationRun.created_at).label("at"))
            .join(Case, Case.id == ReconciliationRun.case_id)
            .where(*scope, ReconciliationRun.kind == "property_case")
            .group_by(ReconciliationRun.case_id)
            .subquery()
        )
        for cid, ref, rid, outcome, amount, created, rule in db.execute(
            select(
                Case.id, Case.reference, ReconciliationResult.id, ReconciliationResult.outcome, ReconciliationResult.amount_cents,
                ReconciliationResult.created_at, ReconciliationResult.rule,
            )
            .join(ReconciliationRun, ReconciliationRun.case_id == Case.id)
            .join(latest, (latest.c.case_id == ReconciliationRun.case_id) & (latest.c.at == ReconciliationRun.created_at))
            .join(ReconciliationResult, ReconciliationResult.run_id == ReconciliationRun.id)
            .where(*scope, ReconciliationResult.outcome.in_(("PROPOSED", "EXCEPTION")), ReconciliationResult.review_status == "open")
        ).all():
            items.append({
                "kind": "match_review", "case_id": cid, "case_reference": ref,
                "title": f"{'Proposed match' if outcome == 'PROPOSED' else 'Unmatched item'} of {_money(amount)} ({rule.replace('_', ' ').lower()})",
                "suggests": "Accept or reject" if outcome == "PROPOSED" else "Investigate the exception",
                "severity": "high" if outcome == "EXCEPTION" else "medium",
                "amount_cents": int(amount), "href": f"#case/{cid}/financials", "created_at": created, "count": 1, "id": rid,
            })
        for cid, ref, fid, title, severity, created in db.execute(
            select(Case.id, Case.reference, RiskFinding.id, RiskFinding.title, RiskFinding.severity, RiskFinding.created_at)
            .join(RiskFinding, RiskFinding.case_id == Case.id)
            .where(*scope, RiskFinding.status == "open", RiskFinding.severity.in_(("critical", "high")))
        ).all():
            items.append({
                "kind": "finding", "case_id": cid, "case_reference": ref, "title": title, "suggests": "Acknowledge, resolve or dismiss with a note",
                "severity": severity, "amount_cents": 0, "href": f"#case/{cid}/findings/{fid}", "created_at": created, "count": 1, "id": fid,
            })

    for cid, ref, aid, seq, action, proposer, created in db.execute(
        select(Case.id, Case.reference, WorkflowAction.id, WorkflowAction.seq, WorkflowAction.action, WorkflowAction.proposed_by_id, WorkflowAction.created_at)
        .join(WorkflowAction, WorkflowAction.case_id == Case.id)
        .where(*scope, WorkflowAction.status == "proposed")
    ).all():
        mine = proposer == user.id
        if not mine and not has_permission(user.role, "action:approve"):
            continue
        items.append({
            "kind": "approval", "case_id": cid, "case_reference": ref,
            "title": f"Action {seq}: {action.replace('_', ' ')}" + (" — waiting on another approver" if mine else ""),
            "suggests": "Waiting" if mine else f"Type “approve action {seq}” or reject", "severity": "high", "amount_cents": 0,
            "href": "#approvals", "created_at": created, "count": 1, "id": aid,
        })

    for cid, ref, tid, title, due, created in db.execute(
        select(Case.id, Case.reference, Task.id, Task.title, Task.due_date, Task.created_at)
        .join(Task, Task.case_id == Case.id)
        .where(*scope, Task.assignee_id == user.id, Task.status.in_(("open", "in_progress")))
    ).all():
        items.append({
            "kind": "task", "case_id": cid, "case_reference": ref, "title": title, "suggests": f"due {due}" if due else "Assigned to you",
            "severity": "medium", "amount_cents": 0, "href": f"#case/{cid}/workflow", "created_at": created, "count": 1, "id": tid,
        })

    if has_permission(user.role, "evidence:write"):
        from app.domain.land.lifecycle import needs_you_items

        items += needs_you_items(db, user, scope)

    for item in items:
        item["priority"] = priority(item["severity"], item["amount_cents"], item["created_at"])
        item["created_at"] = item["created_at"].isoformat() if isinstance(item["created_at"], datetime) else item["created_at"]
    items.sort(key=lambda i: -i["priority"])
    return items[:limit]
