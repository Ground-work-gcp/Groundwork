"""The two participants, built from configuration. Each client holds only its own organisation's credential.

Demonstration note: when one Groundwork deployment holds both credentials (LocalNet, or a hosted
demo), they are separate secrets, used by separate clients, and the registry client is reachable only
from registry-role code paths. In an institutional deployment the registry operates its own
participant and its own Groundwork (or other) front end; only the Legal Aid block is configured here.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings

from .auth import HmacTokenProvider, OidcTokenProvider, TokenProvider
from .client import LedgerClient
from .errors import CantonError

ROLES = ("legal_aid", "registry")
TEMPLATES = ("OwnershipApprovalRequest", "OwnershipApprovalDecision", "OwnershipApprovalClosed")


def template_id(entity: str) -> str:
    return f"#{get_settings().canton_package_name}:Groundwork.Approval.OwnershipApproval:{entity}"


def _tokens(role: str) -> TokenProvider:
    s = get_settings()
    get = lambda k: getattr(s, f"canton_{role}_{k}")  # noqa: E731
    if get("oidc_token_url"):
        return OidcTokenProvider(get("oidc_token_url"), get("oidc_client_id"), get("oidc_client_secret"), get("oidc_audience"))
    return HmacTokenProvider(get("secret_ref"), get("user_id"))


@lru_cache(maxsize=2)
def client(role: str) -> LedgerClient:
    if role not in ROLES:
        raise CantonError("not_configured", "unknown participant role")
    s = get_settings()
    if not s.groundwork_canton_enabled:
        raise CantonError("not_configured", "Canton integration is disabled (GROUNDWORK_CANTON_ENABLED=false)")
    return LedgerClient(
        role=role, base_url=getattr(s, f"canton_{role}_ledger_url"), party=getattr(s, f"canton_{role}_party_id"),
        user_id=getattr(s, f"canton_{role}_user_id"), tokens=_tokens(role), timeout=s.canton_command_timeout_seconds,
        verify=s.canton_ca_bundle or True,
    )


def reset() -> None:
    client.cache_clear()


def status() -> dict:
    s = get_settings()
    out: dict = {"enabled": s.groundwork_canton_enabled, "network": s.groundwork_canton_network, "api": s.canton_api_mode, "participants": {}}
    if not s.groundwork_canton_enabled:
        return out
    for role in ROLES:
        try:
            c = client(role)
            out["participants"][role] = {"ok": True, "party": c.party, "ledger_end": c.ledger_end()}
        except CantonError as exc:
            out["participants"][role] = {"ok": False, "error": exc.safe()}
    return out
