"""The rural-land case lifecycle: stage gates, the live workflow graph, and the human queue.

A land case moves through the stages a family really goes through (``workflows.LAND_STAGES``).
Every gate is computed from the case's own records, analyses and proceedings — never ticked by
hand — and the same facts drive the workflow graph (with the critical path and the least-effort
route, including the collateral-free bridge as an alternative edge) and the Needs-you items.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import (
    AuditEvent,
    Case,
    EvidenceDocument,
    FamilyMember,
    LandPassport,
    LandProceeding,
    ProceedingEvent,
    RiskFinding,
    User,
    WorkflowAction,
)

from . import proceedings, service
from .regions import PROCEEDING_KINDS, TERMINAL

RECONCILE_TYPES = ("land_name_mismatch", "land_extent_mismatch", "land_survey_mismatch", "land_boundary_conflict", "land_class_conflict", "land_government_land")
KEY_RECORDS = (("ror", "record of rights"), ("sketch", "survey sketch"), ("encumbrance", "encumbrance certificate"))
SIGNED = ("signed_letter", "registered_deed")


def _n(db: Session, model: Any, *where: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def proceedings_of(db: Session, case: Case) -> list[tuple[LandProceeding, list[ProceedingEvent]]]:
    rows = list(db.scalars(select(LandProceeding).where(LandProceeding.case_id == case.id).order_by(LandProceeding.created_at)))
    out = []
    for p in rows:
        events = list(db.scalars(select(ProceedingEvent).where(ProceedingEvent.proceeding_id == p.id).order_by(ProceedingEvent.event_date, ProceedingEvent.created_at)))
        out.append((p, events))
    return out


def facts(db: Session, case: Case) -> dict[str, Any]:
    """Everything the gates, the graph and the queue need, computed once."""
    parcel = service.parcel_of(db, case)
    records = service.records_of(db, case)
    members = service.members_of(db, case)
    have = {r.source_type for r in records}
    conc = service.run_concordance(db, case) if records else {"issues": [], "deceased_holders": [], "counts": {}}
    succ_snap = service.latest(db, case, "succession")
    ready_snap = service.latest(db, case, "readiness")
    val_snap = service.latest(db, case, "valuation")
    open_reconcile = _n(db, RiskFinding, RiskFinding.case_id == case.id, RiskFinding.status == "open", RiskFinding.finding_type.in_(RECONCILE_TYPES))
    procs = proceedings_of(db, case)
    today = date.today()
    health = {p.id: proceedings.health(p, ev, today) for p, ev in procs}
    deceased = bool(conc.get("deceased_holders"))
    living = [m for m in members if m.is_alive]
    shares = (succ_snap.result.get("shares", []) if succ_snap else [])
    co_owner_ids = {s["member_id"] for s in shares if s.get("alive")}
    co_owners = [m for m in living if m.id in co_owner_ids] if co_owner_ids else []
    mutation_ordered = any(p.kind == "mutation" and p.status == "ordered" for p, _ in procs)
    return {
        "parcel": parcel, "records": records, "have": have, "members": members, "conc": conc, "succ": succ_snap, "ready": ready_snap,
        "val": val_snap, "open_reconcile": open_reconcile, "procs": procs, "health": health, "deceased": deceased, "co_owners": co_owners,
        "signed": [m for m in co_owners if m.consent_evidence in SIGNED], "online": [m for m in co_owners if m.consent_evidence == "online"],
        "objected": [m for m in co_owners if m.consent_status == "objected"], "mutation_ordered": mutation_ordered,
        "passport": _n(db, LandPassport, LandPassport.case_id == case.id, LandPassport.status == "active"),
    }


def land_checklist(db: Session, case: Case, f: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    f = f or facts(db, case)
    parcel, have = f["parcel"], f["have"]
    missing = [label for key, label in KEY_RECORDS if key not in have]
    conc_snap = service.latest(db, case, "concordance")
    succ = f["succ"]
    co = f["co_owners"]
    mutation = next(((p, f["health"][p.id]) for p, _ in f["procs"] if p.kind == "mutation" and p.status not in ("withdrawn", "rejected")), None)
    ready = f["ready"]
    verdict = ready.result.get("verdict") if ready else None
    gates = [
        {"stage": "intake", "done": bool(parcel and parcel.survey_key) and (bool(f["members"]) or bool(parcel and parcel.recorded_holder)),
         "detail": (f"S.No. {parcel.survey_key} · {parcel.village or 'village?'}" if parcel and parcel.survey_key else "plot not entered") + f" · {len(f['members'])} in the family tree"},
        {"stage": "records", "done": not missing, "detail": f"{len(f['records'])} record(s)" + (f" · missing {', '.join(missing)}" if missing else " · patta, sketch and EC on file")},
        {"stage": "reconciled", "done": conc_snap is not None and f["open_reconcile"] == 0,
         "detail": ("not compared yet" if conc_snap is None else f"{f['open_reconcile']} difference(s) still open with an office" if f["open_reconcile"] else "differences explained or sent to the right office")},
        {"stage": "heirs", "done": (not f["deceased"]) or bool(succ and succ.result.get("status") == "computed"),
         "detail": "no deceased holder" if not f["deceased"] else (f"{succ.result.get('co_owners', 0)} co-owner(s), {succ.result.get('status')}" if succ else "draft shares not worked out")},
        {"stage": "consent", "done": (not f["deceased"]) or (bool(co) and len(f["signed"]) == len(co) and not f["objected"]),
         "detail": "not needed" if not f["deceased"] else f"{len(f['signed'])} signed · {len(f['online'])} agreed online · {len(f['objected'])} objected · of {len(co)}"},
        {"stage": "mutation", "done": not f["deceased"] or f["mutation_ordered"],
         "detail": ("record shows living holders" if not f["deceased"] else "mutation ordered — upload the new record" if f["mutation_ordered"]
                    else f"mutation {mutation[1]['status_label'].lower()} · {mutation[1]['signal'].replace('_', ' ')}" if mutation else "mutation not filed")},
        {"stage": "credit", "done": verdict in ("ready", "ready_without_collateral") or f["passport"] > 0,
         "detail": f"{ready.result.get('lender_label')}: {ready.result.get('score')}/100, {verdict.replace('_', ' ')}" if ready else "readiness not checked"},
        {"stage": "resolved", "done": case.status in ("resolved", "closed"), "detail": case.status},
    ]
    return gates


# ── the live workflow graph ──────────────────────────────────────────────────

LAND_EDGES: list[tuple[str, str, str]] = [
    ("documents", "records", "read"),
    ("records", "concordance", "compare"),
    ("records", "valuation", "extent · class"),
    ("family", "shares", "family tree"),
    ("concordance", "shares", "holder on record"),
    ("shares", "consents", "draft shares"),
    ("consents", "proceedings", "signed letters"),
    ("concordance", "proceedings", "corrections"),
    ("proceedings", "mutation", "order"),
    ("mutation", "readiness", "heirs on record"),
    ("valuation", "readiness", "lending value"),
    ("consents", "readiness", "alt: collateral-free crop loan"),
    ("concordance", "needs_you", "differences"),
    ("proceedings", "needs_you", "overdue · objections"),
    ("needs_you", "approvals", "proposals"),
    ("readiness", "resolution", "passport"),
    ("approvals", "resolution", "after your OK"),
]
LAND_LAYERS = {
    "documents": "evidence", "records": "evidence", "concordance": "evidence", "family": "analysis", "shares": "analysis", "consents": "analysis",
    "proceedings": "control", "mutation": "control", "valuation": "finance", "readiness": "finance", "needs_you": "control", "approvals": "control",
    "resolution": "control", "audit": "system",
}
LAND_POSITIONS = {
    "documents": (0, 0), "records": (1, 0), "concordance": (2, 0), "family": (0, 1), "shares": (1, 1), "consents": (2, 1),
    "proceedings": (3, 1), "mutation": (4, 1), "valuation": (2, 2), "readiness": (5, 1), "needs_you": (3, 0), "approvals": (4, 0),
    "resolution": (5, 0), "audit": (4, 2),
}
LAND_NEXT_STEP = {
    "documents": "Upload the patta / RTC, the adangal, the survey sketch, the EC and the death certificate.",
    "records": "Read the uploaded documents into land records and add what is missing.",
    "concordance": "Compare the records and send each difference to the office that corrects it.",
    "family": "Enter the family tree: who died, when, and their spouses and children.",
    "shares": "Work out the draft shares.",
    "consents": "Send consent links to co-heirs who live away and collect signed letters.",
    "proceedings": "File the legal heir certificate and mutation applications, and log what the office does.",
    "mutation": "Follow the mutation to its order, then upload the new record of rights.",
    "valuation": "Enter the guideline value and nearby sales, then estimate the value.",
    "readiness": "Check what the lender asks for and work through the fix plan.",
    "needs_you": "Clear the questions waiting on you.",
    "approvals": "An approver must confirm or reject the proposed actions.",
    "resolution": "Issue the land credit passport, then propose resolving the case.",
}


def _status(remaining: int, done: bool, blocked: bool = False) -> str:
    if blocked:
        return "blocked"
    if remaining > 0:
        return "attention"
    return "done" if done else "idle"


def land_nodes(db: Session, case: Case) -> dict[str, dict[str, Any]]:
    f = facts(db, case)
    cid, href = case.id, f"#case/{case.id}/land"
    docs = _n(db, EvidenceDocument, EvidenceDocument.case_id == cid)
    missing = [label for key, label in KEY_RECORDS if key not in f["have"]]
    conc = f["conc"]
    succ = f["succ"]
    co = f["co_owners"]
    unsigned = len(co) - len(f["signed"]) if f["deceased"] else 0
    active = [(p, f["health"][p.id]) for p, _ in f["procs"] if p.status not in TERMINAL]
    trouble = [h for _, h in active if h["signal"] in ("overdue", "stalled")]
    val = f["val"]
    ready = f["ready"]
    blocked_reqs = [r for r in (ready.result.get("requirements", []) if ready else []) if r["status"] in ("missing", "blocked")]
    proposed = _n(db, WorkflowAction, WorkflowAction.case_id == cid, WorkflowAction.status == "proposed")
    serious = _n(db, RiskFinding, RiskFinding.case_id == cid, RiskFinding.status == "open", RiskFinding.severity.in_(("critical", "high")))
    objections = len(f["objected"]) + sum(1 for _, h in active if h.get("objection"))
    info = sum(1 for m in f["members"] if m.is_alive and m.consent_status == "needs_info")
    needs = serious + proposed + len(trouble) + objections + info
    audit_count = _n(db, AuditEvent, AuditEvent.case_id == cid)
    gates = {g["stage"]: g for g in land_checklist(db, case, f)}
    mutation_left = 1 if f["deceased"] and not f["mutation_ordered"] else 0
    filed_needed = mutation_left and not any(p.kind == "mutation" for p, _ in f["procs"])
    return {
        "documents": {"label": "Documents", "metric": docs, "unit": "uploaded", "remaining": 0 if docs else 1, "sub": "evidence tab",
                      "stats": {}, "status": _status(0 if docs else 1, docs > 0), "href": f"#case/{cid}/evidence"},
        "records": {"label": "Land records", "metric": len(f["records"]), "unit": "records", "remaining": len(missing),
                    "sub": ("missing " + ", ".join(missing)) if missing else "patta, sketch and EC on file", "stats": {"missing": len(missing)},
                    "status": _status(len(missing), bool(f["records"]), blocked=not docs and not f["records"]), "href": f"{href}/records"},
        "concordance": {"label": "Concordance", "metric": len(conc.get("issues", [])), "unit": "differences", "remaining": f["open_reconcile"],
                        "sub": " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in conc.get("counts", {}).items() if v) or "records agree",
                        "stats": dict(conc.get("counts", {})), "status": _status(f["open_reconcile"], bool(f["records"]), blocked=not f["records"]), "href": f"{href}/records"},
        "family": {"label": "Family tree", "metric": len(f["members"]), "unit": "people", "remaining": 0 if f["members"] or not f["deceased"] else 1,
                   "sub": f"{sum(1 for m in f['members'] if not m.is_alive)} deceased · {sum(1 for m in f['members'] if m.is_alive and not m.lives_in_village)} away",
                   "stats": {}, "status": _status(0 if f["members"] else int(f["deceased"]), bool(f["members"])), "href": f"{href}/family"},
        "shares": {"label": "Draft shares", "metric": len(co), "unit": "co-owners", "remaining": 0 if gates["heirs"]["done"] else 1 + len((succ.result.get("unresolved") or []) if succ else []),
                   "sub": gates["heirs"]["detail"], "stats": {}, "status": _status(0 if gates["heirs"]["done"] else 1, gates["heirs"]["done"], blocked=not f["members"] and f["deceased"]), "href": f"{href}/family"},
        "consents": {"label": "Consents", "metric": len(f["signed"]), "unit": "signed", "remaining": unsigned + len(f["objected"]),
                     "sub": gates["consent"]["detail"], "stats": {"online": len(f["online"]), "objected": len(f["objected"])},
                     "status": _status(unsigned + len(f["objected"]), gates["consent"]["done"], blocked=not co and f["deceased"]), "href": f"{href}/family"},
        "proceedings": {"label": "Office proceedings", "metric": len(active), "unit": "active", "remaining": len(trouble) + (1 if filed_needed else 0),
                        "sub": " · ".join(f"{PROCEEDING_KINDS[p.kind]['label']}: {h['signal'].replace('_', ' ')}" for p, h in active[:3]) or "nothing filed",
                        "stats": {"overdue": sum(1 for h in trouble if h["signal"] == "overdue"), "stalled": sum(1 for h in trouble if h["signal"] == "stalled")},
                        "status": "attention" if trouble else _status(1 if filed_needed else 0, bool(f["procs"])), "href": f"{href}/proceedings"},
        "mutation": {"label": "Mutation", "metric": int(f["mutation_ordered"]), "unit": "ordered", "remaining": mutation_left, "sub": gates["mutation"]["detail"],
                     "stats": {}, "status": _status(mutation_left, gates["mutation"]["done"]), "href": f"{href}/proceedings"},
        "valuation": {"label": "Valuation", "metric": int(val.result.get("p50_inr", 0) // 100_000) if val and val.result.get("status") == "estimated" else 0, "unit": "lakh (P50)",
                      "remaining": 0 if val and val.result.get("status") == "estimated" else 1,
                      "sub": f"{val.result.get('confidence')} confidence · lend at P20 less haircut" if val and val.result.get("status") == "estimated" else "not estimated",
                      "stats": {}, "status": _status(0 if val else 1, bool(val)), "href": f"{href}/value"},
        "readiness": {"label": "Lender readiness", "metric": ready.result.get("score", 0) if ready else 0, "unit": "/100", "remaining": len(blocked_reqs) if ready else 1,
                      "sub": gates["credit"]["detail"], "stats": {"blocked": len(blocked_reqs)}, "status": _status(len(blocked_reqs) if ready else 1, gates["credit"]["done"]), "href": f"{href}/lender"},
        "needs_you": {"label": "Needs you", "metric": needs, "unit": "questions", "remaining": needs,
                      "sub": f"{serious} serious findings · {len(trouble)} overdue/stalled files · {objections} objection(s) · {info} co-heir question(s) · {proposed} approvals",
                      "stats": {}, "status": "attention" if needs else "done", "href": f"#case/{cid}/workflow"},
        "approvals": {"label": "Approvals", "metric": proposed, "unit": "waiting", "remaining": proposed, "sub": "two-person, single use",
                      "stats": {}, "status": _status(proposed, True), "href": "#approvals"},
        "resolution": {"label": "Resolution", "metric": f["passport"], "unit": "passport", "remaining": 0 if case.status != "open" else (0 if gates["credit"]["done"] else 1),
                       "sub": f"stage: {case.stage} · case {case.status}", "stats": {}, "status": "done" if case.status != "open" else _status(0 if gates["credit"]["done"] else 1, gates["credit"]["done"]),
                       "href": f"{href}/lender"},
        "audit": {"label": "Audit chain", "metric": audit_count, "unit": "events", "remaining": 0, "sub": "hash-chained", "stats": {}, "status": "done" if audit_count else "idle", "href": f"#case/{cid}/history"},
    }


# ── the human queue: land items ──────────────────────────────────────────────


def needs_you_items(db: Session, user: User, scope: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    today = date.today()
    for case in db.scalars(select(Case).where(*scope, Case.kind == "rural_land")):
        for p, events in proceedings_of(db, case):
            if p.status in TERMINAL:
                continue
            h = proceedings.health(p, events, today)
            label = PROCEEDING_KINDS[p.kind]["label"]
            if h.get("objection"):
                items.append(_item(case, "objection", f"Objection on the {label.lower()} — prepare for the hearing", "Review the objection; consider mediation (DLSA)", "critical", p, "proceedings"))
            if h["signal"] in ("overdue", "stalled"):
                why = f"{h.get('overdue_days')} days past the {p.sla_days}-day timeline" if h["signal"] == "overdue" else f"no movement for {h['last_event_days']} days"
                items.append(_item(case, "proceeding", f"{label} at {p.office}: {why}", "Follow up, or send the drafted escalation", "high", p, "proceedings"))
            elif h["signal"] == "not_filed":
                items.append(_item(case, "proceeding", f"{label} drafted but not filed", "File it and record the acknowledgement number", "medium", p, "proceedings"))
            if h.get("waiting_on") == "family" and h["signal"] != "not_filed":
                items.append(_item(case, "proceeding", f"The office asked for a document on the {label.lower()}", "Submit it and log the submission", "high", p, "proceedings"))
        for m in db.scalars(select(FamilyMember).where(FamilyMember.case_id == case.id, FamilyMember.consent_status.in_(("objected", "needs_info")))):
            sev = "high" if m.consent_status == "objected" else "medium"
            title = f"{m.name} {'objected to' if m.consent_status == 'objected' else 'asked for more information about'} the draft shares"
            items.append({
                "kind": "consent", "case_id": case.id, "case_reference": case.reference, "title": title,
                "suggests": "Call them; offer mediation through the DLSA" if m.consent_status == "objected" else "Explain the shares and resend the link",
                "severity": sev, "amount_cents": 0, "href": f"#case/{case.id}/land/family", "created_at": m.consent_at or m.created_at, "count": 1, "id": m.id,
            })
    return items


def _item(case: Case, kind: str, title: str, suggests: str, severity: str, p: LandProceeding, tab: str) -> dict[str, Any]:
    created = p.updated_at or p.created_at
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "kind": kind, "case_id": case.id, "case_reference": case.reference, "title": title, "suggests": suggests, "severity": severity,
        "amount_cents": 0, "href": f"#case/{case.id}/land/{tab}", "created_at": created, "count": 1, "id": p.id,
    }

