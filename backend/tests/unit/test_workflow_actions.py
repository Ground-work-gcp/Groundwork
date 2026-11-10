"""The approval rules, exercised directly against the domain layer."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.domain.approvals import ApprovalError, approve, propose
from app.domain.cases import create_case
from app.domain.workflows import ParamError, validate_params
from app.infrastructure.database.models import Organization, Task, User
from tests.helpers import unique


def _people(db):
    org = Organization(name=unique("Org "))
    db.add(org)
    db.flush()

    def person(role):
        user = User(organization_id=org.id, username=unique(role), email=f"{unique(role)}@example.org", full_name=role.title(), password_hash=hash_password("x" * 12), role=role)
        db.add(user)
        db.flush()
        return user

    return org, person("paralegal"), person("attorney"), person("attorney")


def _request(db, case, actor):
    return propose(db, case, "evidence.request", {"title": "Obtain probate order", "description": "for Ernest L. Parker", "assignee_id": ""}, actor=actor)


def test_unknown_action_and_extra_params_are_refused():
    with pytest.raises(ParamError):
        validate_params("case.delete_everything", {})
    with pytest.raises(ParamError):
        validate_params("case.close_unresolved", {"reason": "x", "force": True})
    with pytest.raises(ParamError):
        validate_params("case.close_unresolved", {"reason": ""})


def test_approval_requires_the_exact_phrase(db):
    _org, paralegal, attorney, _ = _people(db)
    case = create_case(db, paralegal, title="Phrase case")
    action = _request(db, case, paralegal)
    with pytest.raises(ApprovalError) as error:
        approve(db, case, action, attorney, "yes, approve it")
    assert error.value.status == 422 and action.status == "proposed"
    approve(db, case, action, attorney, f"  Approve   Action {action.seq} ")
    assert action.status == "executed"
    assert db.scalar(select(Task).where(Task.case_id == case.id)).kind == "evidence_request"


def test_proposer_cannot_approve_their_own_action(db):
    _org, _paralegal, attorney, _ = _people(db)
    case = create_case(db, attorney, title="Four eyes")
    action = _request(db, case, attorney)
    with pytest.raises(ApprovalError) as error:
        approve(db, case, action, attorney, f"approve action {action.seq}")
    assert error.value.status == 403


def test_an_action_executes_exactly_once(db):
    _org, paralegal, attorney, second = _people(db)
    case = create_case(db, paralegal, title="Once")
    action = _request(db, case, paralegal)
    approve(db, case, action, attorney, f"approve action {action.seq}")
    with pytest.raises(ApprovalError):
        approve(db, case, action, second, f"approve action {action.seq}")
    assert len(db.scalars(select(Task).where(Task.case_id == case.id)).all()) == 1


def test_role_must_be_an_approver_for_the_action(db):
    _org, paralegal, attorney, _ = _people(db)
    case = create_case(db, attorney, title="Roles")
    action = propose(db, case, "case.close_unresolved", {"reason": "family withdrew"}, actor=attorney)
    with pytest.raises(ApprovalError) as error:
        approve(db, case, action, paralegal, f"approve action {action.seq}")
    assert error.value.status == 403


def test_kill_switch_blocks_execution(db, monkeypatch):
    _org, paralegal, attorney, _ = _people(db)
    case = create_case(db, paralegal, title="Kill switch")
    action = _request(db, case, paralegal)
    monkeypatch.setattr(get_settings(), "workflow_execution_enabled", False)
    with pytest.raises(ApprovalError) as error:
        approve(db, case, action, attorney, f"approve action {action.seq}")
    assert error.value.status == 423 and action.status == "proposed"


def test_resolution_cannot_be_proposed_before_the_case_is_ready(db):
    _org, paralegal, attorney, _ = _people(db)
    case = create_case(db, paralegal, title="Too early")
    with pytest.raises(ApprovalError):
        propose(db, case, "case.resolve", {"resolution": "done"}, actor=attorney)
