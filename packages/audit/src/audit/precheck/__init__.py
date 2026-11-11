"""UAES data preparation checks run before an audit: file roles, required structure and pairing facts.

These never produce formal findings. UAES checker leaves call ``prepare`` from their ``precheck`` hook.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def prepare(context: Any, resource: dict[str, Any]) -> list[dict]:
    """Preparation facts for one target, executed only in guest."""
    from audit.engine.checkers.rule_declarations import discover_declared_rules
    from audit.precheck.contract import fact
    from audit.precheck.dependencies import dependency_facts

    entry = context.rule.preset_key
    facts = list(dependency_facts(context.audit_file, entry, context.group_context))
    implementation = resource["carriers"].get(Path(context.audit_file.file_url).suffix.lower())
    if implementation is None:
        return [*facts, fact("coverage", "not_checked", "此检查程序尚不支持原件格式的准备检查。")]
    module, name = implementation.split(":", 1)
    cls = getattr(importlib.import_module(module), name)
    checker = cls(context.audit_file, context.rule, group_context=context.group_context)
    context.on_close(checker)
    declarations = discover_declared_rules(cls)
    for source_id, method in getattr(cls, "PRECHECK_METHODS", {}).items():
        indices = [index for index, (method_name, _) in enumerate(declarations) if method_name == method]
        if len(indices) != 1:
            facts.append(fact(source_id, "error", "预检方法绑定无效。"))
            continue
        observed = checker.check(indices[0])
        status, evidence = getattr(observed, "status", None), getattr(observed, "evidence", None)
        mapped = "satisfied" if status == "PASS" and evidence and evidence.sources else "error" if status == "ERR" else "unknown"
        facts.append(fact(source_id, mapped, getattr(observed, "suggestions", "未取得检查结论。"), evidence=evidence.model_dump(mode="json") if evidence else None))
    if entry == "U07":
        from audit.precheck.pfmea import pfmea_precheck

        facts.extend(pfmea_precheck(context.audit_file, entry, context.group_context))
    if entry == "U08":
        from audit.precheck.cp import cp_precheck

        facts.extend(cp_precheck(context.audit_file, entry, context.group_context))
    custom = getattr(checker, "precheck", None)
    if custom:
        facts.extend(custom())
    return facts
