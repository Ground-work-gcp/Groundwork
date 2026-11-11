"""Ledger errors, classified and sanitised. Raw participant messages never reach users or logs."""

from __future__ import annotations

RETRYABLE = {"timeout", "unavailable", "rate_limited"}


class CantonError(RuntimeError):
    """``kind``: auth | timeout | unavailable | rate_limited | duplicate | stale_contract | rejected | not_configured."""

    def __init__(self, kind: str, message: str, *, code: str = "", correlation_id: str = ""):
        super().__init__(message)
        self.kind, self.code, self.correlation_id = kind, code, correlation_id

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    def safe(self) -> str:
        ref = f" (ref {self.correlation_id[:12]})" if self.correlation_id else ""
        return f"{self.kind.replace('_', ' ')}: {self}{ref}"


def classify(status: int, body: dict | None) -> CantonError:
    body = body or {}
    code = str(body.get("code", ""))
    corr = str(body.get("correlationId", "") or "")
    if status in (401, 403):
        return CantonError("auth", "the participant refused the credentials", code=code, correlation_id=corr)
    if status == 429:
        return CantonError("rate_limited", "the participant is busy", code=code, correlation_id=corr)
    if status >= 500 or code in ("PARTICIPANT_BACKPRESSURE", "SERVICE_NOT_RUNNING", "NOT_CONNECTED_TO_ANY_SYNCHRONIZER"):
        return CantonError("unavailable", "the participant is unavailable", code=code, correlation_id=corr)
    if code in ("DUPLICATE_COMMAND", "SUBMISSION_ALREADY_IN_FLIGHT"):
        return CantonError("duplicate", "this command was already submitted", code=code, correlation_id=corr)
    if code in ("CONTRACT_NOT_FOUND", "LOCAL_VERDICT_INACTIVE_CONTRACTS", "INACTIVE_CONTRACTS"):
        return CantonError("stale_contract", "the contract is no longer active on the ledger", code=code, correlation_id=corr)
    cause = str(body.get("cause", ""))
    # Contract assertions (assertMsg) are ours and safe to show; anything else is summarised.
    for hint in ("has expired", "not the evidence", "not the proposal", "not expired yet", "rejection needs", "evidence item", "only the proposer"):
        if hint in cause:
            return CantonError("rejected", "the contract refused the command: " + cause[cause.find(hint) - 4 if cause.find(hint) > 4 else 0:][:120].strip(), code=code, correlation_id=corr)
    return CantonError("rejected", f"the ledger rejected the command ({code or status})", code=code, correlation_id=corr)
