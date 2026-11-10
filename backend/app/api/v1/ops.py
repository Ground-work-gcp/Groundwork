"""Operational endpoints: provider/webhook/pool metrics for admins, and the scheduled-job hook.

``POST /ops/jobs/{name}`` is called by Cloud Scheduler with an OIDC token for the scheduler
service account (verified by Cloud Run IAM when the service requires authentication) *and* an
HMAC signature over the body with ``WEBHOOK_SIGNING_SECRET`` — so it stays protected even on a
publicly reachable service. Prefer the Cloud Run Job (``python -m app.jobs``), which needs no
HTTP surface at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app import jobs
from app.core.config import get_settings
from app.core.resilience import PROVIDER_STATS
from app.infrastructure.database.models import User
from app.infrastructure.database.session import pool_status
from app.infrastructure.external_integrations import DELIVERY_STATS, verify_signature

from .deps import require

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/metrics")
def metrics(_admin: User = Depends(require("users:manage"))) -> dict:
    return {"providers": PROVIDER_STATS.snapshot(), "webhooks": dict(DELIVERY_STATS), "db_pool": pool_status()}


@router.post("/jobs/{name}")
async def run_job(name: str, request: Request) -> dict:
    secret = get_settings().webhook_signing_secret
    body = await request.body()
    if not secret or not verify_signature(body, request.headers.get("x-groundwork-signature"), secret):
        raise HTTPException(401, detail="Invalid or missing signature")
    if name not in jobs.JOBS:
        raise HTTPException(404, detail="Unknown job")
    return jobs.run(name)
