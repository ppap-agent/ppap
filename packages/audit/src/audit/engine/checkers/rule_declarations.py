"""Shared discovery of deterministic agent rule declarations."""

from __future__ import annotations


def discover_declared_rules(agent_cls: type[object]) -> tuple[tuple[str, str], ...]:
    """Return decorated checks. Numbered rule methods keep their existing order."""
    numbered: list[tuple[int, str, str]] = []
    named: list[tuple[str, str]] = []
    for name in dir(agent_cls):
        method = getattr(agent_cls, name, None)
        description = getattr(method, "__rule_description__", None)
        if description is None:
            continue
        text = str(description)
        if name.startswith("rule_"):
            try:
                numbered.append((int(name.split("_")[1]), name, text))
                continue
            except ValueError:
                pass
        named.append((name, text))
    numbered.sort(key=lambda item: item[0])
    named.sort(key=lambda item: item[0])
    return tuple((name, description) for _number, name, description in numbered) + tuple(named)
