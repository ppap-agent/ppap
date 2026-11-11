"""
检具验收报告 (Checking Fixture Approval Report) 审核代理

规则1：文件完整性检查
规则2：标题前缀检查
规则3：不合格项与结论关联检查
"""

from __future__ import annotations

import logging
import re
from typing import Any, Tuple

from audit.engine.rule_engine import RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

_NEGATIVE_KEYWORDS = ("不合格", "未完成", "非完成")
_SELECTED_MARKS = ("√", "✔", "☑", "✅")
_RESPONSIBLE_PERSON_PATTERN = re.compile(
    r"检具(?:认可)?负责人\s*[:：]?\s*([^\s_,，。;；:：]{2,40})",
    re.IGNORECASE,
)
_RESPONSIBLE_PLACEHOLDERS = {"负责人", "姓名", "签名", "name", "none", "nan"}
_CONCLUSION_MISSING_TOKENS = {"", "na", "n/a", "none", "nan"}


class CheckingFixtureApprovalExcelAgent(ExcelAgent):
    """检具验收报告审核代理（规则1~3）。"""

    DOC_TYPE = PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT

    def _contains_negative_checked_mark(self, value: Any) -> bool:
        text = str(value or "").lower()
        return bool(text) and any(keyword in text for keyword in _NEGATIVE_KEYWORDS) and any(marker in text for marker in _SELECTED_MARKS)

    def _conclusion_candidates(self, block: Any) -> list[str] | None:
        if block.missing_fields("结论字段"):
            return None

        return [
            str(block.value("结论值字段", compact=True)),
            str(block.value("结论下一行字段", compact=True)),
            str(block.value("结论下一行右侧字段", compact=True)),
        ]

    def _has_responsible_evidence(self, block: Any) -> bool:
        dedicated_field = block.field("检具认可负责人字段")
        if dedicated_field.exists():
            return bool(dedicated_field.has_business_value())
        narrative = str(block.value("检具负责人叙述字段") or "")
        match = _RESPONSIBLE_PERSON_PATTERN.search(narrative)
        if match is None:
            return False
        person = match.group(1).strip().lower()
        return person not in _RESPONSIBLE_PLACEHOLDERS

    def _normalized_conclusion_text(
        self,
        candidates: list[str],
        *,
        reject_missing_tokens: bool = False,
    ) -> str:
        merged_text = "".join(value for value in candidates if value)
        normalized = re.sub(
            r"[（）()]+|认可状态及后续是否有行动措施",
            "",
            merged_text,
        ).strip()
        if reject_missing_tokens and normalized.lower() in _CONCLUSION_MISSING_TOKENS:
            return ""
        return normalized

    @rule("文件完整性检查 (Completeness Check)")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        block = table.field_block("检具认可报告字段区")
        required_report_fields: tuple[str, ...] = (
            "零件号字段",
            "零件名字段",
            "GD&T图号字段",
            "产品图纸更改级别字段",
            "总成号字段",
            "年型字段",
            "设计方字段",
            "制造方字段",
            "检具号字段",
            "三坐标检测机构字段",
        )
        has_dedicated_responsible_field = block.exists("检具认可负责人字段")
        if has_dedicated_responsible_field:
            required_report_fields += ("检具认可负责人字段",)
        required_report_fields += ("日期字段",)
        block.expect(
            *required_report_fields,
            context="检具认可报告字段区",
        )
        if not has_dedicated_responsible_field:
            block.expect_condition(
                "检具负责人叙述字段",
                lambda _value: self._has_responsible_evidence(block),
                context="检具认可负责人字段或核查叙述",
            )

        section = table.sheet("检具认可报告").section("所附文件数据区")
        columns = section.columns("所附文件项", optional=("所附文件状态",))
        has_attachment = False
        for row in section.rows_from(columns):
            if not row.is_present("所附文件项"):
                continue
            if row.is_header() and row.exact("所附文件项", "所附文件", case_insensitive=False, compact=True):
                continue
            has_attachment = True
            if "所附文件状态" in columns:
                status_value = row.value("所附文件状态", compact=True)
            else:
                right_cell = row.find_right(
                    "所附文件项",
                    max_offset=0,
                    use_merged_anchor=True,
                )
                status_value = right_cell.value(compact=True) if right_cell else ""
            if not status_value:
                attachment_name = row.value("所附文件项", compact=True)
                return "NG", f"所附文件 '{attachment_name}' 的状态为空"
        if not has_attachment:
            return "NG", "所附文件列表为空"

        conclusion_candidates = self._conclusion_candidates(block)
        if conclusion_candidates is None:
            return "NG", "未找到检具认可结论字段"
        conclusion_value = self._normalized_conclusion_text(
            conclusion_candidates,
            reject_missing_tokens=True,
        )
        if not conclusion_value:
            return "NG", "检具认可结论为空"

        list_section = table.sheet("检具清单").section("检具清单数据区")
        list_columns = list_section.columns("序号", "零件号", "零件名称", "检具号", "检具名称", "检具数量", optional=("备注",))
        for row in list_section.rows_from(list_columns):
            if not row.is_present("序号"):
                break
            row.expect("零件号", "零件名称", "检具号", "检具名称", "检具数量", context="检具清单序号")

        return "PASS", ""

    @rule("前缀检查 (Prefix Check)")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        fixture_list_sheet = table.sheet("检具清单")

        prefix_pattern = re.compile(r"[A-Za-z]{1,5}\d{1,4}项目检具清单")
        title_cell = fixture_list_sheet.find_label(
            (("项目检具清单",),),
            row_max=12,
            col_max=12,
        )
        if title_cell is None:
            return "NG", "未找到'项目检具清单'标题"

        title_text = title_cell.value(compact=True)
        if prefix_pattern.search(title_text):
            return "PASS", ""

        row_parts = [title_text]
        for candidate_cell in title_cell.scan_nearby(row_span=0, col_span=12):
            row_parts.append(candidate_cell.value(compact=True))
        if prefix_pattern.search("".join(row_parts)):
            return "PASS", ""

        return "NG", "'项目检具清单'标题中未包含车型代号前缀"

    @rule("不合格项与结论的关联性检查 (Correlation Check)")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        block = table.field_block("检具认可报告字段区")

        section = table.sheet("检具认可报告").section("所附文件数据区")
        columns = section.columns("所附文件项", optional=("所附文件状态",))
        has_attachment = False
        non_conforming_found = False
        for row in section.rows_from(columns):
            if not row.is_present("所附文件项"):
                continue
            if row.is_header() and row.exact("所附文件项", "所附文件", case_insensitive=False, compact=True):
                continue
            has_attachment = True
            if "所附文件状态" in columns:
                status_value = row.value("所附文件状态", compact=True)
            else:
                right_cell = row.find_right(
                    "所附文件项",
                    max_offset=0,
                    use_merged_anchor=True,
                )
                status_value = right_cell.value(compact=True) if right_cell else ""
            if self._contains_negative_checked_mark(status_value):
                non_conforming_found = True
                break
        if not has_attachment:
            return "NG", "'所附文件' 区域缺失"

        if not non_conforming_found:
            return "PASS", ""

        conclusion_candidates = self._conclusion_candidates(block)
        if conclusion_candidates is None:
            return "NG", "'结论' 字段不能为空，因存在不合格/未完成项"

        normalized_conclusion = self._normalized_conclusion_text(conclusion_candidates)
        if not normalized_conclusion:
            return "NG", "'结论' 字段不能为空，因存在不合格/未完成项"

        return "PASS", ""
