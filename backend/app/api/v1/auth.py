"""Authentication (InHeir's sign-up/sign-in/sign-out and username check, unified)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_token, hash_password, verify_password
from app.domain import audit
from app.infrastructure.database.models import Organization, User, utcnow
from app.infrastructure.database.session import get_db

from . import serializers as s
from .deps import credential_limiter, current_user

router = APIRouter(prefix="/auth", tags=["auth"])
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


class SignUp(BaseModel):
    organization: str = Field(min_length=2, max_length=200)
    username: str
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        if not USERNAME.match(value):
            raise ValueError("3–64 letters, digits, dot, dash or underscore")
        return value.lower()


class SignIn(BaseModel):
    username: str
    password: str


def _set_session(response: Response, user: User) -> None:
    settings = get_settings()
    token, expires = create_token(user.id)
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


@router.get("/username/{username}/available")
def username_available(username: str, db: Session = Depends(get_db, scope="function")) -> dict:
    taken = db.scalar(select(func.count()).select_from(User).where(User.username == username.lower()))
    return {"available": bool(USERNAME.match(username)) and not taken}


@router.post("/sign-up", status_code=201)
def sign_up(body: SignUp, request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> dict:
    credential_limiter.check(request)
    if db.scalar(select(User).where((User.username == body.username) | (User.email == body.email.lower()))):
        raise HTTPException(status_code=409, detail="That username or email is already registered")
    org = Organization(name=body.organization.strip())
    db.add(org)
    db.flush()
    # The first user of a new organisation administers it.
    user = User(
        organization_id=org.id,
        username=body.username,
        email=body.email.lower(),
        full_name=body.full_name.strip(),
        password_hash=hash_password(body.password),
        role="admin",
        last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()
    audit.record(db, organization_id=org.id, event="organization.created", actor=user, entity_type="organization", entity_id=org.id, detail={"name": org.name})
    _set_session(response, user)
    db.refresh(user)
    return {"user": s.me(user)}


@router.post("/sign-in")
def sign_in(body: SignIn, request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> dict:
    credential_limiter.check(request)
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    # Same message either way: the endpoint must not reveal which usernames exist.
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Username or password is incorrect")
    user.last_login_at = utcnow()
    audit.record(db, organization_id=user.organization_id, event="user.signed_in", actor=user, entity_type="user", entity_id=user.id)
    _set_session(response, user)
    return {"user": s.me(user)}


@router.post("/token")
def token(body: SignIn, request: Request, db: Session = Depends(get_db, scope="function")) -> dict:
    """Bearer token for API clients and scripts (same credentials, no cookie)."""
    credential_limiter.check(request)
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Username or password is incorrect")
    value, expires = create_token(user.id)
    return {"access_token": value, "token_type": "bearer", "expires_at": expires}


@router.post("/sign-out")
def sign_out(response: Response) -> dict:
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/me")
def whoami(user: User = Depends(current_user)) -> dict:
    return {"user": s.me(user)}
