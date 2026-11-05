"""L05 checks each declared plan column and each complete R&R sample grid."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.msa_report_agent import _RULE3_EVALUATOR_RANGES, _RULE3_SAMPLE_COL_RANGE, MsaReportAgent
from audit.engine.rule_engine import Table

from .facts import NativeFacts, compact


class LiAutoMsaAgent(NativeFacts, MsaReportAgent):
    EXPECTED_RULE_CATEGORIES = ("任务项字段识别与状态审核", "测量系统分析计划表完整性审核", "量具重复性和再现性样本数量审核", "量具重复性和再现性结论判定审核")
    PRECHECK_METHODS = {"L05:2": "rule_2"}

    @rule("测量系统分析计划表完整性审核")
    def rule_2(self, ctx):
        checked = 0
        for sheet in Table(ctx).find_sheets("测量系统分析计划表"):
            section = sheet.section("计划主数据区")
            serial = section.columns("序号")["序号"]
            header_columns = []
            for column in range(1, sheet.ws.max_column + 1):
                labels = [compact(sheet.cell(index, column).value()) for index in range(section.resolved.header_row, serial.data_start_row)]
                if any(label for label in labels) and not any("备注" in label for label in labels):
                    header_columns.append(column)
            if len(header_columns) < 2:
                return "UNABLE", "尚不能完整确认计划表业务列。"
            for index in range(serial.data_start_row, sheet.ws.max_row + 1):
                values = {column: compact(sheet.cell(index, column).value()) for column in header_columns}
                value = values[serial.header_col]
                if not any(values.values()):
                    continue
                if value.rstrip(":：") in {"备注", "编制", "审核", "批准"}:
                    break
                if all(not text or text in {compact(sheet.cell(header, column).value()) for header in range(section.resolved.header_row, serial.data_start_row)} for column, text in values.items()):
                    continue
                checked += 1
                if any(not value for value in values.values()):
                    return "NG", f"计划表第{index}行有未填写的业务列。"
        return ("PASS", "") if checked else ("UNABLE", "计划表中没有序号数据行。")

    @rule("量具重复性和再现性样本数量审核")
    def rule_3(self, ctx):
        for sheet in Table(ctx).find_sheets("R&R数据表"):
            for evaluator, (first, last) in _RULE3_EVALUATOR_RANGES.items():
                values = sheet.scan_non_empty(row_min=first, row_max=last, col_min=min(_RULE3_SAMPLE_COL_RANGE), col_max=max(_RULE3_SAMPLE_COL_RANGE))
                if len(values) != 30:
                    return "NG", f"{sheet.title}评价人{evaluator}须有10个样本各3次的30个数据值。"
        return "PASS", ""

    def precheck(self):
        from .precheck_l1 import precheck_msa

        return precheck_msa(self)
