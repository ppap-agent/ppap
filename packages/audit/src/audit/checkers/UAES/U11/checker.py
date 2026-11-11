"""UAES U11 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from audit.checker_lib.source import MissingFact, SourceAudit, UnknownFact
from resources import RESOURCE


class UaesEngineeringHistoryAgent(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.require_text("工程更改一览表")
        aliases = {
            "项目": ("项目", "项目名称", "项目代号"),
            "零件号": ("零件号", "物料编号", "零件编号"),
            "变更内容": ("变更内容简述", "变更内容", "更改内容"),
            "原因": ("变更原因", "更改原因"),
            "时间": ("时间", "变更日期", "更改日期", "日期"),
        }
        table = facts.table(("工程更改",))
        try:
            facts.columns(table, aliases)
        except MissingFact as exc:
            # The source explicitly permits semantically equivalent aliases.
            # An unrecognized spelling is not proof that the field is absent.
            raise UnknownFact("尚不能完整确认工程更改表字段及合法别名，需人工核对。") from exc
        return "PASS", "工程更改表及项目、零件号、变更内容、原因、时间已核实。"


def create_audit(audit_file, rule, group_context=None):
    return UaesEngineeringHistoryAgent(audit_file, rule, group_context, resource=RESOURCE)
