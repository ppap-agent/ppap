"""Map frozen customer rule items onto builtin checkers without rewriting them."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

COVERAGE_MAPPED = "mapped"
COVERAGE_UNSUPPORTED = "unsupported"
MODE_LEGACY_BUILTIN = "legacy_builtin"
MODE_RULE_ITEMS = "rule_items"
REASON_MANUAL_ONLY = "manual-only"
REASON_UNSUPPORTED = "unsupported"


def normalize_match_text(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def map_rule_items(items: Sequence[dict[str, Any]], declarations: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """Match each customer item to at most one unused builtin declaration by exact text."""
    unused = list(enumerate(declarations))
    mapped: list[dict[str, Any]] = []
    reason = REASON_UNSUPPORTED if not declarations else REASON_MANUAL_ONLY
    for raw in items:
        item = {
            "id": str(raw.get("id") or "").strip(),
            "category": str(raw.get("category") or "").strip(),
            "priority": raw.get("priority"),
            "rule_to_check": str(raw.get("rule_to_check") or "").strip(),
        }
        if not item["id"]:
            continue
        rule_text = normalize_match_text(item["rule_to_check"])
        match_offset = None
        for offset, (position, (method, description)) in enumerate(unused):
            if rule_text and normalize_match_text(description) == rule_text:
                match_offset = offset
                item.update(
                    coverage=COVERAGE_MAPPED,
                    checker_method=method,
                    checker_index=position,
                    checker_description=description,
                )
                unused.pop(offset)
                break
        if match_offset is None:
            item.update(coverage=COVERAGE_UNSUPPORTED, reason=reason)
        mapped.append(item)
    return mapped


def freeze_rule_execution(rule_body: dict[str, Any] | None, declarations: Iterable[tuple[str, str]] | None = None) -> dict[str, Any] | None:
    if not rule_body or rule_body.get("kind") == "presence":
        return None
    items = [row for row in (rule_body.get("rules") or []) if isinstance(row, dict)]
    declared = tuple(declarations or ())
    if not items:
        return {"mode": MODE_LEGACY_BUILTIN, "items": []}
    return {"mode": MODE_RULE_ITEMS, "items": map_rule_items(items, declared)}


def unsupported_item_note(item: dict[str, Any]) -> str:
    label = item.get("rule_to_check") or item.get("id") or "规则条目"
    if item.get("reason") == REASON_UNSUPPORTED:
        return f"{label}（未自动检查：当前文档类型没有可执行检查器）"
    return f"{label}（未自动检查：无对应内置检查，需人工核对）"
