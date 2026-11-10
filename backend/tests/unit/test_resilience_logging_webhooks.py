"""Provider retry policy, structured logging hygiene, webhook signing and delivery."""

from __future__ import annotations

import json
import logging
import urllib.error

import pytest
from google.api_core.exceptions import (
    InvalidArgument,
    PermissionDenied,
    ResourceExhausted,
    ServiceUnavailable,
)

from app.core.logging import JsonFormatter, new_request_id, parse_cloud_trace, request_id, scrub
from app.core.resilience import PROVIDER_STATS, ProviderError, call_provider, is_transient
from app.infrastructure import external_integrations as hooks


def test_transient_errors_are_retried_then_succeed():
    PROVIDER_STATS.reset()
    calls = iter([ResourceExhausted("quota"), ServiceUnavailable("down"), "ok"])

    def flaky():
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    assert call_provider("p", "op", flaky, attempts=3, sleep=lambda _s: None) == "ok"
    stats = PROVIDER_STATS.snapshot()["p"]
    assert stats["retries"] == 2 and stats["calls"] == 1 and stats["failures"] == 0


def test_permanent_errors_fail_immediately_without_retry():
    PROVIDER_STATS.reset()
    attempts = []

    def bad():
        attempts.append(1)
        raise PermissionDenied("no")

    with pytest.raises(ProviderError) as info:
        call_provider("p", "op", bad, attempts=5, sleep=lambda _s: None)
    assert len(attempts) == 1 and info.value.transient is False and info.value.cause_type == "PermissionDenied"
    assert PROVIDER_STATS.snapshot()["p"]["failures"] == 1


def test_retries_stop_at_the_attempt_limit():
    with pytest.raises(ProviderError) as info:
        call_provider("p", "op", lambda: (_ for _ in ()).throw(ServiceUnavailable("x")), attempts=3, sleep=lambda _s: None)
    assert info.value.transient is True


def test_transient_classification():
    assert is_transient(ResourceExhausted("429")) and is_transient(TimeoutError())
    assert not is_transient(InvalidArgument("bad")) and not is_transient(ValueError())


def test_logs_are_json_with_request_id_and_scrubbed():
    token = request_id.set("req-12345678")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "user a.b@example.org sent 123-45-6789 with password=hunter2", None, None)
        record.fields = {"provider": "document_ai", "duration_ms": 12.5}
        line = json.loads(JsonFormatter("proj").format(record))
    finally:
        request_id.reset(token)
    assert line["severity"] == "INFO" and line["request_id"] == "req-12345678" and line["provider"] == "document_ai"
    assert "example.org" not in line["message"] and "123-45-6789" not in line["message"] and "hunter2" not in line["message"]
    assert scrub("Authorization: Bearer abc.def") == "Authorization: [REDACTED]"


def test_request_and_trace_ids():
    assert new_request_id("client-supplied-id") == "client-supplied-id"
    assert len(new_request_id("bad id with spaces")) == 32
    assert parse_cloud_trace("105445aa7843bc8bf206b12000100000/1;o=1") == "105445aa7843bc8bf206b12000100000"
    assert parse_cloud_trace("not-a-trace") == ""


def test_webhook_signature_round_trip_and_rejections():
    body = b'{"x":1}'
    header = hooks.sign(body, "s3cret", timestamp=1_700_000_000)
    assert hooks.verify_signature(body, header, "s3cret", now=1_700_000_010)
    assert not hooks.verify_signature(body + b" ", header, "s3cret", now=1_700_000_010)  # tampered body
    assert not hooks.verify_signature(body, header, "other", now=1_700_000_010)  # wrong key
    assert not hooks.verify_signature(body, header, "s3cret", now=1_700_000_000 + 3600)  # replay window
    assert not hooks.verify_signature(body, "garbage", "s3cret")
    assert not hooks.verify_signature(body, None, "s3cret")


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_webhook_delivery_retries_5xx_and_signs(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "k" * 32)
    get_settings.cache_clear()
    seen = []
    outcomes = iter([urllib.error.HTTPError("u", 503, "x", {}, None), _Resp(200)])

    def fake_urlopen(request, timeout):
        seen.append(dict(request.header_items()))
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(hooks.urllib.request, "urlopen", fake_urlopen)
    before = dict(hooks.DELIVERY_STATS)
    try:
        assert hooks.deliver("https://hooks.example.test/x", {"text": "hi"}, delivery_id="d1", sleep=lambda _s: None)
    finally:
        get_settings.cache_clear()
    assert len(seen) == 2 and all(h["X-groundwork-delivery"] == "d1" and h["X-groundwork-signature"].startswith("t=") for h in seen)
    assert hooks.DELIVERY_STATS["retried"] == before["retried"] + 1 and hooks.DELIVERY_STATS["sent"] == before["sent"] + 1


def test_webhook_4xx_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)

    monkeypatch.setattr(hooks.urllib.request, "urlopen", fake_urlopen)
    assert hooks.deliver("https://hooks.example.test/x", {}, delivery_id="d2", sleep=lambda _s: None) is False
    assert len(calls) == 1
