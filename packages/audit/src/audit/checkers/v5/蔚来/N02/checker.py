"""N02 native header and revision chronology belong to this directory leaf."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact
from audit.engine.checkers.date_utils import normalize_excel_date
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return PfmeaAudit(audit_file, rule, group_context, resource=RESOURCE)


class PfmeaAudit(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.table(("PFMEA",))
        for name in ("公司名称", "车型项目", "零件名称", "零件号", "制造地址", "PFMEA开始日期", "PFMEA修订日期", "跨职能团队"):
            facts.field((name,), title=("PFMEA",))
        return "PASS", "PFMEA八项原件表头字段及对应非空内容均已读取。"

    def rule_2(self):
        facts = self.native()
        begin = normalize_excel_date(facts.field(("PFMEA开始日期",), title=("PFMEA",)))
        revised = normalize_excel_date(facts.field(("PFMEA修订日期",), title=("PFMEA",)))
        if begin is None or revised is None:
            raise UnknownFact("PFMEA开始日期或修订日期无法可靠解析。")
        rows = facts.rows({"date": ("修订日期",)}, title=("变更履历",), anchor=("date",))
        dates = [normalize_excel_date(row["date"]) for row in rows]
        if any(value is None for value in dates):
            raise UnknownFact("变更履历存在无法可靠解析的有效日期。")
        if begin != dates[0] or revised != dates[-1]:
            return "NG", "PFMEA开始/修订日期与变更履历首/末有效物理行的日期不一致。"
        return "PASS", "PFMEA开始/修订日期与变更履历首/末有效行一致。"
