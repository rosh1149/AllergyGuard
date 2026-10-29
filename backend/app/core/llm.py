"""Shared Claude client. Returns None when no API key is configured (features fall back)."""

from __future__ import annotations

from app.core.config import get_settings

DEFAULT_MODEL = "claude-opus-5-5"
# Server-side refusal fallback: a declined request is retried on a suitable model in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def get_client(timeout: float = 30.0):
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=timeout, max_retries=1)


def model_name() -> str:
    return get_settings().llm_model or DEFAULT_MODEL
