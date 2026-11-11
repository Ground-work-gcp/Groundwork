"""Ledger API tokens. Tokens are minted or fetched server-side, cached briefly, and never leave the backend."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx
import jwt

from .errors import CantonError


class TokenProvider:
    def token(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class HmacTokenProvider(TokenProvider):
    """LocalNet only: a user token signed with the participant's own HS256 secret (unsafe-jwt-hmac-256)."""

    secret: str
    user_id: str
    ttl_seconds: int = 300

    def token(self) -> str:
        if not self.secret:
            raise CantonError("auth", "no ledger credential is configured for this participant")
        now = int(time.time())
        return jwt.encode({"sub": self.user_id, "scope": "daml_ledger_api", "iat": now, "exp": now + self.ttl_seconds}, self.secret, algorithm="HS256")


@dataclass
class OidcTokenProvider(TokenProvider):
    """DevNet / TestNet / MainNet: OAuth2 client-credentials grant against the participant operator's IdP."""

    token_url: str
    client_id: str
    client_secret: str
    audience: str
    timeout: float = 15.0
    _cached: tuple[str, float] = field(default=("", 0.0), repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def token(self) -> str:
        with self._lock:
            value, expires = self._cached
            if value and time.time() < expires - 60:
                return value
            try:
                r = httpx.post(
                    self.token_url,
                    data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret, "audience": self.audience, "scope": "daml_ledger_api"},
                    timeout=self.timeout,
                )
            except httpx.HTTPError as exc:
                raise CantonError("unavailable", "the identity provider could not be reached") from exc
            if r.status_code != 200:
                raise CantonError("auth", f"the identity provider refused the client credentials ({r.status_code})")
            body = r.json()
            self._cached = (body["access_token"], time.time() + float(body.get("expires_in", 300)))
            return self._cached[0]
