"""Responses-compatible URL helpers. No Chat Completions fallback."""

from __future__ import annotations

from product.settings import Settings


def responses_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


def llm_configured(settings: Settings) -> bool:
    return bool(settings.model_text_api_key.strip() and settings.model_text_base_url.strip() and settings.model_text_model.strip())
