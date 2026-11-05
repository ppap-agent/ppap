"""L14 requires exact finite arithmetic, including OCR-derived tables."""

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from audit.engine.rule_engine import Decision, GuardStop, Row, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, columns_present, number, precheck_fact, sheet_present

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT)
assert _BASE is not None
_SECTIONS = dict(_BASE.sections)
_SECTIONS["产能数据区"] = _SECTIONS["产能数据区"].model_copy(
    update={
        "header_anchors": (
            "线体/工序类别",
            "参与测试线体数量",
            "SOP+3达成风险",
            "良率",
            "合格品数",
            "产出数量",
            "SOP+1达成风险",
            "SOP+2达成风险",
            "线体/工序预估月度产能",
            "单线体/工序总产出JPH",
            "现有线体数",
            "班次/天",
            "每班净利用时间（h）",
            "每月标准工作天数",
            "累积良率",
        ),
        "row_anchors": ("线体/工序类别", "参与测试线体数量", "SOP+3达成风险", "良率", "合格品数", "产出数量", "SOP+1达成风险", "SOP+2达成风险", "线体/工序预估月度产能", "单线体/工序总产出JPH"),
    }
)
_RESOURCE = replace(_BASE, sections=MappingProxyType(_SECTIONS))


class LiAutoPlantCapacityAgent(NativeFacts, PlantCapacityEvaluationAgent):
    EXPECTED_RULE_CATEGORIES = ("基础字段完整性与内容校验", "线体工序数据完整性校验", "风险评估与措施一致性校验", "良率计算逻辑校验", "单线体总产出JPH计算逻辑校验", "预估月度产能计算逻辑校验")
    PRECHECK_METHODS = {"L14:1": "rule_1", "L14:2": "rule_2"}

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("风险评估与措施一致性校验")
    def rule_3(self, ctx):
        keys = ("SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险")
        rows = self._calculated_rows(ctx, keys)
        if any(str(row.value(key)).strip() in {"中风险", "高风险"} for row in rows for key in keys):
            block = Table(ctx).field_block("头部字段区")
            block.expect("产能评估总体结论", "后续措施", context="中高风险处理")
            if not any(v in str(block.value("产能评估总体结论")) for v in ("条件通过", "未通过")):
                return "NG", "中高风险的总体结论必须包含条件通过或未通过。"
        return "PASS", "风险与处理措施已核对。"

    def _calculated_rows(self, ctx, names):
        result = []
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("产能数据区")
            columns = section.columns(*names, optional=() if "线体/工序类别" in names else ("线体/工序类别",))
            for index in range(max(column.data_start_row for column in columns.values()), sheet.ws.max_row + 1):
                if any(sheet.ws.cell(index, column.header_col).value is not None for column in columns.values()):
                    result.append(Row(ctx, sheet.ws, columns, index, section.resolved))
        ctx.note_scan(yielded=len(result), finished=True)
        if not result:
            raise GuardStop(Decision.UNABLE, "没有可核实的产能业务行。")
        return result

    @staticmethod
    def _ratio(value):
        text = str(value).strip()
        return number(text[:-1]) / number(100) if text.endswith("%") else number(value)

    @rule("良率计算逻辑校验")
    def rule_4(self, ctx):
        for row in self._calculated_rows(ctx, ("良率", "合格品数", "产出数量")):
            actual = self._ratio(row.value("良率"))
            qualified, output = (number(row.value(key)) for key in ("合格品数", "产出数量"))
            if output == 0:
                return "UNABLE", "产出数量为0，尚不能计算良率。"
            if abs(actual - qualified / output) > number("1e-10"):
                return "NG", "良率与合格品数除以产出数量不一致。"
        return "PASS", ""

    @rule("单线体总产出JPH计算逻辑校验")
    def rule_5(self, ctx):
        keys = ("单线体/工序总产出JPH", "产出数量", "净利用时间（h）", "参与测试线体数量")
        for row in self._calculated_rows(ctx, keys):
            actual, output, duration, lines = (number(row.value(key)) for key in keys)
            if duration == 0 or lines == 0:
                return "UNABLE", "净利用时间或参与测试线体数量为0，尚不能计算JPH。"
            if abs(actual - output / duration / lines) > number("1e-10"):
                return "NG", "JPH与产出数量除以净利用时间及测试线体数量不一致。"
        return "PASS", ""

    @rule("预估月度产能计算逻辑校验")
    def rule_6(self, ctx):
        keys = ("线体/工序预估月度产能", "单线体/工序总产出JPH", "现有线体数", "班次/天", "每班净利用时间（h）", "每月标准工作天数", "累积良率")
        for row in self._calculated_rows(ctx, keys):
            actual = number(row.value(keys[0]))
            factors = [self._ratio(row.value(key)) if key == "累积良率" else number(row.value(key)) for key in keys[1:]]
            expected = number(1)
            for factor in factors:
                expected *= factor
            if abs(actual - expected) > number("1e-10"):
                return "NG", "月度产能与源条款各项乘积不一致。"
        return "PASS", ""

    def _precheck_risk_structure(self, ctx):
        columns_present(ctx, "主表", "产能数据区", ("SOP+1达成风险", "SOP+2达成风险", "SOP+3达成风险"))
        block = Table(ctx).field_block("头部字段区")
        block.field("产能评估总体结论")
        block.field("后续措施")
        block.value("产能评估总体结论")
        block.value("后续措施")

    def precheck(self):
        return [
            precheck_fact(self, "L14:S1", lambda ctx: sheet_present(ctx, "主表"), code="B05"),
            precheck_fact(self, "L14:S2", self.rule_1),
            precheck_fact(self, "L14:S3", self.rule_2),
            precheck_fact(self, "L14:S4", self._precheck_risk_structure, code="B05"),
            precheck_fact(self, "L14:S5", self.rule_3, code="W06"),
            precheck_fact(self, "L14:S6", self.rule_4, code="B06"),
            precheck_fact(self, "L14:S7", self.rule_5, code="B06"),
            precheck_fact(self, "L14:S8", self.rule_6, code="B06"),
        ]
