"""L10 checks the nine source columns and actual photo placements."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.tooling_approval_agent import ToolingApprovalAgent
from audit.engine.rule_engine import Table

from .facts import NativeFacts, columns_present, data_rows, image_regions, precheck_fact

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
            for region, _ in image_regions(self, source):
                if region[3] >= column.header_col >= region[1] and region[2] >= column.data_start_row:
                    found = True
        return ("PASS", "") if found else ("UNABLE", "未取得可核实的照片列图像，需核对原件。")

    @rule("编制与审核字段检查")
    def rule_5(self, ctx):
        # Source clause 5 requires nonempty values. Global image byte counts do
        # not prove that either of these particular fields has been filled.
        Table(ctx).field_block("模具验收头部字段区").expect("编制字段", "审核字段", context="理想模具编制/审核")
        return "PASS", ""

    def precheck(self):
        def block(ctx):
            return Table(ctx).field_block("模具验收头部字段区")

        checks = [
            precheck_fact(self, "L10:S1", lambda ctx: block(ctx).expect("零件供应商名称字段")),
            precheck_fact(self, "L10:S2", lambda ctx: block(ctx).expect("提交日期字段")),
        ]
        for item, name in [
            ("L10:S3", "工艺类型"),
            ("L10:S4", "一级分总成零件名称"),
            ("L10:S5", "分总成组件或分序模具名称"),
            ("L10:S6", "模具制造供应商"),
            ("L10:S7", "模具存放地点"),
            ("L10:S8", "零件质量评估"),
            ("L10:S9", "模具分供方及实物VS数据&标准验收"),
            ("L10:S10", "模具量产性能评估"),
            ("L10:S11", "模具维保评估"),
        ]:
            checks.append(precheck_fact(self, item, lambda ctx, name=name: columns_present(ctx, "模具验收报告", "模具验收主数据区", (name,)), code="B05"))
        checks.extend(
            [
                precheck_fact(self, "L10:S12", self.rule_3, code="W03"),
                precheck_fact(self, "L10:S13", self.rule_4, code="W01"),
                precheck_fact(self, "L10:S14", self.rule_5),
                precheck_fact(self, "L10:S15", self.rule_6, code="W03"),
            ]
        )
        return checks
