"""Structured logging with per-request correlation IDs.

In production every log line is one JSON object in the shape Cloud Logging parses natively
(``severity``, ``message``, ``logging.googleapis.com/trace``), so a single case operation can
be followed from the browser request through the API, the database and each provider call.

Log lines carry identifiers (request id, case id, provider, duration), never document text,
prompts, answers, names or credentials. ``scrub`` removes anything that looks like a secret
or a high-risk identifier before it is written.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from typing import Any

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

_SECRET = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]+|(?:password|secret|token|api[_-]?key)\s*[=:]\s*\S+)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def scrub(text: str) -> str:
    text = _SECRET.sub("[REDACTED]", text)
    text = _SSN.sub("[REDACTED:SSN]", text)
    return _EMAIL.sub("[REDACTED:EMAIL]", text)


class JsonFormatter(logging.Formatter):
    def __init__(self, project: str = ""):
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "message": scrub(record.getMessage()),
            "logger": record.name,
            "request_id": request_id.get(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
        }
        trace = trace_id.get()
        if trace and self.project:
            entry["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update({k: v for k, v in fields.items() if k not in entry})
        if record.exc_info and record.exc_info[0] is not None:
            # Type and scrubbed message only: tracebacks can carry request data.
            entry["error_type"] = record.exc_info[0].__name__
            entry["error"] = scrub(str(record.exc_info[1]))[:500]
        return json.dumps(entry, default=str, ensure_ascii=False)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure(*, json_logs: bool, project: str = "", level: str = "INFO") -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_groundwork", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler._groundwork = True  # type: ignore[attr-defined]
    if json_logs:
        handler.setFormatter(JsonFormatter(project))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s [%(request_id)s] %(message)s"))
        handler.addFilter(_RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(level)


def new_request_id(incoming: str | None) -> str:
    if incoming and re.fullmatch(r"[A-Za-z0-9._-]{8,64}", incoming):
        return incoming
    return uuid.uuid4().hex


def parse_cloud_trace(header: str | None) -> str:
    """``X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`` → TRACE_ID."""
    if not header:
        return ""
    trace = header.split("/", 1)[0]
    return trace if re.fullmatch(r"[0-9a-fA-F]{16,32}", trace) else ""


def event(logger: logging.Logger, message: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log a structured event. Pass identifiers and measurements only — never content."""
    logger.log(level, message, extra={"fields": fields})
