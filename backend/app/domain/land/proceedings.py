"""Revenue proceedings: what happens to an application after the family hands it in.

In the field the hard part is not knowing *what* to apply for — it is that a mutation application
sits on a desk. The family visits the taluk office again and again, pays someone to "follow up",
and has no idea whether the file is with the VAO for field verification, in its objection window,
or simply stalled. This module makes the file's life visible and actionable:

* **Events drive status.** Each dated office event (acknowledged, field inspection, notice
  published, objection received, hearing, order) moves the proceeding through that kind's flow
  (``regions.PROCEEDING_KINDS``). Nothing is ticked by hand.
* **Health against the timeline.** Days elapsed against the region's indicative service timeline,
  the objection window, who the file is waiting on (the office, the family, the notice period), and
  a signal: on track · at risk · overdue · stalled · done.
* **Escalation, drafted.** An overdue or stalled file gets a ready representation to the next
  authority — the appellate revenue officer (citing the state's right-to-services law where there
  is one), then the Collector, then the state grievance portal — with the dates and events that
  justify it. Groundwork drafts; the family signs and submits.
* **Filing readiness.** Before filing, the documents the office will ask for are checked against
  the case, so the application is not returned for a missing paper.
* **Objections route to mediation.** An objection from a co-heir proposes pre-litigation mediation
  through the District Legal Services Authority (free legal aid) before anyone thinks of a suit.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .regions import PROCEEDING_KINDS, STATUS_LABELS, TERMINAL, Region, office_for, process

EVENT_KINDS = (
    "filed", "acknowledged", "field_inspection", "notice_published", "objection_received", "hearing_scheduled", "hearing_held",
    "document_requested", "document_submitted", "follow_up", "order_passed", "rejected", "escalated", "withdrawn", "note",
)
EVENT_STATUS = {
    "filed": "filed", "acknowledged": "filed", "field_inspection": "field_verification", "notice_published": "notice_period",
    "objection_received": "hearing", "hearing_scheduled": "hearing", "hearing_held": "hearing", "order_passed": "ordered",
    "rejected": "rejected", "withdrawn": "withdrawn",
}
STALL_DAYS = 21


class ProceedingError(ValueError):
    pass


def _d(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def apply_event(p: Any, kind: str, event_date: str, region: Region, *, hearing_on: str | None = None) -> str:
    """Move a proceeding for one dated event; returns the status after it. Raises on an impossible event."""
    if kind not in EVENT_KINDS:
        raise ProceedingError(f"event must be one of {', '.join(EVENT_KINDS)}")
    if p.status in TERMINAL and kind not in ("note", "escalated"):
        raise ProceedingError(f"the proceeding is already {p.status}; add a note or file an appeal instead")
    if p.status == "draft" and kind not in ("filed", "acknowledged", "note", "withdrawn"):
        raise ProceedingError("file the application first (event: filed)")
    when = _d(event_date)
    if when is None:
        raise ProceedingError("event_date is not a date")
    if p.filed_on and when < _d(p.filed_on) and kind != "note":  # type: ignore[operator]
        raise ProceedingError("an event cannot be dated before the application was filed")
    flow = PROCEEDING_KINDS[p.kind]["flow"]
    new = EVENT_STATUS.get(kind)
    if kind in ("filed", "acknowledged") and not p.filed_on:
        p.filed_on = when.isoformat()
        p.due_on = (when + timedelta(days=p.sla_days)).isoformat()
    if new == "notice_period":
        p.objection_until = (when + timedelta(days=process(region.code)["objection_days"])).isoformat()
    if kind == "hearing_scheduled" and hearing_on:
        p.hearing_on = hearing_on
    if new in ("ordered", "rejected", "withdrawn"):
        p.decided_on = when.isoformat()
    if new is None or (new not in flow and new not in TERMINAL):
        return p.status  # informational for this kind (e.g. a hearing on an EC request)
    if new in TERMINAL or p.status == "draft" or flow.index(new) >= flow.index(p.status):
        p.status = new  # files move forward; a late acknowledgement never moves one back
    return p.status


def health(p: Any, events: list[Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    flow = PROCEEDING_KINDS[p.kind]["flow"]
    filed, due = _d(p.filed_on), _d(p.due_on)
    last = max((_d(e.event_date) for e in events if _d(e.event_date)), default=filed)
    decided = _d(p.decided_on)
    # A decided file stops its clock on the decision date.
    elapsed = ((decided if p.status in TERMINAL and decided else today) - filed).days if filed else 0
    out: dict[str, Any] = {
        "status_label": STATUS_LABELS.get(p.status, p.status),
        "flow": [{"status": s, "label": STATUS_LABELS[s], "reached": p.status in flow and flow.index(s) <= flow.index(p.status)} for s in flow],
        "elapsed_days": elapsed, "sla_days": p.sla_days, "due_on": p.due_on,
        "days_left": (due - today).days if due and p.status not in TERMINAL else None,
        "last_event_days": (today - last).days if last else None,
        "progress": round(flow.index(p.status) / (len(flow) - 1), 2) if p.status in flow else (1.0 if p.status == "ordered" else 0.0),
    }
    if p.status in TERMINAL:
        out.update(signal="done" if p.status == "ordered" else p.status, waiting_on="nobody")
        return out
    if p.status == "draft":
        out.update(signal="not_filed", waiting_on="family")
        return out
    requested = [e for e in events if e.kind == "document_requested"]
    submitted = [e for e in events if e.kind == "document_submitted"]
    waiting = "office"
    if requested and (not submitted or max(e.event_date for e in submitted) < max(e.event_date for e in requested)):
        waiting = "family"
    elif p.status == "notice_period" and p.objection_until and today <= _d(p.objection_until):  # type: ignore[operator]
        waiting = "objection window"
    elif p.status == "hearing" and p.hearing_on and today <= _d(p.hearing_on):  # type: ignore[operator]
        waiting = "hearing date"
    out["waiting_on"] = waiting
    if p.objection_until:
        out["objection_days_left"] = (_d(p.objection_until) - today).days  # type: ignore[operator]
    if any(e.kind == "objection_received" for e in events):
        out["objection"] = True
    if due and today > due and waiting != "family":
        out["signal"] = "overdue"
        out["overdue_days"] = (today - due).days
    elif out["last_event_days"] is not None and out["last_event_days"] >= STALL_DAYS and waiting == "office":
        out["signal"] = "stalled"
    elif p.sla_days and elapsed >= 0.75 * p.sla_days and out["progress"] < 0.75:
        out["signal"] = "at_risk"
    else:
        out["signal"] = "on_track"
    return out


def next_authority(region: Region, level: int) -> tuple[str, str]:
    """(authority, basis) for escalation ``level`` (0 = first escalation above the deciding office)."""
    proc = process(region.code)
    chain = [
        (proc["appellate_office"], f"appeal under the {proc['rts_law']}" if proc["rts_law"] else "representation to the supervising officer"),
        (proc["collector"], "representation to the District Collector"),
        (proc["grievance_portal"], "grievance on the state public-grievance system"),
    ]
    return chain[min(level, len(chain) - 1)]


def escalation_letter(
    p: Any, events: list[Any], region: Region, *, case_ref: str, survey: str, village: str, applicants: list[str], today: date | None = None
) -> dict[str, Any]:
    today = today or date.today()
    h = health(p, events, today)
    authority, basis = next_authority(region, p.escalation_level)
    label = PROCEEDING_KINDS[p.kind]["label"]
    history = [f"- {e.event_date}: {e.kind.replace('_', ' ')}{' — ' + e.note if e.note else ''}" for e in sorted(events, key=lambda e: e.event_date)]
    lines = [
        f"# Representation — delay in {label.lower()}",
        "",
        f"*Draft prepared by Groundwork for case {case_ref}. The applicants review, sign and submit it; Groundwork does not file anything.*",
        "",
        f"**To:** The {authority}",
        f"**From:** {', '.join(applicants) or 'the applicants'}",
        f"**Subject:** {label} for survey number {survey}, {village} village — application {p.application_no or '(number awaited)'} "
        f"pending {h['elapsed_days']} days{' (' + str(h.get('overdue_days')) + ' days beyond the service timeline)' if h.get('overdue_days') else ''}",
        "",
        "Respected Sir / Madam,",
        "",
        f"We applied to the {p.office} on {p.filed_on} for {label.lower()} of the above land. The indicative service timeline for this "
        f"service is {p.sla_days} days, i.e. by {p.due_on}. As of {today.isoformat()} the application is "
        f"“{h['status_label'].lower()}”, and the last action we know of was {h['last_event_days']} days ago.",
        "",
        "The record of the application so far:",
        "",
        *(history or ["- (no events recorded)"]),
        "",
        f"We request that the application be disposed of without further delay, or that we be told in writing what is pending from us. "
        f"This is a {basis}.",
        "",
        "Enclosures: acknowledgement of the application; copies of the documents submitted.",
        "",
        "Signatures: ____________________     Date: ____________",
    ]
    return {"authority": authority, "basis": basis, "level": p.escalation_level + 1, "letter": "\n".join(lines) + "\n", "health": h}


def filing_check(kind: str, *, records: list[Any], conc: dict[str, Any], succ: dict[str, Any], proceedings: list[Any]) -> list[dict[str, Any]]:
    """What the office will ask for with this application, and whether the case has it."""
    have = {r.source_type for r in records}
    done = {(x.kind, x.status) for x in proceedings}
    living = [s for s in succ.get("shares", []) if s.get("alive")]
    items: list[tuple[str, bool, str]] = []
    if kind == "legal_heir_certificate":
        items = [
            ("Death certificate of the deceased", "death_certificate" in have, "local body"),
            ("Family tree with every heir", bool(succ.get("shares")), "Family & shares tab"),
            ("Identity proof of each heir", False, "collect from the family"),
        ]
    elif kind in ("mutation", "name_correction"):
        heir_cert = "legal_heir" in have or ("legal_heir_certificate", "ordered") in done
        signed = [s for s in living if s.get("consent_evidence") in ("signed_letter", "registered_deed")]
        items = [
            ("Current record of rights extract", "ror" in have, "village official / online portal"),
            ("Legal heir certificate", heir_cert or not conc.get("deceased_holders"), "revenue office"),
            ("Death certificate", "death_certificate" in have or not conc.get("deceased_holders"), "local body"),
            (f"Signed consent of every co-heir ({len(signed)} of {len(living)})", bool(living) and len(signed) == len(living), "Family & shares tab"),
            ("Encumbrance certificate", "encumbrance" in have, "Sub-Registrar"),
        ]
    elif kind == "survey_measurement":
        items = [("Survey sketch copy", "sketch" in have, "survey office"), ("Names of neighbouring holders for notice", bool(conc.get("boundary_edges")), "Records tab")]
    elif kind == "encumbrance_certificate":
        items = [("Survey number and village", bool(conc.get("survey_key")), "Records tab")]
    elif kind == "dispute_mediation":
        items = [("Names and addresses of the parties", bool(living), "Family & shares tab"), ("Draft shares and how they were worked out", bool(succ.get("steps")), "Family & shares tab")]
    return [{"item": i, "ok": ok, "where": w} for i, ok, w in items]


def defaults(kind: str, region: Region) -> dict[str, Any]:
    if kind not in PROCEEDING_KINDS:
        raise ProceedingError(f"kind must be one of {', '.join(PROCEEDING_KINDS)}")
    return {"office": office_for(region, kind), "sla_days": process(region.code)["sla"][kind], "label": PROCEEDING_KINDS[kind]["label"]}
