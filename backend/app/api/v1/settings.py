"""Organisation settings: members and roles, and the honest status of every integration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import ROLES, hash_password
from app.domain import audit
from app.infrastructure import storage
from app.infrastructure.database.models import User
from app.infrastructure.database.session import engine, get_db
from app.repositories import get_org_user
from app.services import ai

from . import serializers as s
from .auth import USERNAME
from .deps import current_user, require

router = APIRouter(prefix="/settings", tags=["settings"])


class NewUser(BaseModel):
    username: str
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: str = "paralegal"


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    full_name: str | None = None


@router.get("/users")
def users(user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> dict:
    rows = db.scalars(select(User).where(User.organization_id == user.organization_id).order_by(User.full_name))
    return {"users": [s.user(u) for u in rows], "roles": list(ROLES)}


@router.post("/users", status_code=201)
def add_user(body: NewUser, admin: User = Depends(require("users:manage")), db: Session = Depends(get_db, scope="function")) -> dict:
    if body.role not in ROLES:
        raise HTTPException(422, detail=f"role must be one of {', '.join(ROLES)}")
    if not USERNAME.match(body.username):
        raise HTTPException(422, detail="username: 3–64 letters, digits, dot, dash or underscore")
    if db.scalar(select(User).where((User.username == body.username.lower()) | (User.email == body.email.lower()))):
        raise HTTPException(409, detail="That username or email is already registered")
    member = User(
        organization_id=admin.organization_id,
        username=body.username.lower(),
        email=body.email.lower(),
        full_name=body.full_name.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(member)
    db.flush()
    audit.record(db, organization_id=admin.organization_id, event="user.added", actor=admin, entity_type="user", entity_id=member.id, detail={"username": member.username, "role": member.role})
    return {"user": s.user(member)}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, admin: User = Depends(require("users:manage")), db: Session = Depends(get_db, scope="function")) -> dict:
    member = get_org_user(db, admin, user_id)
    changes: dict = {}
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, detail=f"role must be one of {', '.join(ROLES)}")
        changes["role"] = (member.role, body.role)
        member.role = body.role
    if body.is_active is not None:
        changes["is_active"] = (member.is_active, body.is_active)
        member.is_active = body.is_active
    if body.full_name:
        member.full_name = body.full_name.strip()
    db.flush()
    admins = db.scalar(select(func.count()).select_from(User).where(User.organization_id == admin.organization_id, User.role == "admin", User.is_active.is_(True)))
    if not admins:
        raise HTTPException(409, detail="An organisation must keep at least one active admin")
    audit.record(db, organization_id=admin.organization_id, event="user.updated", actor=admin, entity_type="user", entity_id=member.id, detail={k: list(v) for k, v in changes.items()})
    return {"user": s.user(member)}


@router.get("/integrations")
def integrations(_user: User = Depends(current_user)) -> dict:
    """What each integration is actually running on — configured, local, or off."""
    settings = get_settings()
    dialect = engine().dialect.name
    return {
        "database": {"dialect": dialect, "mode": "cloud-sql/postgresql" if dialect == "postgresql" else "local sqlite"},
        "storage": storage.status(),
        "extraction": {
            "backend": settings.extraction_backend,
            "mode": "Document AI" if settings.extraction_backend == "documentai" else "local text layer (pypdf / plain text); no OCR",
            "processor": settings.documentai_processor_id or None,
        },
        "ai": ai.status() | {"mode": "Gemini on Vertex AI" if ai.enabled() else "off — answers are extractive, no model output"},
        "pii": {"backend": settings.pii_backend, "mode": "Sensitive Data Protection" if settings.pii_backend == "dlp" else "local high-precision patterns"},
        "notifications": {"webhook": bool(settings.notify_webhook_url)},
        "workflow": {"execution_enabled": settings.workflow_execution_enabled, "separate_approver": settings.require_separate_approver},
        "gcp_project": settings.gcp_project or None,
    }
