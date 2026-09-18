"""项目问题清单 Excel 审核代理。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Tuple

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import RuleContext, Table

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)


def _parse_deadline_dates(raw_value: object) -> tuple[datetime, ...]:
    if isinstance(raw_value, datetime):
        return (raw_value,)
    if isinstance(raw_value, date):
        return (datetime.combine(raw_value, datetime.min.time()),)

    text = "" if raw_value is None else str(raw_value)
    matched = re.findall(r"\d{4}/\d{2}/\d{2}", text)
    return tuple(datetime.strptime(item, "%Y/%m/%d") for item in matched)


class XirAssemblyIssueAgent(ExcelAgent):
    """项目问题清单专业审核代理。"""

    DOC_TYPE = PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT
    EXPECTED_RULE_CATEGORIES = (
        "基础字段完整性审核",
        "问题关闭状态与描述关联审核",
        "整改措施要素完整性及时间有效性审核",
        "风险分类标识审核",
    )

    @rule("基础字段完整性审核")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        header = Table(ctx).field_block("头部字段区")
        required_fields = (
            "车型字段",
            "供应商名称字段",
            "零件编号字段",
            "零件名称字段",
        )

        header.expect(*required_fields, context="头部字段区")

        return "PASS", ""

    @rule("问题关闭状态与描述关联审核")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        sheet = Table(ctx).sheet("主表")
        status_specs = (
            ("状态-过程审核区", "状态-过程审核"),
            ("状态-检验体系区", "状态-检验体系"),
            ("状态-追溯体系区", "状态-追溯体系"),
            ("状态-其他专项区", "状态-其他专项"),
        )
        status_values = [sheet.section(section_name).first_value(column_name, lower=True, compact=True) for section_name, column_name in status_specs]

        if not any(value == "no" for value in status_values):
            return "PASS", ""

        issue_section = sheet.section("问题跟踪区")
        issue_columns = issue_section.columns("问题描述列")
        rows = list(issue_section.rows_from(issue_columns))
        if not rows:
            return "NG", "规则 2 审核不通过: 问题跟踪区为空表"

        if any((not row.is_header()) and row.is_present("问题描述列") for row in rows):
            return "PASS", ""
        return "NG", "规则 2 审核不通过: 问题描述列下无内容"

    @rule("整改措施要素完整性及时间有效性审核")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        issue_section = Table(ctx).sheet("主表").section("问题跟踪区")
        columns = issue_section.columns("问题描述列", "对应措施列", "时间节点列", "负责人列")
        rows = [row for row in issue_section.rows_from(columns) if not row.is_header() and row.is_present("问题描述列")]
        if not rows:
            return "PASS", ""

        now = datetime.now()
        for row in rows:
            row.expect("对应措施列", "时间节点列", "负责人列", context="问题描述")
            row.expect_condition(
                "时间节点列",
                lambda value: bool(_parse_deadline_dates(value)),
                context="时间节点必须包含至少一个YYYY/MM/DD格式日期",
            )
            row.expect_condition(
                "时间节点列",
                lambda value: all(deadline > now for deadline in _parse_deadline_dates(value)),
                context="时间节点中的所有日期必须晚于当前时间",
            )

        return "PASS", ""

    @rule("风险分类标识审核")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        issue_section = Table(ctx).sheet("主表").section("问题跟踪区")
        columns = issue_section.columns("问题描述列", "风险评估列")
        rows = [row for row in issue_section.rows_from(columns) if not row.is_header() and row.is_present("问题描述列")]
        if not rows:
            return "PASS", ""

        for row in rows:
            row.expect_condition(
                "风险评估列",
                lambda value: "".join(str(value or "").split()).lower() in {"yes", "no"},
                context="风险评估字段值必须为Yes或No",
            )

        return "PASS", ""
