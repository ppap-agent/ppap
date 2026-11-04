"""L09's list is triggered by part number, never by an optional sequence cell."""

from audit.checkers.v3.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import Table

from .facts import NativeFacts, compact


class LiAutoFixtureAgent(NativeFacts, CheckingFixtureApprovalExcelAgent):
    EXPECTED_RULE_CATEGORIES = ("检具清单与认可报告完整性审核", "检具清单标题规范性审核", "所附文件与结论一致性审核")
    PRECHECK_METHODS = {"L09:1:15af01843efa244952e4": "rule_1"}

    @rule("检具清单与认可报告完整性审核")
    def rule_1(self, ctx):
        table = Table(ctx)
        report = table.sheet("检具认可报告")
        # Source clause 1 requires meaningful report content outside remarks;
        # it does not add a new mandatory set of template-specific labels.
        content = []
        in_remarks = False
        for row in range(1, report.ws.max_row + 1):
            for column in range(1, report.ws.max_column + 1):
                value = compact(report.cell(row, column).value())
                if value in {"备注", "备注：", "备注:"}:
                    in_remarks = True
                elif value and not in_remarks and value not in {"检具认可报告", "所附文件", "结论"}:
                    content.append(value)
        if not content:
            return "NG", "检具认可报告除备注之外没有可核实的内容。"
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
