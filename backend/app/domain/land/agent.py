"""The land case agent: prepares the whole case, hands off what only people and offices can do.

A bounded plan-act-observe loop over an allowlist of tools. Each tool is deterministic and
read-mostly (it computes and snapshots; it never changes who owns anything). The agent:

1. inventories the case (documents, records, family tree, comparables, costs);
2. picks the next tool whose inputs are present, runs it, observes the result;
3. when an input is missing that only a person can supply — a family tree, a guideline value,
   a co-heir's consent, a certified copy from an office — it records a **handoff** saying who
   must act, where, and why, instead of guessing;
4. proposes (never executes) evidence-request actions for the first steps of the fix plan; a
   human approves them through the two-person workflow;
5. writes every step to the audit chain as ``actor_kind = "ai"`` on behalf of the person who
   started it, and stores the whole run as a hashed snapshot.

With Gemini configured, the agent also writes a short narrative grounded only in the tool
observations (the prompt carries the observations, not the documents). Without it, the
narrative is the deterministic family explanation. Either way, the facts come from the tools.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import audit
from app.infrastructure.database.models import (
    Case,
    ComparableSale,
    CostEntry,
    EvidenceDocument,
    LandProceeding,
    LandRecord,
    Task,
    User,
    WorkflowAction,
)
from app.services import ai

from . import explain, lifecycle, service
from . import proceedings as procs
from .regions import PROCEEDING_KINDS, TERMINAL
from .valuation import inr

MAX_STEPS = 12
SYSTEM = (
    "You write a short case note for a rural legal-aid paralegal in India. Use ONLY the JSON observations given. "
    "Do not state who owns the land: succession shares are drafts for the revenue authority or a court. Always quote "
    "valuations as a range with the confidence. Name the office for each next step. 120 words at most."
)


def _count(db: Session, model: Any, *where: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def run(db: Session, case: Case, user: User, *, lender: str = "bank_crop_loan", loan_amount_inr: int | None = None, propose: bool = True) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    obs: dict[str, Any] = {}

    def step(tool: str, status: str, why: str, summary: str = "", analysis_id: str | None = None) -> None:
        n = len(steps) + 1
        steps.append({"n": n, "tool": tool, "status": status, "why": why, "summary": summary, "analysis_id": analysis_id})
        audit.record(
            db, organization_id=case.organization_id, event="land.agent.step", actor=user, actor_kind="ai", case_id=case.id,
            entity_type="case", entity_id=case.id, detail={"n": n, "tool": tool, "status": status, "summary": summary[:300]},
        )

    def handoff(who: str, what: str, where: str, why: str) -> None:
        if not any(h["what"] == what for h in handoffs):
            handoffs.append({"who": who, "what": what, "where": where, "why": why})

    parcel = service.parcel_of(db, case)
    reg = service.region_of(db, case)
    if parcel is None:
        step("inventory", "blocked", "no parcel on the case")
        handoff("paralegal", "Enter the plot: state, district, taluk, village and survey number", "Land tab → Parcel", "Every analysis is keyed on the survey number.")
        return _finish(db, case, user, steps, handoffs, obs, [], lender, loan_amount_inr)

    # 1 · read new documents
    extracted = _count(db, EvidenceDocument, EvidenceDocument.case_id == case.id, EvidenceDocument.status == "extracted")
    derived = _count(db, LandRecord, LandRecord.case_id == case.id, LandRecord.document_id.is_not(None))
    if extracted > derived:
        out = service.derive_records(db, case, user)
        step("derive_records", "ran", f"{extracted} readable document(s), {derived} already read", f"{out['created']} land record(s) and {out['comparables']} comparable sale(s) derived")
    records = service.records_of(db, case)
    if not records:
        step("concordance", "blocked", "no land records yet")
        handoff("family", f"Bring the {reg.ror}, {reg.cultivation}, {reg.sketch} and {reg.encumbrance}", f"{reg.village_official} / {reg.registration_office}", "The records must be compared before anything else.")
        return _finish(db, case, user, steps, handoffs, obs, [], lender, loan_amount_inr)

    # 2 · concordance → findings
    conc = service.run_concordance(db, case)
    snap = service.snapshot(db, case, "concordance", {}, conc, user, actor_kind="ai")
    obs["concordance"] = {"issues": [{"type": i["type"], "severity": i["severity"], "title": i["title"], "office": i["office"]} for i in conc["issues"]], "counts": conc["counts"]}
    step("concordance", "ran", f"{conc['records_compared']} record(s) of S.No. {conc['survey_key']} side by side", f"{len(conc['issues'])} disagreement(s): " + ", ".join(f"{k} {v}" for k, v in conc["counts"].items() if v), snap.id)
    from app.domain.risk import run_detection

    det = run_detection(db, case)
    step("detect_findings", "ran", "turn disagreements into reviewable findings", f"{det['created']} new, {det['updated']} updated, {det['closed']} closed")
    have = {r.source_type for r in records}
    for kind, label, where in (("ror", reg.ror, reg.village_official), ("sketch", reg.sketch, reg.survey_office), ("encumbrance", reg.encumbrance, reg.registration_office)):
        if kind not in have:
            handoff("family", f"Obtain the {label}", where, "Needed to compare the records and by every lender.")

    # 3 · succession
    members = service.members_of(db, case)
    succ: dict[str, Any] = {"shares": []}
    if not members:
        step("succession", "blocked", "no family tree")
        if conc["deceased_holders"]:
            handoff("family", f"Describe the family of {', '.join(conc['deceased_holders'])}: spouse(s), children, who has died and when", "Land tab → Family tree", "The record is in a deceased person's name; shares cannot be drafted without the tree.")
    else:
        succ = service.run_succession(db, case)
        snap = service.snapshot(db, case, "succession", {}, succ, user, actor_kind="ai")
        obs["succession"] = {"status": succ["status"], "law": succ.get("law_name"), "shares": [(s["name"], s["share"]) for s in succ["shares"] if s["alive"]], "unresolved": succ.get("unresolved", [])}
        step("succession", "ran" if succ["status"] != "not_computed" else "blocked", f"{len(members)} people in the tree", f"{succ['status']}: " + "; ".join(f"{s['name']} {s['share']}" for s in succ["shares"] if s["alive"]), snap.id)
        for u in succ.get("unresolved", []):
            handoff("advocate" if "Muslim" in u.get("reason", "") else "paralegal", f"Resolve {u['person']}'s share", "Land tab → Family tree", u["reason"])
        for s in succ["shares"]:
            if s["alive"] and s["consent_status"] not in ("consented",):
                handoff(
                    "paralegal", f"Get {s['name']}'s consent ({s['share']})", "send a consent link" if not s["lives_in_village"] else "visit in the village",
                    "Undivided land is pledged or mutated only with every co-owner's consent.",
                )

    # 4 · valuation
    comps = _count(db, ComparableSale, ComparableSale.organization_id == case.organization_id)
    val = service.run_valuation(db, case, lender=lender, conc=conc, succ=succ if members else None)
    if val.get("status") == "estimated":
        snap = service.snapshot(db, case, "valuation", {"lender": lender}, val, user, actor_kind="ai")
        obs["valuation"] = {k: val[k] for k in ("p10_inr", "p50_inr", "p90_inr", "lending_value_inr", "confidence", "effective_comparables", "title_haircut")}
        step("valuation", "ran", f"{comps} comparable sale(s) in the organisation; guideline value {'entered' if parcel.guideline_inr_per_sqm else 'missing'}", f"{inr(val['p10_inr'])}–{inr(val['p90_inr'])} ({val['confidence']} confidence), lending value {inr(val['lending_value_inr'])}", snap.id)
        if val["effective_comparables"] < 2:
            handoff("paralegal", "Record two or more recent sales near the plot", f"{reg.encumbrance} of nearby survey numbers or the {reg.registration_office}", "With fewer comparables the range stays wide.")
    else:
        step("valuation", "blocked", val.get("reason", "insufficient data"))
        handoff("paralegal", f"Enter the guideline value per square metre for S.No. {conc['survey_key']}", f"{reg.registration_office} guideline value register", val.get("reason", ""))

    # 5 · readiness and the fix plan
    ready = service.run_readiness(db, case, lender=lender, loan_amount_inr=loan_amount_inr)
    snap = service.snapshot(db, case, "readiness", {"lender": lender, "loan_amount_inr": loan_amount_inr}, ready, user, actor_kind="ai")
    obs["readiness"] = {"lender": ready["lender_label"], "score": ready["score"], "verdict": ready["verdict"], "plan": [(p["name"], p["office"], p["days"]) for p in ready["plan"]], "days_to_ready": ready["days_to_ready"], "notes": ready["notes"]}
    step("readiness", "ran", ready["lender_label"], f"score {ready['score']}/100, {ready['verdict'].replace('_', ' ')}; about {ready['days_to_ready']} days and {inr(ready['official_fees_inr'])} in official fees to fix", snap.id)
    for p in ready["plan"][:6]:
        handoff("office" if p["office"] != "family (notarised)" else "family", p["name"], p["office"], "Unblocks: " + ", ".join(p["unblocks"]) if p["unblocks"] else "Needed by a later step.")

    # 6 · office proceedings: draft what must be filed, watch what is filed
    files = lifecycle.proceedings_of(db, case)
    open_kinds = {p.kind for p, _ in files if p.status not in ("rejected", "withdrawn")}
    drafted: list[str] = []
    if conc["deceased_holders"]:
        applicants = [s["name"] for s in succ.get("shares", []) if s["alive"]]
        for kind in ("legal_heir_certificate", "mutation"):
            if kind in open_kinds or (kind == "legal_heir_certificate" and "legal_heir" in have):
                continue
            d = procs.defaults(kind, reg)
            db.add(LandProceeding(case_id=case.id, kind=kind, office=d["office"], sla_days=d["sla_days"], applicants=applicants,
                                  notes="Drafted by the land case agent — check the filing list, then file and record the acknowledgement.", created_by_id=user.id))
            drafted.append(d["label"])
        db.flush()
    signals: list[str] = []
    for p, ev in lifecycle.proceedings_of(db, case):
        if p.status in TERMINAL:
            continue
        h = procs.health(p, ev)
        label = PROCEEDING_KINDS[p.kind]["label"]
        signals.append(f"{label}: {h['signal'].replace('_', ' ')}")
        if h["signal"] == "not_filed":
            handoff("family", f"File the {label.lower()} application", p.office, "Drafted; the filing list under Proceedings shows what to carry.")
        elif h["signal"] in ("overdue", "stalled"):
            authority, basis = procs.next_authority(reg, p.escalation_level)
            why = f"{h.get('overdue_days')} days past the {p.sla_days}-day timeline" if h["signal"] == "overdue" else f"no movement for {h['last_event_days']} days"
            handoff("paralegal", f"Escalate the {label.lower()} ({why})", authority, f"A {basis} is drafted under Proceedings → Escalate.")
        if h.get("waiting_on") == "family" and h["signal"] != "not_filed":
            handoff("family", f"Submit the document the office asked for on the {label.lower()}", p.office, "The file is waiting on the family, not the office.")
        if h.get("objection"):
            handoff("advocate", f"Prepare for the hearing on the {label.lower()}", p.office, "An objection was filed; consider DLSA mediation first.")
    obs["proceedings"] = signals
    step("proceedings", "ran", f"{len(files)} file(s) with offices", ("drafted " + ", ".join(drafted) + "; " if drafted else "") + ("; ".join(signals) or "nothing filed"))
    for s in succ.get("shares", []):
        if s["alive"] and s["consent_status"] == "objected":
            handoff("paralegal", f"Talk to {s['name']}, who objected", "District Legal Services Authority (pre-litigation mediation)", "Mediation is free and keeps the family out of a partition suit.")
        elif s["alive"] and s.get("consent_evidence") == "online":
            handoff("family", f"Collect {s['name']}'s signed consent letter", "by post or in person", "Agreed online; offices and lenders need it on paper.")

    # 7 · family pathways
    if members and val.get("status") == "estimated":
        pw = service.run_pathways(db, case, lender=lender)
        if pw.get("status") == "compared":
            snap = service.snapshot(db, case, "pathways", {"lender": lender}, pw, user, actor_kind="ai")
            best = next((r for r in pw["routes"] if r.get("recommended")), None)
            bridge = next((r for r in pw["routes"] if r.get("bridge")), None)
            obs["pathways"] = [{"route": r["label"], "days": r["days"], "cost_inr": r["cost_inr"], "capacity_inr": r["capacity_inr"], "score": r.get("score")} for r in pw["routes"]]
            step(
                "pathways", "ran", f"{len(pw['routes'])} routes compared for the family",
                (f"highest score: {best['label']} — about {best['days']} days, {inr(best['cost_inr'])} in fees and duty, borrowing up to {inr(best['capacity_inr'])}" if best else "")
                + (f"; bridge: {inr(bridge['capacity_inr'])} crop loan in about {bridge['days']} days" if bridge else ""), snap.id,
            )
            handoff("family", "Choose a route together (joint, release, partition) — the options are compared under Pathways", "a family meeting", "Every route needs different signatures; agree before anyone signs.")

    # 8 · costs
    if _count(db, CostEntry, CostEntry.case_id == case.id):
        costs = service.run_costs(db, case, lender)
        obs["costs"] = {k: costs[k] for k in ("total_inr", "days", "unreceipted_inr", "excess_total_inr", "informal_premium_inr")}
        step("costs", "ran", "follow the money the family has spent", f"{inr(costs['total_inr'])} and {costs['days']} days so far; {inr(costs['unreceipted_inr'])} without receipts")

    # 9 · proposals for the critical path (a human approves)
    proposed: list[int] = []
    if propose:
        from app.domain.approvals import ApprovalError
        from app.domain.approvals import propose as propose_action

        existing = {t.title for t in db.scalars(select(Task).where(Task.case_id == case.id, Task.status.in_(("open", "in_progress"))))}
        existing |= {
            a.params.get("title", "") for a in db.scalars(select(WorkflowAction).where(WorkflowAction.case_id == case.id, WorkflowAction.status == "proposed", WorkflowAction.action == "evidence.request"))
        }
        for p in [p for p in ready["plan"] if p["critical"]][:3]:
            title = f"{p['name']} — {p['office']}"
            if title in existing:
                continue
            try:
                row = propose_action(
                    db, case, "evidence.request",
                    {"title": title, "description": f"Documents: {', '.join(p['documents']) or '—'}. Indicative fee ₹{p['fee_inr']}, about {p['days']} days. Proposed by the land case agent.", "assignee_id": ""},
                    actor=None, rationale="Critical path of the collateral-readiness plan", kind="system",
                )
                proposed.append(row.seq)
            except ApprovalError:
                continue
        if proposed:
            step("propose_actions", "ran", "first steps on the critical path", f"proposed action(s) {', '.join(map(str, proposed))} for approval")

    exp = explain.build("en", parcel=parcel, conc=conc, succ=succ, val=val, ready=ready)
    obs["explanation"] = exp["lines"]
    return _finish(db, case, user, steps, handoffs, obs, proposed, lender, loan_amount_inr)


def _finish(db: Session, case: Case, user: User, steps: list, handoffs: list, obs: dict, proposed: list, lender: str, amount: int | None) -> dict[str, Any]:
    narrative, mode = " ".join(obs.get("explanation", [])), "deterministic"
    if ai.enabled() and obs:
        try:
            narrative = ai.generate_text("Observations (JSON):\n" + json.dumps(obs, ensure_ascii=False, default=str)[:20000], system=SYSTEM)
            mode = f"gemini:{ai.model_name()}"
        except Exception:  # noqa: BLE001 — the deterministic narrative stands if the model is unavailable
            mode = "deterministic (model unavailable)"
    result = {
        "goal": "Prepare the land case for mutation and for a lender, leaving every decision to people and offices.",
        "mode": mode, "steps": steps, "handoffs": handoffs, "proposed_actions": proposed, "narrative": narrative, "observations": obs,
        "lender": lender, "loan_amount_inr": amount, "max_steps": MAX_STEPS,
    }
    snap = service.snapshot(db, case, "agent_run", {"lender": lender, "loan_amount_inr": amount}, result, user, actor_kind="ai")
    result["analysis_id"] = snap.id
    result["result_hash"] = snap.result_hash
    return result
