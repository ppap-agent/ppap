"""Bind confirmed rule items to the Standard checker's fixed execution order."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

COVERAGE_MAPPED = "mapped"
COVERAGE_UNSUPPORTED = "unsupported"
MODE_LEGACY_BUILTIN = "legacy_builtin"
MODE_RULE_ITEMS = "rule_items"
REASON_MANUAL_ONLY = "manual-only"
REASON_UNSUPPORTED = "unsupported"


def map_rule_items(
    items: Sequence[dict[str, Any]],
    declarations: Sequence[tuple[str, str]],
    *,
    source_bindings: Mapping[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Keep user text and order; do not adjudicate the rule's business meaning."""
    mapped: list[dict[str, Any]] = []
    reason = REASON_UNSUPPORTED if not declarations else REASON_MANUAL_ONLY
    methods = {name: (index, description) for index, (name, description) in enumerate(declarations)}
    if source_bindings is not None and (len(methods) != len(declarations) or any(method not in methods for _text, method in source_bindings.values())):
        raise ValueError("invalid_source_binding")
    seen = set()
    for position, raw in enumerate(items):
        item = {
            "id": str(raw.get("id") or "").strip(),
            "category": str(raw.get("category") or "").strip(),
            "priority": raw.get("priority"),
            "rule_to_check": str(raw.get("rule_to_check") or "").strip(),
        }
        if not item["id"]:
            continue
        if source_bindings is not None:
            if item["id"] in seen:
                raise ValueError("duplicate_rule_id")
            seen.add(item["id"])
            binding = source_bindings.get(item["id"])
            if binding is None or binding[0] != item["rule_to_check"]:
                item.update(coverage=COVERAGE_UNSUPPORTED, reason=reason)
                mapped.append(item)
                continue
            method = binding[1]
            position, description = methods[method]
        elif position < len(declarations):
            method, description = declarations[position]
        else:
            item.update(coverage=COVERAGE_UNSUPPORTED, reason=reason)
            mapped.append(item)
            continue
        item.update(
            coverage=COVERAGE_MAPPED,
            checker_method=method,
            checker_index=position,
            checker_description=description,
        )
        mapped.append(item)
    return mapped


def freeze_rule_execution(
    rule_body: dict[str, Any] | None,
    declarations: Iterable[tuple[str, str]] | None = None,
    *,
    source_bindings: Mapping[str, tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    if not rule_body or rule_body.get("kind") == "presence":
        return None
    items = [row for row in (rule_body.get("rules") or []) if isinstance(row, dict)]
    declared = tuple(declarations or ())
    if not items:
        return {"mode": MODE_LEGACY_BUILTIN, "items": []}
    return {"mode": MODE_RULE_ITEMS, "items": map_rule_items(items, declared, source_bindings=source_bindings)}


def unsupported_item_note(item: dict[str, Any]) -> str:
    label = item.get("rule_to_check") or item.get("id") or "规则条目"
    if item.get("reason") == REASON_UNSUPPORTED:
        return f"{label}（未自动检查：当前文档类型没有可执行检查器）"
    return f"{label}（未自动检查：无对应内置检查，需人工核对）"
