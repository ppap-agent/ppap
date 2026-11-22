from __future__ import annotations

from documents.model_access import ModelUnavailable, platform_model_access

from product.settings import Settings


def _state(configured: bool) -> dict[str, str]:
    """Configuration presence only. External readiness is proven elsewhere."""
    return {"state": "configured" if configured else "disabled"}


def _text_model_configured() -> bool:
    """The platform text model as calls resolve it: endpoint, model and key all present."""
    try:
        return bool(platform_model_access("text").api_key.get_secret_value())
    except ModelUnavailable:
        return False


def report(settings: Settings) -> dict[str, dict[str, str]]:
    """Capability flags are not GitHub readiness probes and not product Stories."""
    return {
        "llm": _state(_text_model_configured()),
        "feishu": _state(bool(settings.feishu_app_id.strip() and settings.feishu_app_secret.strip())),
        "deep": _state(bool(settings.deep_controller_socket.strip())),
    }
