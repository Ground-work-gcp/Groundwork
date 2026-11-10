"""Scheduled operations, run as a Cloud Run Job (``python -m app.jobs <name>``) on a Cloud
Scheduler cron — never by a long-lived local process. The same functions back
``POST /api/v1/ops/jobs/{name}`` for operators. Each job is idempotent: running it twice in a
row changes nothing the second time.

  expire-actions  move proposals past their expiry to ``expired`` and record it in the audit chain
  verify-audit    re-verify every organisation's hash chain; exit non-zero if any is broken
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure, event
from app.domain import audit
from app.domain.audit import verify_chain
from app.infrastructure.database.models import Case, Organization, WorkflowAction, utcnow
from app.infrastructure.database.session import session_factory

logger = logging.getLogger("groundwork.jobs")


def expire_actions(db: Session) -> dict[str, Any]:
    now = utcnow()
    expired = 0
    rows = db.execute(
        select(WorkflowAction, Case.organization_id).join(Case, Case.id == WorkflowAction.case_id).where(WorkflowAction.status == "proposed")
    ).all()
    for row, org_id in rows:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=now.tzinfo)
        if expires > now:
            continue
        row.status = "expired"
        row.decided_at = now
        audit.record(
            db, organization_id=org_id, event="action.expired", actor=None, actor_kind="system", case_id=row.case_id,
            entity_type="workflow_action", entity_id=row.id, detail={"seq": row.seq, "action": row.action},
        )
        expired += 1
    return {"job": "expire-actions", "checked": len(rows), "expired": expired}


def verify_audit(db: Session) -> dict[str, Any]:
    results = {}
    for org in db.scalars(select(Organization)):
        results[org.id] = verify_chain(db, org.id)
    broken = [k for k, v in results.items() if not v.get("ok")]
    return {"job": "verify-audit", "organizations": len(results), "broken": broken, "ok": not broken}


JOBS = {"expire-actions": expire_actions, "verify-audit": verify_audit}


def run(name: str) -> dict[str, Any]:
    if name not in JOBS:
        raise KeyError(name)
    db = session_factory()()
    try:
        result = JOBS[name](db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    event(logger, "job finished", **{k: v for k, v in result.items() if k != "broken"}, broken=len(result.get("broken", [])))
    return result


def main(argv: list[str]) -> int:
    settings = get_settings()
    configure(json_logs=settings.json_logs, project=settings.gcp_project, level=settings.log_level)
    if len(argv) != 2 or argv[1] not in JOBS:
        print(f"usage: python -m app.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    try:
        result = run(argv[1])
    except Exception as exc:
        event(logger, "job failed", logging.ERROR, job=argv[1], error_type=type(exc).__name__)
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
