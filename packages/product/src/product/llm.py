"""Whether the text model is configured. No Chat Completions fallback."""

from __future__ import annotations

from product.settings import Settings


def llm_configured(settings: Settings) -> bool:
    return bool(settings.model_text_api_key.strip() and settings.model_text_base_url.strip() and settings.model_text_model.strip())
