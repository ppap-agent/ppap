"""Task starting points from the installed checker and resource implementations."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from audit.checkers.v3.excel_agent import ExcelAgent
from audit.checkers.v4.uaes.native import UaesSourceAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.rule_declarations import _agent_for_target, discover_declared_rules
from audit.engine.rule_engine import get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType


def task_reference(audit_file: AuditInput, rule: RuleInput) -> type[object] | None:
    if rule.rule_profile == "v4":
        from audit.presets import validate_preset_source

        validate_preset_source(rule.model_dump(mode="json"))
    return _agent_for_target(
        str(rule.type or ""),
        audit_file.file_url,
        rule.rule_profile,
        preset_key=rule.preset_key,
        preset_customer=rule.customers[0] if len(rule.customers) == 1 else None,
    )


def write_starting_point(work: Path, audit_file: AuditInput, rule: RuleInput) -> str:
    """Expose real implementation experience, without importing any test expectations."""
    candidate = task_reference(audit_file, rule)
    native = candidate is not None and issubclass(candidate, UaesSourceAgent)
    reusable = candidate is not None and issubclass(candidate, (ExcelAgent, UaesSourceAgent))
    base = candidate if reusable else ExcelAgent
    assert base is not None
    reference = candidate or ExcelAgent
    module = inspect.getmodule(reference)
    source = inspect.getsource(module) if module is not None else inspect.getsource(reference)
    (work / "reference-checker.py").write_text(source, encoding="utf-8")
    resource = get_doc_resource_by_doc_type(PpapDocumentType.from_string(str(rule.type or "")))
    if resource is not None:
        payload = {name: {key: value.model_dump(mode="json") for key, value in getattr(resource, name).items()} for name in ("sheets", "sections", "columns", "field_blocks", "fields")}
        payload["tokens"] = resource.tokens.model_dump(mode="json")
        (work / "reference-resources.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    declarations = discover_declared_rules(reference)
    methods = "\n".join(f"- {name}: {description}" for name, description in declarations)
    if rule.rule_profile == "v4":
        from audit.presets import preset_source_bindings, validate_preset_source

        preset = validate_preset_source(rule.model_dump(mode="json"))
        profile = "v4" if ".v4." in reference.__module__ else "v3" if ".v3." in reference.__module__ else "v2"
        bindings = preset_source_bindings(preset, checker_profile=profile)
        (work / "reference-bindings.json").write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "from pathlib import Path",
        f"from {base.__module__} import {base.__name__}",
        "from audit.checkers.v3.excel_agent import rule",
        "from audit.engine.rule_engine.context import RuleContext",
        "",
        f"class TaskAudit({base.__name__}):",
        "    CLAUSE_BOUND_CHECKS = True",
        "    POSITIONAL_CLAUSE_CHECKS = False",
        "    EXPECTED_RULE_CATEGORIES = ()",
    ]
    if native:
        lines.extend(
            [
                "",
                "    def __init__(self, audit_file, rule, group_context=None):",
                "        super().__init__(audit_file, rule, group_context)",
                "        from agent.execution.checker_native import bind_native_checks",
                "        self.checks = bind_native_checks(self)",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "    @classmethod",
                "    def declared_rules(cls):",
                "        # Only this task's declared methods become checks. Inherited helpers remain available.",
                "        return tuple(item for item in super().declared_rules() if item[0] in cls.__dict__)",
            ]
        )
    for index, clause in enumerate(rule.rules, 1):
        ident = str(clause.get("id") or "")
        description = str(clause.get("category") or clause.get("rule_to_check") or ident)
        lines.extend(
            [
                "",
                f"    @rule({description!r}, clause_id={ident!r})",
                f"    def check_{index:03d}(self{'' if native else ', ctx: RuleContext'}) -> tuple[str, str]:",
                "        # Reuse a matching parent method, or adapt its algorithm to this clause.",
                "        raise NotImplementedError('核对本次条款后实现')",
            ]
        )
    if native:
        lines.extend(["", "def create_audit(audit_file, rule, group_context=None):", "    return TaskAudit(audit_file, rule, group_context)", ""])
    else:
        lines.extend(
            [
                "",
                "def create_audit(audit_file, rule, group_context=None):",
                "    RESOURCE = None",
                '    if Path(__file__).with_name("resources.py").is_file():',
                "        from resources import RESOURCE",
                "    return TaskAudit(audit_file, rule, group_context, resource=RESOURCE)",
                "",
            ]
        )
    (work / "template-checker.py").write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(
        [
            f"本次参考：{reference.__module__}.{reference.__name__}。执行基类：{base.__module__}.{base.__name__}。",
            "先读 reference-checker.py 中相关方法和 reference-resources.json（若存在）。辅助函数仍可从原模块导入。",
            "template-checker.py 已按本次条款装配入口。布局不同先适配资源。",
            "UAES 原生方法无 ctx 参数，沿用 native()/related()/witness() 与 check(ith) 的证据收尾；其他 ExcelAgent 路径继续使用原资源和 Fluent API。",
            "V4 的 reference-bindings.json（若存在）是原始条款 ID → 方法名的参考映射。逐条核对本次规则和附录后复用或修订；相同 ID 不证明修改后的条款语义不变。",
            "条款含义不同则调整实现。文档类型和方法编号不证明规则相同，参考源码不决定本次判定。",
            methods or "没有对应的人工规则方法；使用原 ExcelAgent 和 Fluent API 实现本次条款。",
        ]
    )
