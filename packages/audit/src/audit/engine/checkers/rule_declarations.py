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


def _agent_for_target(doc_type: str, filename: str, rule_profile: str) -> type[object] | None:
    from pathlib import Path as FilePath

    from audit.engine.routing import resolve_engine_agent_class
    from audit.kinds import PpapDocumentType

    name = filename or "document.xlsx"
    if FilePath(name).suffix.lower() == ".doc":
        name = str(FilePath(name).with_suffix(".docx"))
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.from_string(doc_type or ""),
        name,
        rule_profile or "v2",
    )
    agent_cls = getattr(agent_cls, "TARGET_EXCEL_AGENT_CLASS", None) or agent_cls
    return agent_cls


def declarations_for_target(doc_type: str, filename: str, rule_profile: str) -> tuple[tuple[str, str], ...]:
    """Resolve the builtin checker list for one target file without executing it."""
    agent_cls = _agent_for_target(doc_type, filename, rule_profile)
    return discover_declared_rules(agent_cls) if agent_cls else ()


def freeze_target_rule_execution(rule_body: dict | None, filename: str):
    from audit.rule_coverage import freeze_rule_execution

    if not rule_body or rule_body.get("kind") == "presence":
        return None
    agent_cls = _agent_for_target(
        str(rule_body.get("type") or ""),
        filename,
        str(rule_body.get("rule_profile") or "v2"),
    )
    declarations = discover_declared_rules(agent_cls) if agent_cls else ()
    return freeze_rule_execution(rule_body, declarations)
