from __future__ import annotations

from ppap_core.llm import llm_configured
from ppap_core.settings import Settings


def _state(configured: bool, proven_by: str) -> dict[str, str]:
    if configured:
        return {"state": "configured", "ready_proven_by": proven_by}
    return {"state": "disabled"}


def report(settings: Settings) -> dict[str, dict[str, str]]:
    """Capability flags are not GitHub readiness probes and not product Stories."""
    return {
        "llm": _state(llm_configured(settings), "github_actions_llm_readiness"),
        "feishu": _state(bool(settings.feishu_app_id.strip() and settings.feishu_app_secret.strip()), "issue_4"),
        "partner_ai": _state(bool(settings.partner_ai_endpoint.strip()), "issue_12"),
        "legacy_engine": _state(bool(settings.legacy_engine_url.strip()), "migration_only"),
    }
