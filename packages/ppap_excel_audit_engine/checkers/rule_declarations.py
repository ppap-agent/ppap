"""Shared discovery of deterministic agent rule declarations."""

from __future__ import annotations


def discover_declared_rules(agent_cls: type[object]) -> tuple[tuple[str, str], ...]:
    """Return rule method names and descriptions in numeric rule order."""
    methods: list[tuple[int, str, str]] = []
    for name in dir(agent_cls):
        if not name.startswith("rule_"):
            continue
        method = getattr(agent_cls, name)
        description = getattr(method, "__rule_description__", None)
        if description is None:
            continue
        try:
            rule_no = int(name.split("_")[1])
        except ValueError:
            continue
        methods.append((rule_no, name, str(description)))
    methods.sort(key=lambda item: item[0])
    return tuple((name, description) for _number, name, description in methods)
