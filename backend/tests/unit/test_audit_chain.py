"""The audit chain is tamper-evident."""

from __future__ import annotations

from sqlalchemy import select

from app.domain import audit
from app.infrastructure.database.models import AuditEvent, Organization
from tests.helpers import unique


def _org(db) -> Organization:
    org = Organization(name=unique("Chain "))
    db.add(org)
    db.flush()
    for n in range(5):
        audit.record(db, organization_id=org.id, event="test.event", detail={"n": n})
    db.flush()
    return org


def test_a_fresh_chain_verifies(db):
    org = _org(db)
    result = audit.verify_chain(db, org.id)
    assert result == {"ok": True, "count": 5, "head": org.audit_head}


def test_editing_a_row_breaks_the_chain_at_that_row(db):
    org = _org(db)
    third = db.scalar(select(AuditEvent).where(AuditEvent.organization_id == org.id, AuditEvent.seq == 3))
    third.detail = {"n": 999}
    db.flush()
    result = audit.verify_chain(db, org.id)
    assert result["ok"] is False and result["broken_at"] == 3


def test_deleting_a_row_is_detected(db):
    org = _org(db)
    db.delete(db.scalar(select(AuditEvent).where(AuditEvent.organization_id == org.id, AuditEvent.seq == 2)))
    db.flush()
    result = audit.verify_chain(db, org.id)
    assert result["ok"] is False and result["broken_at"] == 2
