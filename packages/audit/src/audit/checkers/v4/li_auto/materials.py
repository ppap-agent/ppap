"""L04 requires the literal Y token for outsourced parts."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.sub_supplier_list_excel_agent import SubSupplierListExcelAgent

from .facts import NativeFacts, data_rows


class LiAutoMaterialsAgent(NativeFacts, SubSupplierListExcelAgent):
    EXPECTED_RULE_CATEGORIES = ("单件重量列及精度审核", "材料信息列完整性及非空审核", "部件状态列存在性及互斥性审核", "PPAP完成状态逻辑一致性审核")
    PRECHECK_METHODS = {"L04:2:0ec0cf78b5785b5f2f28": "rule_2"}

    @rule("部件状态列存在性及互斥性审核")
    def rule_3(self, ctx):
        keys = ("自制", "外协-国产", "外协-进口", "领用件")
        for row in data_rows(ctx, "主表", "主数据区", ("零件号", *keys)):
            row.expect_condition(keys[0], lambda _, r=row: r.count_present(*keys) == 1, context="每个业务行必须且仅有一个部件状态")
        return "PASS", ""

    @rule("PPAP完成状态逻辑一致性审核")
    def rule_4(self, ctx):
        rows = data_rows(ctx, "主表", "主数据区", ("零件号", "外协-国产", "外协-进口", "完成PPAP"), trigger="零件号")
        for row in rows:
            if row.is_present("外协-国产") or row.is_present("外协-进口"):
                row.expect_equal("完成PPAP", "Y", case_insensitive=False, compact=True, context="理想外协零件完成PPAP")
        return "PASS", ""
