"""零部件设计验证计划和报告(DVP&R)审核代理。"""

import logging
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.checkers.date_utils import normalize_excel_date
from ppap_excel_audit_engine.rule_engine import RuleContext, Table

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

_SECTION_NAME = "DVP&R主数据区"
_HEADER_BLOCK_NAME = "DVP&R头部字段区"
_VALID_ACTUAL_RESULTS = {"合格", "视同", "偏差认可"}
_VALID_ACTUAL_RESULTS_COMPACT = {"".join(item.split()) for item in _VALID_ACTUAL_RESULTS}


class DvpRExcelAgent(ExcelAgent):
    """零部件设计验证计划和报告(DVP&R)审核代理"""

    DOC_TYPE = PpapDocumentType.DVP_R

    _SECTION_HEADER_PATTERN = re.compile(r"^[一二三四五六七八九十百千万\d]+、")

    def _is_blankish_value(self, value: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(value or "").strip()).lower()
        return normalized in {"", "nan", "none", "/", "／"}

    def _is_missing_basic(self, value: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(value or "").strip()).lower()
        return normalized in {"", "nan", "none"}

    def _parse_datetime(self, value: Any) -> date | None:
        if self._is_blankish_value(value):
            return None
        return normalize_excel_date(value)

    def _is_section_header_serial(self, serial_value: Any) -> bool:
        compact_text = re.sub(r"\s+", "", str(serial_value or "").strip())
        return bool(compact_text and self._SECTION_HEADER_PATTERN.match(compact_text))

    def _is_data_serial(self, serial_value: Any) -> bool:
        compact_text = re.sub(r"\s+", "", str(serial_value or "").strip())
        if not compact_text:
            return False
        if self._is_section_header_serial(compact_text):
            return False
        return bool(re.fullmatch(r"\d+(?:\.\d+)?", compact_text))

    def _iter_main_rows(self, table: Table, columns_required: tuple[str, ...]) -> Iterator[Any]:
        for fluent_sheet in table.find_sheets("DVP&R工作表"):
            section = fluent_sheet.section(_SECTION_NAME)
            columns = section.columns(*columns_required)
            rows = list(section.rows_from(columns))
            for row in rows:
                if not self._is_data_serial(row.value("序号")):
                    continue
                yield row

    @rule("检查版本号、修订日期等字段是否存在且不为空")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        required_fields = (
            "版本号字段",
            "修订日期字段",
            "修订部门姓名字段",
            "零件总成名称字段",
            "适用车型字段",
            "零件总成编号字段",
            "供应商名称字段",
        )
        block = Table(ctx).field_block(_HEADER_BLOCK_NAME)
        block.expect(*required_fields, context="DVP&R头部字段区")
        for field_name in required_fields:
            block.expect_condition(
                field_name,
                lambda value: not self._is_blankish_value(value),
                context=f"{field_name}必须为有效业务值",
            )
        return "PASS", ""

    @rule("检查试验项目等字段是否存在且不为空")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = (
            "序号",
            "试验项目",
            "试验要求",
            "试验标准方法",
            "试验类型",
            "完成节点",
            "试验地点",
            "数量",
            "状态",
            "外观属性",
            "计划开始",
            "计划结束",
        )
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            for column_name in columns_required:
                row.expect_condition(
                    column_name,
                    lambda value: not self._is_missing_basic(value),
                    context=f"DVP&R主数据区关键字段[{column_name}]不能为空",
                )
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""

    @rule("检查试验类型是否为PV且状态为硬模件")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = ("序号", "试验类型", "状态")
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            if row.exact("试验类型", "pv", case_insensitive=True, compact=True):
                row.expect_equal(
                    "状态",
                    "硬模件",
                    case_insensitive=False,
                    compact=True,
                    context="试验类型为PV时状态必须为硬模件",
                )
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""

    @rule("检查计划开始和结束日期")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = ("序号", "计划开始", "计划结束")
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            row.expect("计划开始", "计划结束", context="计划日期")
            row.expect_condition(
                "计划开始",
                lambda value: self._parse_datetime(value) is not None,
                context="计划开始日期格式必须可解析",
            )
            row.expect_condition(
                "计划结束",
                lambda value: self._parse_datetime(value) is not None,
                context="计划结束日期格式必须可解析",
            )
            start_date = self._parse_datetime(row.value("计划开始"))

            def ends_after_start(end_value: Any, planned_start: date | None = start_date) -> bool:
                planned_end = self._parse_datetime(end_value)
                return planned_start is not None and planned_end is not None and planned_end >= planned_start

            row.expect_condition(
                "计划结束",
                ends_after_start,
                context="计划结束日期必须晚于或等于计划开始日期",
            )
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""

    @rule("检查实际开始和结束日期")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = ("序号", "试验实际开始日期", "试验实际结束日期")
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            row.expect("试验实际开始日期", "试验实际结束日期", context="实际试验日期")
            row.expect_condition(
                "试验实际开始日期",
                lambda value: self._parse_datetime(value) is not None,
                context="试验实际开始日期格式必须可解析",
            )
            row.expect_condition(
                "试验实际结束日期",
                lambda value: self._parse_datetime(value) is not None,
                context="试验实际结束日期格式必须可解析",
            )
            start_date = self._parse_datetime(row.value("试验实际开始日期"))
            if start_date is None:
                row.expect_condition(
                    "试验实际开始日期",
                    lambda _value: False,
                    context="试验实际开始日期格式必须可解析",
                )

            def actual_ends_after_start(end_value: Any, actual_start: date | None = start_date) -> bool:
                actual_end = self._parse_datetime(end_value)
                return actual_start is not None and actual_end is not None and actual_end >= actual_start

            row.expect_condition(
                "试验实际结束日期",
                actual_ends_after_start,
                context="试验实际结束日期必须晚于或等于试验实际开始日期",
            )
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""

    @rule("检查实际试验结果")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = ("序号", "实际试验结果")
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            row.expect(
                "实际试验结果",
                context="实际试验结果",
            ).expect_condition(
                "实际试验结果",
                lambda value: "".join(str(value or "").split()) in _VALID_ACTUAL_RESULTS_COMPACT,
                context=f"实际试验结果必须属于{sorted(_VALID_ACTUAL_RESULTS)}",
            )
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""

    @rule("检查试验报告编号")
    def rule_7(self, ctx: RuleContext) -> tuple[str, str]:
        columns_required = ("序号", "试验报告编号")
        table = Table(ctx)
        checked_rows = 0
        for row in self._iter_main_rows(table, columns_required):
            checked_rows += 1
            row.expect("试验报告编号", context="试验报告编号")
        if checked_rows == 0:
            return "NG", "未找到有效数据行"
        return "PASS", ""
