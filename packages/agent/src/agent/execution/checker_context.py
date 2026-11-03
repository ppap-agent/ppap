"""Task starting points from the installed checker and resource implementations."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from audit.checkers.v3.excel_agent import ExcelAgent
from audit.contracts import AuditInput, RuleInput
from audit.engine.checkers.rule_declarations import _agent_for_target, discover_declared_rules
from audit.engine.rule_engine import get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType


def write_starting_point(work: Path, audit_file: AuditInput, rule: RuleInput) -> str:
    """Expose real implementation experience, without importing any test expectations."""
    candidate = _agent_for_target(str(rule.type or ""), audit_file.file_url, rule.rule_profile)
    reusable = candidate is not None and issubclass(candidate, ExcelAgent)
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
        "",
        "    @classmethod",
        "    def declared_rules(cls):",
        "        # Only this task's declared methods become checks. Inherited helpers remain available.",
        "        return tuple(item for item in super().declared_rules() if item[0] in cls.__dict__)",
    ]
    for index, clause in enumerate(rule.rules, 1):
        ident = str(clause.get("id") or "")
        description = str(clause.get("category") or clause.get("rule_to_check") or ident)
        lines.extend(
            [
                "",
                f"    @rule({description!r}, clause_id={ident!r})",
                f"    def check_{index:03d}(self, ctx: RuleContext) -> tuple[str, str]:",
                "        # Reuse a matching parent method, or adapt its algorithm to this clause.",
                "        raise NotImplementedError('核对本次条款后实现')",
            ]
        )
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
            "可直接调用语义相符的父类方法；若参考类不支持当前 checks 合同，保留其算法和辅助函数，在原 ExcelAgent 内装配本次方法。",
            "条款含义不同则调整实现。文档类型和方法编号不证明规则相同，参考源码不决定本次判定。",
            methods or "没有对应的人工规则方法；使用原 ExcelAgent 和 Fluent API 实现本次条款。",
        ]
    )
