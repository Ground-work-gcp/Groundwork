"""``/api/v1`` — the single versioned API surface."""

from fastapi import APIRouter

from . import (
    admin,
    approvals,
    assistant,
    audit,
    auth,
    cases,
    evidence,
    financials,
    flow,
    land,
    ops,
    ownership,
    properties,
    public,
    reconciliation,
    risks,
    settings,
    workflows,
)

router = APIRouter()
for module in (auth, cases, properties, evidence, ownership, financials, reconciliation, risks, workflows, approvals, audit, settings, admin, flow, assistant, ops, land, public):
    router.include_router(module.router)
