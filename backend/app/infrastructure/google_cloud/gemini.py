"""Gemini via the ``google-genai`` SDK: Vertex AI (ADC) or a Google AI Studio API key.

Every call is bounded: prompt length is capped before sending, the HTTP request has a timeout,
output tokens are capped, transient errors (429 / 5xx / deadline) are retried with backoff,
and a blocked or empty response raises instead of returning an empty answer. Prompts and
responses are never logged — only the model id, sizes and timing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.core.resilience import call_provider

PROVIDER = "vertex_gemini"


class EmptyResponse(RuntimeError):
    """The model returned no usable text (blocked by safety filters, or empty)."""


@lru_cache(maxsize=1)
def _client():
    from google import genai
    from google.genai import types

    settings = get_settings()
    http_options = types.HttpOptions(timeout=int(settings.provider_timeout_seconds * 1000))
    if settings.ai_backend == "gemini_api":
        return genai.Client(api_key=settings.gemini_api_key, http_options=http_options)
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project,
        location=settings.gcp_location,
        http_options=types.HttpOptions(timeout=int(settings.provider_timeout_seconds * 1000)),
    )


def _bounded(prompt: str) -> str:
    limit = get_settings().gemini_max_prompt_chars
    return prompt if len(prompt) <= limit else prompt[:limit] + "\n[truncated]"


def _text(response: Any) -> str:
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        reason = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = str(getattr(candidates[0], "finish_reason", "") or "")
        raise EmptyResponse(f"model returned no text {reason}".strip())
    return text


def generate(prompt: str, *, system: str = "") -> str:
    from google.genai import types

    settings = get_settings()
    config = types.GenerateContentConfig(system_instruction=system or None, temperature=0.1, max_output_tokens=settings.gemini_max_output_tokens)
    response = call_provider(
        PROVIDER, "generate", lambda: _client().models.generate_content(model=settings.gemini_model, contents=_bounded(prompt), config=config),
        attempts=settings.provider_attempts, budget_seconds=settings.provider_timeout_seconds * 2,
    )
    return _text(response)


def generate_json(prompt: str, *, schema: dict[str, Any], system: str = "") -> Any:
    from google.genai import types

    settings = get_settings()
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=0,
        response_mime_type="application/json",
        response_schema=schema,
        max_output_tokens=max(settings.gemini_max_output_tokens, 4096),
    )
    response = call_provider(
        PROVIDER, "generate_json", lambda: _client().models.generate_content(model=settings.gemini_model, contents=_bounded(prompt), config=config),
        attempts=settings.provider_attempts, budget_seconds=settings.provider_timeout_seconds * 2,
    )
    return json.loads(_text(response))
