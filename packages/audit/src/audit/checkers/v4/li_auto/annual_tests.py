"""L16 samples are required for each test item, not only nonempty sample cells."""

import re
from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v3.annual_test_requirements_agent import AnnualTestRequirementsAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, Row, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, compact, precheck_fact

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.ANNUAL_TEST_REQUIREMENTS)
assert _BASE is not None
_COLUMNS = dict(_BASE.columns)
_COLUMNS["要求-样本数量"] = _COLUMNS["样本数量值"].model_copy(update={"canonical_name": "要求-样本数量", "section_name": "试验要求区"})
for _name, _source in (("样本-试验项目", "要求-试验项目"), ("样本-试验要求", "要求-试验要求"), ("样本-标准方法", "要求-试验标准方法")):
    _COLUMNS[_name] = _COLUMNS[_source].model_copy(update={"canonical_name": _name, "section_name": "样本数量区"})
_RESOURCE = replace(_BASE, columns=MappingProxyType(_COLUMNS))


class LiAutoAnnualTestsAgent(NativeFacts, AnnualTestRequirementsAgent):
    EXPECTED_RULE_CATEGORIES = ("质量等级字段识别", "试验项目与测试频率一致性", "供应商名称格式审核", "样本数量列及内容识别", "试验要求栏完整性审核", "车型字段存在性及内容审核")
    PRECHECK_METHODS = {"L16:5": "rule_5", "L16:6": "rule_6"}

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @staticmethod
    def _metadata_values(ctx, section_name, field):
        values = []
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section(section_name)
            columns = section.columns(field)
            # Consume the bounded section before making a metadata claim;
            # first_value closes an unfinished scan after its first yield.
            for row in list(section.rows_from(columns)):
                if row.is_present(field):
                    values.append(compact(row.value(field)))
        if len(set(values)) > 1:
            raise GuardStop(Decision.UNABLE, "同一元信息字段有多个不同取值，需确认实际归属。")
        return values

    @rule("质量等级字段识别")
    def rule_1(self, ctx):
        values = self._metadata_values(ctx, "质量等级区", "质量等级值")
        return ("PASS", "") if values and values[0] in {"关键件", "重要件", "一般件"} else ("NG", "质量等级未填写或不在关键件、重要件、一般件集合内。")

    @rule("供应商名称格式审核")
    def rule_3(self, ctx):
        values = self._metadata_values(ctx, "供应商区", "供应商代码全称值")
        return ("PASS", "") if values and re.fullmatch(r"[A-Za-z0-9]+-.+", values[0]) else ("NG", "供应商名称须为供应商代码-供应商全称。")

    @staticmethod
    def _annual_rows(ctx, section_name, required, *, optional=()):
        result = []
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section(section_name)
            columns = section.columns(*required, optional=optional)
            worksheet = section.resolved.ws
            count = 0
            for index in range(max(column.data_start_row for column in columns.values()), worksheet.max_row + 1):
                physical = {compact(cell.value) for cells in worksheet.iter_rows(min_row=index, max_row=index) for cell in cells if compact(cell.value)}
                if physical and physical <= {"签署栏", "试验要求栏", "试验频率", "测试频率要求"}:
                    break
                if section_name == "试验频率区" and {"试验要求", "试验标准/方法"} <= physical:
                    break
                row = Row(ctx, worksheet, columns, index, section.resolved)
                if row.is_header():
                    continue
                if any(compact(row.value(name)) for name in columns):
                    result.append(row)
                    count += 1
            ctx.note_scan(yielded=count, finished=True)
        if not result:
            raise GuardStop(Decision.UNABLE, "没有可核实的年度试验业务行，不能按空范围通过。")
        return result

    @rule("试验项目与测试频率一致性")
    def rule_2(self, ctx):
        for row in self._annual_rows(ctx, "试验频率区", ("频率-试验项目", "频率-测试频率")):
            item = compact(row.value("频率-试验项目"))
            if not item:
                return "UNABLE", "试验项目为空，尚不能确定应采用哪种频率。"
            expected = {"每两年"} if item.casefold() in {"ELV试验".casefold(), "禁限用", "EMC试验".casefold()} else {"每年", "每半年"}
            row.expect_condition("频率-测试频率", lambda value, expected=expected: compact(value) in expected, context="试验项目对应测试频率")
        return "PASS", ""

    @rule("样本数量列及内容识别")
    def rule_4(self, ctx):
        rows = self._annual_rows(ctx, "样本数量区", ("样本数量值",), optional=("样本-试验项目", "样本-试验要求", "样本-标准方法"))
        for row in rows:
            value = row.value("样本数量值")
            if not any(character.isdecimal() for character in str(value if value is not None else "")):
                return "NG", "每个已识别试验业务行的样本数量须包含阿拉伯数字。"
        return "PASS", ""

    @rule("试验要求栏完整性审核")
    def rule_5(self, ctx):
        fields = ("要求-试验项目", "要求-试验要求", "要求-试验标准方法")
        for row in self._annual_rows(ctx, "试验要求区", fields):
            row.expect(*fields, context="年度试验要求三列均须填写")
        return "PASS", ""

    def _precheck_frequency(self, ctx, biennial):
        rows = self._annual_rows(ctx, "试验频率区", ("频率-试验项目", "频率-测试频率"))
        applicable = False
        for row in rows:
            item = compact(row.value("频率-试验项目"))
            if not item:
                return "UNABLE", "试验项目未填写，不能确定频率要求。"
            if (item.casefold() in {"ELV试验".casefold(), "禁限用", "EMC试验".casefold()}) != biennial:
                continue
            applicable = True
            row.expect_condition("频率-测试频率", lambda value: compact(value) in ({"每两年"} if biennial else {"每年", "每半年"}), context="年度试验频率取值")
        ctx.mark_scope_complete()
        return ("PASS", "适用试验项目频率已核实。") if applicable else ("NA", "已核对全部业务行，没有本项适用的试验类别。")

    def precheck(self):
        return [
            precheck_fact(self, "L16:S1", self.rule_1, code="W03"),
            precheck_fact(self, "L16:S2", lambda ctx: self._precheck_frequency(ctx, True), code="W03"),
            precheck_fact(self, "L16:S3", lambda ctx: self._precheck_frequency(ctx, False), code="W03"),
            precheck_fact(self, "L16:S4", self.rule_3, code="W03"),
            precheck_fact(self, "L16:S5", self.rule_4, code="W03"),
            precheck_fact(self, "L16:S6", self.rule_5),
            precheck_fact(self, "L16:S7", self.rule_6),
        ]
