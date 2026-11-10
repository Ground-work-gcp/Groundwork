"""Outbound notifications (Beacon's ``channels.py``, narrowed to one Slack-compatible webhook).

Delivery rules:
* fire-and-forget on a background thread — a slow or failing channel never breaks a request;
* each payload carries an idempotency key (``X-Groundwork-Delivery``) so a receiver can drop
  duplicates from retries, and, when ``WEBHOOK_SIGNING_SECRET`` is set, an HMAC-SHA256
  signature over ``timestamp.body`` (``X-Groundwork-Signature: t=<unix>,v1=<hex>``);
* bounded retries with backoff on 429/5xx/timeouts; 4xx other than 429 is not retried;
* outcomes are counted in ``DELIVERY_STATS`` (``/api/v1/ops/metrics``) and logged without the
  URL or body. Messages carry a deep link and never document contents or PII.

``verify_signature`` is the same scheme on the receiving side; the scheduled-job endpoint
uses it to authenticate Cloud Scheduler.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid

from app.core.config import get_settings
from app.core.logging import event

logger = logging.getLogger(__name__)
ICONS = {"approval_requested": "🟣", "action_executed": "🟢", "finding_critical": "🔴"}
TOLERANCE_SECONDS = 300
_lock = threading.Lock()
DELIVERY_STATS: dict[str, int] = {"sent": 0, "failed": 0, "retried": 0}


def deep_link(case_id: str | None) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/#case/{case_id}" if case_id else f"{base}/#overview"


def sign(body: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(body: bytes, header: str | None, secret: str, *, now: int | None = None) -> bool:
    if not header or not secret:
        return False
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        ts = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((int(time.time()) if now is None else now) - ts) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def _count(key: str) -> None:
    with _lock:
        DELIVERY_STATS[key] += 1


def deliver(url: str, payload: dict, *, delivery_id: str, attempts: int = 3, sleep=time.sleep) -> bool:
    body = json.dumps(payload).encode()
    secret = get_settings().webhook_signing_secret
    for attempt in range(1, attempts + 1):
        headers = {"content-type": "application/json", "x-groundwork-delivery": delivery_id}
        if secret:
            headers["x-groundwork-signature"] = sign(body, secret)
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        status = 0
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - operator-configured https URL
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception:
            status = 0  # timeout / connection failure: retryable
        if 200 <= status < 300:
            _count("sent")
            event(logger, "webhook delivered", delivery_id=delivery_id, attempt=attempt, status=status)
            return True
        retryable = status == 0 or status == 429 or status >= 500
        if retryable and attempt < attempts:
            _count("retried")
            sleep(min(2**attempt, 10))
            continue
        _count("failed")
        event(logger, "webhook failed", logging.WARNING, delivery_id=delivery_id, attempt=attempt, status=status)
        return False
    return False  # pragma: no cover


def notify(kind: str, title: str, text: str, *, case_id: str | None = None) -> bool:
    url = get_settings().notify_webhook_url
    if not url:
        return False
    delivery_id = uuid.uuid4().hex
    payload = {"text": f"{ICONS.get(kind, '•')} *{title}*\n{text}\n<{deep_link(case_id)}|Open in Groundwork>", "kind": kind, "delivery_id": delivery_id}
    threading.Thread(target=deliver, args=(url, payload), kwargs={"delivery_id": delivery_id}, daemon=True).start()
    return True
