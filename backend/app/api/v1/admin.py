"""Internal platform-operations boundary (``is_platform_admin`` only; 404 for everyone else).

Holds what is genuinely operator-only: Magpie's original seven-source settlement
reconciliation, preserved unchanged, and the safety model (kill switch, action allowlist,
and the tests that prove each control) — Beacon's Safety page, retargeted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.domain.reconciliation.engine import SOURCE_NAMES
from app.domain.reconciliation.service import run_settlement_batch
from app.domain.workflows import catalog
from app.infrastructure.database.models import User
from app.infrastructure.database.session import get_db

from .deps import current_user, platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])

CONTROLS = [
    {"id": "allowlist", "title": "Only allowlisted actions", "rule": "Nothing outside the registry can be proposed or executed; params must match the schema exactly.", "file": "backend/app/domain/workflows/__init__.py", "test": "tests/unit/test_workflow_actions.py::test_unknown_action_and_extra_params_are_refused"},
    {"id": "phrase", "title": "Consent is an exact phrase", "rule": "An approver must type “approve action <n>”; the words are stored on the approval.", "file": "backend/app/domain/approvals/__init__.py", "test": "tests/unit/test_workflow_actions.py::test_approval_requires_the_exact_phrase"},
    {"id": "four-eyes", "title": "Proposer cannot approve", "rule": "With REQUIRE_SEPARATE_APPROVER on, the person who proposed an action cannot approve it.", "file": "backend/app/domain/approvals/__init__.py", "test": "tests/unit/test_workflow_actions.py::test_proposer_cannot_approve_their_own_action"},
    {"id": "once", "title": "One approval, one execution", "rule": "proposed → executing is a conditional update; a second approval finds nothing to claim.", "file": "backend/app/domain/approvals/__init__.py", "test": "tests/unit/test_workflow_actions.py::test_an_action_executes_exactly_once"},
    {"id": "kill", "title": "Kill switch", "rule": "WORKFLOW_EXECUTION_ENABLED=false stops every execution path.", "file": "backend/app/domain/approvals/__init__.py", "test": "tests/unit/test_workflow_actions.py::test_kill_switch_blocks_execution"},
    {"id": "grounding", "title": "AI facts must be on the page", "rule": "A model-proposed fact is kept only if its snippet is on the cited page and its value is in the snippet.", "file": "backend/app/services/extraction/__init__.py", "test": "tests/unit/test_extraction.py::test_grounding_gate_rejects_ungrounded_model_facts"},
    {"id": "tenancy", "title": "Tenant isolation", "rule": "Every case-owned record is read through the organisation scope; other tenants' ids return 404.", "file": "backend/app/repositories/__init__.py", "test": "tests/integration/test_permissions.py::test_other_organisation_cannot_read_or_write"},
    {"id": "chain", "title": "Tamper-evident history", "rule": "Audit events are hash-chained per organisation and verifiable end to end.", "file": "backend/app/domain/audit/__init__.py", "test": "tests/unit/test_audit_chain.py::test_editing_a_row_breaks_the_chain_at_that_row"},
    {"id": "recon", "title": "Reconciliation behaviour preserved", "rule": "The Python engine reproduces Magpie's TypeScript matcher result-for-result.", "file": "backend/app/domain/reconciliation/engine/match.py", "test": "tests/unit/test_recon_engine_golden.py::test_every_match_result_is_identical_and_in_order"},
]


@router.get("/safety")
def safety(_user: User = Depends(current_user)) -> dict:
    settings = get_settings()
    return {
        "execution_enabled": settings.workflow_execution_enabled,
        "separate_approver": settings.require_separate_approver,
        "action_ttl_days": settings.action_ttl_days,
        "allowlist": catalog(),
        "controls": CONTROLS,
        "never": ["file or record a legal document", "send anything to a court or county", "make a legal determination", "transfer money"],
    }


@router.post("/reconciliation/settlement-batch")
async def settlement_batch(
    files: list[UploadFile] = File(...),
    admin: User = Depends(platform_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    sources: dict[str, str | None] = {}
    for upload in files:
        name = (upload.filename or "").split("/")[-1].lower()
        if name in SOURCE_NAMES:
            sources[name] = (await upload.read(20 * 1024 * 1024)).decode("utf-8", errors="replace")
    outcome = run_settlement_batch(db, admin.organization_id, sources, admin)
    audit.record(db, organization_id=admin.organization_id, event="reconciliation.settlement_batch", actor=admin, entity_type="reconciliation_run", entity_id=outcome["run_id"], detail={"files": sorted(sources), "byOutcome": outcome["stats"]["byOutcome"]})
    return outcome
