"""The generative-AI boundary.

One provider: Gemini on Vertex AI (``AI_BACKEND=vertex``), replacing InHeir's Azure OpenAI
and Beacon's Bedrock. With ``AI_BACKEND=none`` every AI feature reports that it is not
configured — nothing returns invented output. AI never writes to the case directly:
extraction proposals pass a grounding gate, answers carry citations, and actions it might
suggest still need a human approval.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings


class AINotConfigured(RuntimeError):
    pass


def enabled() -> bool:
    settings = get_settings()
    return settings.ai_backend == "vertex" and bool(settings.gcp_project)


def model_name() -> str:
    return get_settings().gemini_model


def generate_text(prompt: str, *, system: str = "") -> str:
    if not enabled():
        raise AINotConfigured("AI_BACKEND is not configured (set AI_BACKEND=vertex and GCP_PROJECT)")
    from app.infrastructure.google_cloud import gemini

    return gemini.generate(prompt, system=system)


def generate_json(prompt: str, *, schema: dict[str, Any], system: str = "") -> Any:
    if not enabled():
        raise AINotConfigured("AI_BACKEND is not configured (set AI_BACKEND=vertex and GCP_PROJECT)")
    from app.infrastructure.google_cloud import gemini

    return gemini.generate_json(prompt, schema=schema, system=system)


def status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "backend": settings.ai_backend,
        "model": settings.gemini_model if enabled() else None,
        "configured": enabled(),
    }
