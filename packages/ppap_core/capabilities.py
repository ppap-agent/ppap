from __future__ import annotations

from ppap_core.llm import llm_configured
from ppap_core.settings import Settings


def _state(configured: bool) -> dict[str, str]:
    """Configuration presence only. External readiness is proven elsewhere."""
    return {"state": "configured" if configured else "disabled"}


def report(settings: Settings) -> dict[str, dict[str, str]]:
    """Capability flags are not GitHub readiness probes and not product Stories."""
    return {
        "llm": _state(llm_configured(settings)),
        "feishu": _state(bool(settings.feishu_app_id.strip() and settings.feishu_app_secret.strip())),
        "partner_ai": _state(bool(settings.partner_ai_endpoint.strip())),
        "legacy_engine": _state(bool(settings.legacy_engine_url.strip())),
    }
