"""The verified case history: an append-only, hash-chained audit log.

Beacon's consent-certificate idea, generalised: every meaningful action leaves a record,
and each record carries ``sha256(prev_hash + canonical(record))`` so a deleted or edited
row breaks the chain at a point ``verify_chain`` can name.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.database.models import AuditEvent, Organization, User, utcnow

GENESIS = "0" * 64


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _payload(event: AuditEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "organization_id": event.organization_id,
        "case_id": event.case_id,
        "actor_id": event.actor_id,
        "actor_kind": event.actor_kind,
        "event": event.event,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "detail": event.detail,
        "occurred_at": event.occurred_at,
    }


def _digest(prev_hash: str, event: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + _canonical(_payload(event))).encode("utf-8")).hexdigest()


def record(
    db: Session,
    *,
    organization_id: str,
    event: str,
    actor: User | None = None,
    actor_kind: str | None = None,
    case_id: str | None = None,
    entity_type: str = "",
    entity_id: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    # Lock the organisation row so concurrent writers serialise on the chain head (Postgres).
    org = db.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    if org is None:
        raise ValueError("unknown organization")
    row = AuditEvent(
        organization_id=organization_id,
        seq=org.audit_seq + 1,
        case_id=case_id,
        actor_id=actor.id if actor else None,
        actor_kind=actor_kind or ("user" if actor else "system"),
        event=event,
        entity_type=entity_type,
        entity_id=entity_id or "",
        detail=json.loads(_canonical(detail or {})),
        occurred_at=utcnow().isoformat(),
        prev_hash=org.audit_head,
    )
    row.hash = _digest(row.prev_hash, row)
    org.audit_seq = row.seq
    org.audit_head = row.hash
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session, organization_id: str) -> dict[str, Any]:
    """Recompute every link. Reports the first sequence number where the chain breaks."""
    org = db.get(Organization, organization_id)
    rows = list(
        db.scalars(select(AuditEvent).where(AuditEvent.organization_id == organization_id).order_by(AuditEvent.seq))
    )
    prev = GENESIS
    for expected_seq, row in enumerate(rows, start=1):
        if row.seq != expected_seq:
            return {"ok": False, "count": len(rows), "broken_at": expected_seq, "reason": "missing record"}
        if row.prev_hash != prev or _digest(prev, row) != row.hash:
            return {"ok": False, "count": len(rows), "broken_at": row.seq, "reason": "hash mismatch"}
        prev = row.hash
    if org is not None and org.audit_seq != len(rows):
        return {"ok": False, "count": len(rows), "broken_at": len(rows) + 1, "reason": "head does not match"}
    return {"ok": True, "count": len(rows), "head": prev}


def to_csv(rows: list[AuditEvent], names: dict[str, str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["seq", "occurred_at", "event", "actor", "actor_kind", "case_id", "entity_type", "entity_id", "detail", "hash"])
    for row in rows:
        writer.writerow(
            [
                row.seq,
                row.occurred_at,
                row.event,
                names.get(row.actor_id or "", row.actor_id or ""),
                row.actor_kind,
                row.case_id or "",
                row.entity_type,
                row.entity_id,
                _canonical(row.detail),
                row.hash,
            ]
        )
    return out.getvalue()
