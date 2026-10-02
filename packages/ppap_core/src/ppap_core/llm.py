"""Responses-compatible URL helpers. No Chat Completions fallback. No OPENAI_* names."""

from __future__ import annotations

from ppap_core.settings import Settings


def responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


def llm_configured(settings: Settings) -> bool:
    return bool(settings.llm_api_key.strip() and settings.llm_base_url.strip() and settings.llm_model.strip())
