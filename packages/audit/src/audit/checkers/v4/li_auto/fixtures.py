"""L09's list is triggered by part number, never by an optional sequence cell."""

import re

from audit.checkers.v3.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import GuardStop, Table

from .facts import NativeFacts, compact, precheck_fact, sheet_present


class LiAutoFixtureAgent(NativeFacts, CheckingFixtureApprovalExcelAgent):
    EXPECTED_RULE_CATEGORIES = ("检具清单与认可报告完整性审核", "检具清单标题规范性审核", "所附文件与结论一致性审核")
    PRECHECK_METHODS = {"L09:1": "rule_1"}

    def _report_content(self, ctx):
        table = Table(ctx)
        report = table.sheet("检具认可报告")
        block = table.field_block("检具认可报告字段区")
        recognized = 0
        for name in ("零件号字段", "零件名字段", "GD&T图号字段", "产品图纸更改级别字段", "总成号字段", "年型字段", "设计方字段", "制造方字段", "检具号字段", "三坐标检测机构字段", "日期字段"):
            field = block.field(name)
            if field.exists():
                recognized += 1
                if field.has_business_value():
                    return "PASS", "报告存在已填写的非备注业务字段。"
        conclusion = self._conclusion_candidates(block)
        if conclusion is not None:
            recognized += 1
            if self._normalized_conclusion_text(conclusion, reject_missing_tokens=True):
                return "PASS", "报告存在已填写的结论。"
        # A selected attachment option is input content. Printed option names,
        # the form number, bilingual labels and narrative blanks are not.
        for row in report.ws.iter_rows():
            for cell in row:
                value = str(cell.value or "")
                if any(word in value for word in ("合格", "完成")) and any(mark in value for mark in ("√", "✔", "☑", "✅")):
                    report.cell(cell.row, cell.column).value()
                    return "PASS", "报告存在已勾选的附件状态。"
        return ("NG", "检具认可报告除备注之外没有填写内容。") if recognized else ("UNABLE", "尚不能将报告文字定位为业务字段，不能用固定说明文字代替填写内容。")

    @rule("检具清单与认可报告完整性审核")
    def rule_1(self, ctx):
        result = self._report_content(ctx)
        if result[0] != "PASS":
            return result
        return self._list_content(ctx)

    def _list_content(self, ctx):
        table = Table(ctx)
        checked = 0
        for sheet in table.find_sheets("检具清单"):
            section = sheet.section("检具清单数据区")
            part = section.columns("零件号")["零件号"]
            headers = [
                column
                for column in range(1, sheet.ws.max_column + 1)
                if compact(sheet.cell(section.resolved.header_row, column).value()) and "备注" not in compact(sheet.cell(section.resolved.header_row, column).value())
            ]
            if len(headers) < 2:
                return "UNABLE", "尚不能完整确认检具清单业务列。"
            for row in range(part.data_start_row, sheet.ws.max_row + 1):
                value = compact(sheet.cell(row, part.header_col).value())
                if not value:
                    continue
                if any(marker in value for marker in ("备注", "编制", "审核", "批准")):
                    break
                checked += 1
                if any(not compact(sheet.cell(row, column).value()) for column in headers):
                    return "NG", f"检具清单第{row}行有未填写的业务列。"
        return ("PASS", "") if checked else ("UNABLE", "检具清单未取得零件号数据行。")

    def _contains_negative_checked_mark(self, value):
        text = str(value or "")
        marks = r"[√✔☑✅]"
        negative = r"(?:不合格|非完成|未完成)"
        return bool(re.search(negative + r"\s*[（(]\s*" + marks + r"\s*[）)]", text) or re.search(r"(?:^|[/、;；\s])" + marks + r"\s*" + negative, text) or re.search(negative + r"\s*" + marks, text))

    @rule("所附文件与结论一致性审核")
    def rule_3(self, ctx):
        table = Table(ctx)
        block = table.field_block("检具认可报告字段区")
        try:
            section = table.sheet("检具认可报告").section("所附文件数据区")
            columns = section.columns("所附文件项", optional=("所附文件状态",))
        except GuardStop as exc:
            if exc.status == "NG":
                return "UNABLE", "所附文件区域尚未可靠定位，不能确定条件是否触发。"
            raise
        rows = [row for row in section.rows_from(columns) if row.is_present("所附文件项") and not row.is_header()]
        if not rows:
            return "UNABLE", "尚未取得可核实的附件状态行。"
        for row in rows:
            if "所附文件状态" in columns:
                value = row.value("所附文件状态")
            else:
                cell = row.find_right("所附文件项", max_offset=0, use_merged_anchor=True)
                value = cell.value() if cell else None
            if not self._contains_negative_checked_mark(value):
                continue
            candidates = self._conclusion_candidates(block)
            if candidates is None or not self._normalized_conclusion_text(candidates, reject_missing_tokens=True):
                return "NG", "附件明确勾选不合格或非完成，但结论未填写。"
        return "PASS", "已逐项关联勾选标记与附件状态。"

    def precheck(self):
        return [
            precheck_fact(self, "L09:S1", lambda ctx: sheet_present(ctx, "检具清单"), code="B05"),
            precheck_fact(self, "L09:S2", lambda ctx: sheet_present(ctx, "检具认可报告"), code="B05"),
            precheck_fact(self, "L09:S3", self._report_content, code="B05"),
            precheck_fact(self, "L09:S4", self._list_content),
            precheck_fact(self, "L09:S5", self.rule_2, code="W03"),
            precheck_fact(self, "L09:S6", self.rule_3),
        ]
