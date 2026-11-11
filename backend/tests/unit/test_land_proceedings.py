"""Proceedings as a state machine with a clock; stamp-duty rules; escalation chain."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.domain.land import proceedings as P
from app.domain.land.regions import duty, region

TN, MH = region("TN"), region("MH")
TODAY = date(2026, 11, 11)


def proc(kind="mutation", sla=30, status="draft"):
    return SimpleNamespace(kind=kind, status=status, filed_on=None, due_on=None, objection_until=None, hearing_on=None, decided_on=None, sla_days=sla,
                           escalation_level=0, office="Taluk office (Tahsildar)", application_no="M/1")


def ev(kind, d):
    return SimpleNamespace(kind=kind, event_date=d, note="")


def iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def test_events_drive_the_flow_forward_never_back():
    p = proc()
    assert P.apply_event(p, "filed", iso(20), TN) == "filed" and p.due_on == iso(-10)
    assert P.apply_event(p, "notice_published", iso(10), TN) == "notice_period" and p.objection_until == iso(-5)
    assert P.apply_event(p, "acknowledged", iso(9), TN) == "notice_period"  # a late acknowledgement does not move it back
    assert P.apply_event(p, "order_passed", iso(1), TN) == "ordered" and p.decided_on == iso(1)
    with pytest.raises(P.ProceedingError):
        P.apply_event(p, "field_inspection", iso(0), TN)


def test_a_draft_must_be_filed_first_and_events_cannot_predate_filing():
    p = proc()
    with pytest.raises(P.ProceedingError):
        P.apply_event(p, "field_inspection", iso(3), TN)
    P.apply_event(p, "filed", iso(5), TN)
    with pytest.raises(P.ProceedingError):
        P.apply_event(p, "order_passed", iso(9), TN)


def test_health_signals():
    p = proc(sla=30)
    P.apply_event(p, "filed", iso(40), TN)
    h = P.health(p, [ev("filed", iso(40))], TODAY)
    assert h["signal"] == "overdue" and h["overdue_days"] == 10
    p2 = proc(sla=60)
    P.apply_event(p2, "filed", iso(30), TN)
    assert P.health(p2, [ev("filed", iso(30))], TODAY)["signal"] == "stalled"  # 30 days with no movement
    p3 = proc(sla=60)
    P.apply_event(p3, "filed", iso(50), TN)
    assert P.health(p3, [ev("filed", iso(50)), ev("follow_up", iso(5))], TODAY)["signal"] == "at_risk"
    p4 = proc(sla=30)
    P.apply_event(p4, "filed", iso(40), TN)
    waiting = P.health(p4, [ev("filed", iso(40)), ev("document_requested", iso(20))], TODAY)
    assert waiting["waiting_on"] == "family" and waiting["signal"] != "overdue"  # the delay is on the family's side
    p5 = proc(kind="encumbrance_certificate", sla=7)
    P.apply_event(p5, "filed", iso(12), TN)
    P.apply_event(p5, "order_passed", iso(9), TN)
    done = P.health(p5, [], TODAY)
    assert done["signal"] == "done" and done["elapsed_days"] == 3 and done["days_left"] is None


def test_escalation_chain_cites_the_right_law():
    assert P.next_authority(MH, 0) == ("Sub-Divisional Officer (SDO)", "appeal under the Maharashtra Right to Public Services Act, 2015")
    assert P.next_authority(TN, 1)[0] == "District Collector" and P.next_authority(TN, 5)[0].startswith("CM Helpline")
    p = proc()
    P.apply_event(p, "filed", iso(45), TN)
    letter = P.escalation_letter(p, [ev("filed", iso(45))], TN, case_ref="GW-1", survey="123/4A", village="Keezhaiyur", applicants=["Kannan"], today=TODAY)
    assert "Revenue Divisional Officer" in letter["letter"] and "45 days" in letter["letter"] and "M/1" in letter["letter"]


def test_indicative_duty_rules():
    assert duty("TN", "release", 2_000_000) == 20_000 and duty("TN", "release", 9_000_000) == 40_000  # 1%, capped
    assert duty("MH", "release", 9_000_000) == 200  # flat
    assert duty("KA", "partition", 10) == 1_000  # floor
