"""L10 checks the nine source columns and actual photo placements."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.tooling_approval_agent import ToolingApprovalAgent
from audit.engine.rule_engine import Table

from .facts import NativeFacts, data_rows

_COLUMNS = ("一级分总成零件名称", "分总成组件或分序模具名称", "模具制造供应商", "模具存放地点", "零件质量评估", "模具分供方及实物VS数据&标准验收", "模具量产性能评估", "模具维保评估")


class LiAutoToolingAgent(NativeFacts, ToolingApprovalAgent):
    EXPECTED_RULE_CATEGORIES = ("零件供应商名称与提交日期字段检查", "关键列完整性与内容非空检查", "模具设计尺寸格式检查", "实物照片列检查", "编制与审核字段检查", "实物确认列内容检查")
    PRECHECK_METHODS = {"L10:1:4e8e945601812ae4b33b": "rule_1", "L10:2:fd6961d07be220cb241c": "rule_2", "L10:5:d947b1eed353bd878313": "rule_5"}

    @rule("关键列完整性与内容非空检查")
    def rule_2(self, ctx):
        for row in data_rows(ctx, "模具验收报告", "模具验收主数据区", ("工艺类型", *_COLUMNS), trigger="工艺类型"):
            row.expect(*_COLUMNS, context="理想模具工艺类型")
        return "PASS", ""

    @rule("实物照片列检查")
    def rule_4(self, ctx):
        found = False
        for sheet in Table(ctx).find_sheets("模具验收报告"):
            section = sheet.section("模具验收主数据区")
            columns = section.columns("实物照片")
            column = columns["实物照片"]
            source = self.native_tables.get(sheet.title)
            if source is None:
                return "UNABLE", "尚不能从原件确认照片列中的图像。"
            for visual in source.get("visual_evidence", []):
                region = visual.get("range")
                if region is None:
                    return "UNABLE", "照片对象位置尚不能确认。"
                if region[3] >= column.header_col >= region[1] and region[2] >= column.data_start_row:
                    found = True
        return ("PASS", "") if found else ("UNABLE", "未取得可核实的照片列图像，需核对原件。")

    @rule("编制与审核字段检查")
    def rule_5(self, ctx):
        # Source clause 5 requires nonempty values. Global image byte counts do
        # not prove that either of these particular fields has been filled.
        Table(ctx).field_block("模具验收头部字段区").expect("编制字段", "审核字段", context="理想模具编制/审核")
        return "PASS", ""
