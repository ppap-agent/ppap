"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from audit.engine.checkers.date_utils import normalize_excel_date
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("版本号", "修订日期", "修订部门/姓名", "零件/总成名称", "适用车型", "零件/总成编号", "供应商名称")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "已读取DVP&R七个身份字段的实际内容。")

    def dated_rows(self, start, end):
        facts = self.native()
        table = facts.table(("DVP", "设计验证计划"))
        rows = facts.rows({"start": (start,), "end": (end,)}, title=(table["id"],))
        checked = 0
        for row in rows:
            if any((span[0] <= row.row <= span[2] and span[1] == 1 and (span[3] >= table["cols"]) for span in table.get("merges", []))):
                continue
            checked += 1
            first, last = (row["start"], row["end"])
            if blank(first) or blank(last):
                return ("NG", "试验时间对有空白日期。")
            first, last = (normalize_excel_date(first), normalize_excel_date(last))
            if first is None or last is None:
                raise UnknownFact("试验时间无法可靠解析。")
            if first >= last:
                return ("NG", "开始日期必须严格早于同行结束日期。")
        if not checked:
            raise UnknownFact("日期区域只有整行合并说明，没有实际试验日期对。")
        return ("PASS", "非整行合并的全部日期对已按原件核对。")

    def rule_4(self):
        return self.dated_rows("计划开始", "计划结束")

    def rule_5(self):
        return self.dated_rows("试验实际开始日期", "试验实际结束日期")
