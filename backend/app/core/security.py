"""Passwords, session tokens and the role model.

Merged from InHeir (bcrypt + JWT cookie) and Beacon (reads vs. writes vs. approvals as
separate privileges). The role is read from the database on every request, never trusted
from the token, so a demotion takes effect immediately.
"""

from __future__ import annotations

import time

import bcrypt
import jwt

from .config import get_settings

ROLES = ("viewer", "paralegal", "attorney", "admin")
_RANK = {role: rank for rank, role in enumerate(ROLES)}

# permission -> the least role that holds it
PERMISSIONS: dict[str, str] = {
    "case:read": "viewer",
    "case:write": "paralegal",
    "evidence:write": "paralegal",
    "financials:write": "paralegal",
    "analysis:run": "paralegal",
    "task:write": "paralegal",
    "action:propose": "paralegal",
    "finding:review": "paralegal",
    "action:approve": "attorney",
    "decision:record": "attorney",
    "users:manage": "admin",
}


def has_permission(role: str, permission: str) -> bool:
    needed = PERMISSIONS[permission]
    return _RANK.get(role, -1) >= _RANK[needed]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: str) -> tuple[str, int]:
    settings = get_settings()
    expires = int(time.time()) + settings.jwt_ttl_minutes * 60
    token = jwt.encode({"sub": user_id, "exp": expires, "iss": "groundwork"}, settings.jwt_secret, algorithm="HS256")
    return token, expires


def decode_token(token: str) -> str | None:
    """The user id in a valid, unexpired token, else None."""
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], issuer="groundwork")
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None
