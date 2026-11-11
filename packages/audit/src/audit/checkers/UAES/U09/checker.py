"""UAES U09 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


class UaesPackagingAgent(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.require_title("包装方案")
        for phrase in ("零件基础信息", "供应商基础信息", "包装设计的基本要求", "针对该零件包装的特殊要求", "包装方案信息", "包装材料清单"):
            facts.require_text(phrase)
        return "PASS", "已读取包装方案标题及原规则要求的六个内容块。"


def create_audit(audit_file, rule, group_context=None):
    return UaesPackagingAgent(audit_file, rule, group_context, resource=RESOURCE)
