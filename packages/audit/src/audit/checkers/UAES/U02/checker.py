"""U02 source-backed UAES checks owned by this immutable directory leaf.

The business implementation is local. No checker-version fallback is used.
"""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return MeasurementComparisonAudit(audit_file, rule, group_context, resource=RESOURCE)


class MeasurementComparisonAudit(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.require_text("测量比较报告")
        for phrase in ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师"):
            facts.require_text(phrase)
        observed = self.visual_pdf({"four_signatures": "测量比较报告底部供应商、UAES技术中心、UAES测量部门、UAES供应商质量开发工程师四处对应区域均有真实签字。不能用预印姓名或其他位置签字代替。"})
        fact = observed["four_signatures"]
        return ("PASS", "四方实际签字已从原件核实。") if fact is True else ("NG", "四方签字不完整。") if fact is False else ("UNABLE", "四方签字尚未核实，需人工复核。")
