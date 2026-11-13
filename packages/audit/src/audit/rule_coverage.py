"""Notes for confirmed rule items that the Standard checker did not execute."""

from __future__ import annotations

from typing import Any

REASON_MANUAL_ONLY = "manual-only"
REASON_UNSUPPORTED = "unsupported"


def unsupported_item_note(item: dict[str, Any]) -> str:
    label = item.get("rule_to_check") or item.get("id") or "规则条目"
    if item.get("reason") == REASON_UNSUPPORTED:
        return f"{label}（未自动检查：当前文档类型没有可执行检查器）"
    return f"{label}（未自动检查：无对应内置检查，需人工核对）"
