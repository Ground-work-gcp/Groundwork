"""Request dependencies: the database session, the signed-in user, and permission checks."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_token, has_permission
from app.infrastructure.database.models import User
from app.infrastructure.database.session import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CLIENT_HEADER = "x-groundwork-client"


def current_user(request: Request, db: Session = Depends(get_db, scope="function")) -> User:
    settings = get_settings()
    token = None
    via_cookie = False
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    elif settings.cookie_name in request.cookies:
        token = request.cookies[settings.cookie_name]
        via_cookie = True
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required")
    user_id = decode_token(token)
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    # CSRF: a cookie-authenticated write must come from the Groundwork client (a custom header
    # cannot be set cross-site without a CORS preflight, which only allowed origins pass).
    if via_cookie and request.method not in SAFE_METHODS and request.headers.get(CLIENT_HEADER) != "web":
        raise HTTPException(status_code=403, detail="Missing client header")
    request.state.user = user
    # Plain copies for the access log, which runs after the session (and its ORM objects) is gone.
    request.state.user_id = user.id
    request.state.organization_id = user.organization_id
    return user


def require(permission: str) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=403, detail=f"Your role ({user.role}) cannot {permission.replace(':', ' ')}")
        return user

    return dependency


def platform_admin(user: User = Depends(current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status_code=404, detail="Not found")
    return user


class RateLimiter:
    """A small in-process sliding window for credential endpoints (per client IP)."""

    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > self.seconds:
            window.popleft()
        if len(window) >= self.limit:
            raise HTTPException(status_code=429, detail="Too many attempts; wait a few minutes")
        window.append(now)

    def reset(self) -> None:
        self.hits.clear()


credential_limiter = RateLimiter(limit=20, seconds=300)
