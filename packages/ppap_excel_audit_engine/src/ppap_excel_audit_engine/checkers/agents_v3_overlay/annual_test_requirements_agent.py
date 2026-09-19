"""零部件年度试验要求 Excel 审核代理。"""

from __future__ import annotations

import logging
import re
from typing import Tuple

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import RuleContext, Table

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

_ALLOWED_QUALITY_LEVELS = {"关键件", "重要件", "一般件"}
_STRICT_BIENNIAL_ITEMS = {"ELV试验", "禁限用", "EMC试验"}


def _compact_text(value: object) -> str:
    return "".join(str(value or "").split())


def _is_quality_level_allowed(value: object) -> bool:
    return _compact_text(value) in _ALLOWED_QUALITY_LEVELS


def _is_supplier_name_valid(value: object) -> bool:
    supplier_name = _compact_text(value)
    return bool(supplier_name) and bool(re.match(r"^[A-Za-z0-9]+-.+$", supplier_name))


def _contains_number(value: object) -> bool:
    return bool(re.search(r"\d+", _compact_text(value)))


class AnnualTestRequirementsAgent(ExcelAgent):
    """零部件年度试验要求专业审核代理。"""

    DOC_TYPE = PpapDocumentType.ANNUAL_TEST_REQUIREMENTS
    EXPECTED_RULE_CATEGORIES = (
        "质量等级字段识别",
        "试验项目与测试频率一致性",
        "供应商名称格式审核",
        "样本数量列及内容识别",
        "试验要求栏完整性审核",
        "车型字段存在性及内容审核",
    )

    @rule("质量等级字段识别")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("质量等级区")
            quality_level = section.first_value("质量等级值", compact=True)
            if not quality_level:
                return "NG", f"工作表[{sheet.title}]未找到质量等级值"
            if not _is_quality_level_allowed(quality_level):
                return "NG", f"工作表[{sheet.title}]质量等级值不符合要求: {quality_level}"
        return "PASS", ""

    @rule("试验项目与测试频率一致性")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("试验频率区")
            columns = section.columns("频率-试验项目", "频率-测试频率")
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]试验频率区为空表"

            effective_rows = 0
            for row in rows:
                if row.count_present("频率-试验项目", "频率-测试频率") == 0:
                    continue
                effective_rows += 1
                row.expect(
                    "频率-试验项目",
                    "频率-测试频率",
                    context="试验频率",
                )
                test_item = _compact_text(row.value("频率-试验项目"))
                if test_item in _STRICT_BIENNIAL_ITEMS:
                    row.expect_equal(
                        "频率-测试频率",
                        "每两年",
                        case_insensitive=False,
                        compact=True,
                        context=f"试验项目[{test_item}]测试频率必须等于每两年",
                    )
                    continue
                row.expect_condition(
                    "频率-测试频率",
                    lambda value: _compact_text(value) in {"每年", "每半年"},
                    context=f"试验项目[{test_item}]测试频率必须等于每年或每半年",
                )
            if effective_rows == 0:
                return "NG", f"工作表[{sheet.title}]试验频率区未识别到有效数据行"
        return "PASS", ""

    @rule("供应商名称格式审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("供应商区")
            supplier_name = section.first_value("供应商代码全称值", compact=True)
            if not supplier_name:
                return "NG", f"工作表[{sheet.title}]未找到供应商代码全称值"
            if not _is_supplier_name_valid(supplier_name):
                return "NG", f"工作表[{sheet.title}]供应商代码全称值格式不符合要求: {supplier_name}"
        return "PASS", ""

    @rule("样本数量列及内容识别")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("样本数量区")
            columns = section.columns("样本数量值")
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]样本数量区为空表"

            effective_rows = 0
            for row in rows:
                if not row.is_present("样本数量值"):
                    continue
                effective_rows += 1
                row.expect_condition(
                    "样本数量值",
                    _contains_number,
                    context="样本数量值必须包含数字",
                )
            if effective_rows == 0:
                return "NG", f"工作表[{sheet.title}]样本数量区未识别到有效数据行"
        return "PASS", ""

    @rule("试验要求栏完整性审核")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("试验要求区")
            columns = section.columns("要求-试验项目", "要求-试验要求", "要求-试验标准方法")
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]试验要求区为空表"

            effective_rows = 0
            for row in rows:
                if row.count_present("要求-试验项目", "要求-试验要求", "要求-试验标准方法") == 0:
                    continue
                effective_rows += 1
                row.expect(
                    "要求-试验项目",
                    "要求-试验要求",
                    "要求-试验标准方法",
                    context="试验要求",
                )
            if effective_rows == 0:
                return "NG", f"工作表[{sheet.title}]试验要求区未识别到有效数据行"
        return "PASS", ""

    @rule("车型字段存在性及内容审核")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("车型区")
            columns = section.columns("车型值")
            rows = list(section.rows_from(columns))
            if not rows:
                return "NG", f"工作表[{sheet.title}]车型区为空表"

            effective_rows = [row for row in rows if row.is_present("车型值")]
            if not effective_rows:
                return "NG", f"工作表[{sheet.title}]未找到车型值"

            for row in effective_rows:
                row.expect_condition(
                    "车型值",
                    lambda value: bool(_compact_text(value)),
                    context="车型值不能为空",
                )
        return "PASS", ""
