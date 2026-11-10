"""Bounded retries and provider-call accounting for every external service.

``call_provider`` wraps one Google Cloud call: it retries only *transient* failures
(throttling, unavailability, deadline, connection reset) with exponential backoff and full
jitter, stops at a fixed attempt count and a wall-clock budget, and records the outcome in
``PROVIDER_STATS`` (exposed on ``/ready`` and ``/api/v1/ops/metrics``) and the structured log.
Permanent errors (bad request, permission denied, not found) fail immediately.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.logging import event

logger = logging.getLogger("groundwork.provider")
T = TypeVar("T")

TRANSIENT_NAMES = {
    "TooManyRequests",
    "ResourceExhausted",
    "ServiceUnavailable",
    "InternalServerError",
    "DeadlineExceeded",
    "GatewayTimeout",
    "BadGateway",
    "RetryError",
    "ConnectionError",
    "ConnectionResetError",
    "TimeoutError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteDisconnected",
}
TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """A provider call failed. ``transient`` says whether retrying later could help."""

    def __init__(self, provider: str, operation: str, cause: BaseException, *, transient: bool):
        self.provider, self.operation, self.transient = provider, operation, transient
        self.cause_type = type(cause).__name__
        super().__init__(f"{provider}.{operation} failed ({self.cause_type})")


def is_transient(exc: BaseException) -> bool:
    if type(exc).__name__ in TRANSIENT_NAMES:
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None
    code = getattr(code, "value", code)
    if isinstance(code, tuple):
        code = code[0]
    return isinstance(code, int) and code in TRANSIENT_CODES


def _row() -> dict[str, Any]:
    return {"calls": 0, "failures": 0, "retries": 0, "last_error": "", "last_ms": 0.0, "last_at": 0.0}


class _Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.data: dict[str, dict[str, Any]] = {}

    def record(self, provider: str, ok: bool, ms: float, error: str = "") -> None:
        with self._lock:
            row = self.data.setdefault(provider, _row())
            row["calls"] += 1
            row["last_ms"] = round(ms, 1)
            row["last_at"] = time.time()
            if not ok:
                row["failures"] += 1
                row["last_error"] = error

    def retry(self, provider: str) -> None:
        with self._lock:
            self.data.setdefault(provider, _row())["retries"] += 1

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self.data.items()}

    def reset(self) -> None:
        with self._lock:
            self.data.clear()


PROVIDER_STATS = _Stats()


def call_provider(
    provider: str,
    operation: str,
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    budget_seconds: float = 60.0,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    started = time.monotonic()
    for attempt in range(1, attempts + 1):
        t0 = time.monotonic()
        try:
            result = fn()
        except Exception as exc:  # classified below; never swallowed
            transient = is_transient(exc)
            elapsed = time.monotonic() - started
            delay = random.uniform(0, base_delay * 2 ** (attempt - 1))
            if transient and attempt < attempts and elapsed + delay < budget_seconds:
                PROVIDER_STATS.retry(provider)
                event(logger, "provider retry", logging.WARNING, provider=provider, operation=operation, attempt=attempt, error_type=type(exc).__name__)
                sleep(delay)
                continue
            ms = (time.monotonic() - t0) * 1000
            PROVIDER_STATS.record(provider, False, ms, type(exc).__name__)
            event(
                logger, "provider failed", logging.ERROR, provider=provider, operation=operation, attempt=attempt,
                transient=transient, error_type=type(exc).__name__, duration_ms=round(ms, 1),
            )
            raise ProviderError(provider, operation, exc, transient=transient) from exc
        ms = (time.monotonic() - t0) * 1000
        PROVIDER_STATS.record(provider, True, ms)
        event(logger, "provider ok", provider=provider, operation=operation, attempt=attempt, duration_ms=round(ms, 1))
        return result
    raise AssertionError("unreachable")  # pragma: no cover
