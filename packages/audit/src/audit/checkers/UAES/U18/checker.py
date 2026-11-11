"""UAES U18 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


class UaesTraceabilityAgent(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.require_text("供应商零件追溯")
        for names in [("基本信息",), ("分供方追溯",), ("生产过程追溯",), ("交付追溯",), ("追溯状态总结",)]:
            facts.field(names, title=("供应商零件追溯",))
        return "PASS", "已读取供应商零件追溯五个区域的对应内容。"


def create_audit(audit_file, rule, group_context=None):
    return UaesTraceabilityAgent(audit_file, rule, group_context, resource=RESOURCE)
