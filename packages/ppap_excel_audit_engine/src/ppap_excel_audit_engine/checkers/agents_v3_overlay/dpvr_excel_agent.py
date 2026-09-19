"""
零部件设计验证计划和报告(DVP&R)审核代理
审核规则：
1.无论是供应商自己做的报告还是三方机构做的报告，识别是否有“文件编号”或“报告编号”或“编号”或“试验报告编号”字段，如果没有，则该项结果判定为NG；如果有则继续判定字段对应的值是否为空，如果为空，则该项结果判定为NG
2.实验的判定或结论必须为符合或通过或P或pass；若为F，则不通过
3.识别是否有"试验类型"字段，若有，则该字段对应的值必须为DV或PV
4.必须有印章
5.试验报告中必须有试验前，试验中，试验后的图片
6.试验报告中必须有样品数量或样本数量列，并且该列的值不为空
"""

import re
from collections.abc import Iterator
from typing import Any

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import RuleContext, Table

from .excel_agent import ExcelAgent, rule

_SHEET_NAME = "DPVR工作表"
_SECTION_NAME = "DPVR主数据区"
_VALID_TEST_TYPES = {"DV", "PV"}
_SECTION_HEADER_PATTERN = re.compile(r"^[一二三四五六七八九十百千万\d]+、")
_EN_PASS_TOKEN_PATTERN = re.compile(r"\b(?:p|pass)\b", flags=re.IGNORECASE)
_EN_HARD_NEGATIVE_PATTERN = re.compile(r"\b(?:f|fail|ng)\b", flags=re.IGNORECASE)
_CN_POSITIVE_TOKENS = ("合格", "符合", "通过")
_CN_NEUTRAL_TOKENS = ("不做判定",)
_CN_HARD_NEGATIVE_TOKENS = ("不合格", "未通过", "不通过")
_REPORT_NUMBER_LABEL_TOKENS = {
    "试验报告编号",
    "检测报告编号",
    "报告编号",
    "文件编号",
    "编号",
    "reportno",
    "reportnumber",
}
_SAMPLE_QUANTITY_LABEL_TOKENS = {
    "样品数量",
    "样本数量",
    "样品数",
    "样本数",
    "试验样本描述",
    "数量",
}
_SAMPLE_QUANTITY_PROSE_HINTS = ("详见", "参见", "说明", "备注", "附件", "附页", "同上")
_TEST_TYPE_NORMALIZATION = {
    "DV": "DV",
    "PV": "PV",
    "DV测试": "DV",
    "DV阶段": "DV",
    "PV测试": "PV",
    "PV阶段": "PV",
}


class DpvrExcelAgent(ExcelAgent):
    """零部件设计验证计划和报告(DVP&R)审核代理"""

    DOC_TYPE = PpapDocumentType.DPVR
    EXPECTED_RULE_CATEGORIES = (
        "文件编号审核",
        "试验结论审核",
        "试验类型审核",
        "印章审核",
        "试验图片审核",
        "样品数量审核",
    )

    def _normalize_text(self, value: Any) -> tuple[str, str]:
        raw_text = str(value or "").strip()
        compact_lower = re.sub(r"\s+", "", raw_text).lower()
        spaced_lower = re.sub(r"\s+", " ", raw_text).lower()
        return compact_lower, spaced_lower

    def _is_label_only_value(self, compact_text: str, *, labels: set[str]) -> bool:
        if compact_text.rstrip(":：") in labels:
            return True
        return False

    def _is_not_label_only_value(self, value: Any, *, labels: set[str]) -> bool:
        compact_text, _ = self._normalize_text(value)
        return not self._is_label_only_value(compact_text, labels=labels)

    def _rule2_is_pass(self, value: Any) -> bool:
        compact_text, spaced_text = self._normalize_text(value)
        has_hard_negative = any(token in compact_text for token in _CN_HARD_NEGATIVE_TOKENS) or bool(_EN_HARD_NEGATIVE_PATTERN.search(spaced_text))
        if has_hard_negative:
            return False

        has_positive = any(token in compact_text for token in _CN_POSITIVE_TOKENS) or bool(_EN_PASS_TOKEN_PATTERN.search(spaced_text))
        has_neutral = any(token in compact_text for token in _CN_NEUTRAL_TOKENS)
        if has_positive:
            return True
        if has_neutral:
            return False
        return False

    def _normalize_test_type(self, value: Any) -> str:
        compact_text, _ = self._normalize_text(value)
        return _TEST_TYPE_NORMALIZATION.get(compact_text.upper(), "")

    def _is_valid_sample_quantity(self, value: Any) -> bool:
        compact_text, _ = self._normalize_text(value)
        if self._is_label_only_value(compact_text, labels=_SAMPLE_QUANTITY_LABEL_TOKENS):
            return False
        if any(hint in compact_text for hint in _SAMPLE_QUANTITY_PROSE_HINTS) and not re.search(r"\d", compact_text):
            return False
        return True

    def _is_section_header_serial(self, serial_value: Any) -> bool:
        compact_text = re.sub(r"\s+", "", str(serial_value or "").strip())
        return bool(compact_text and _SECTION_HEADER_PATTERN.match(compact_text))

    def _is_data_serial(self, serial_value: Any) -> bool:
        compact_text = re.sub(r"\s+", "", str(serial_value or "").strip())
        if not compact_text:
            return False
        if self._is_section_header_serial(compact_text):
            return False
        return bool(re.fullmatch(r"\d+(?:\.\d+)?", compact_text))

    def _iter_main_rows(self, table: Table, *, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> Iterator[Any]:
        for fluent_sheet in table.find_sheets(_SHEET_NAME):
            section = fluent_sheet.section(_SECTION_NAME)
            columns = section.columns(*required, optional=optional)
            for row in section.rows_from(columns):
                if not self._is_data_serial(row.value("序号")):
                    continue
                yield fluent_sheet, row

    @rule("文件编号审核")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        rows_with_sheet = list(self._iter_main_rows(table, required=("序号", "试验报告编号")))
        if not rows_with_sheet:
            return "NG", "未找到有效数据行"

        for _sheet, row in rows_with_sheet:
            row.expect("试验报告编号", context="报告编号")
            row.expect_condition(
                "试验报告编号",
                lambda value: self._is_not_label_only_value(value, labels=_REPORT_NUMBER_LABEL_TOKENS),
                context="报告编号字段不能仅命中标题/字段名",
            )
        return "PASS", ""

    @rule("试验结论审核")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        rows_with_sheet = list(self._iter_main_rows(table, required=("序号", "实际试验结果")))
        if not rows_with_sheet:
            return "NG", "未找到有效数据行"

        for _sheet, row in rows_with_sheet:
            row.expect("实际试验结果", context="试验结果")
            row.expect_condition(
                "实际试验结果",
                self._rule2_is_pass,
                context="同一单元格内命中硬否定词判NG；其余需命中合格/符合/通过/P/PASS（可与“不做判定”并存）",
            )
        return "PASS", ""

    @rule("试验类型审核")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        rows_with_sheet = list(self._iter_main_rows(table, required=("序号", "试验类型")))
        if not rows_with_sheet:
            return "NG", "未找到有效数据行"

        for _sheet, row in rows_with_sheet:
            row.expect("试验类型", context="试验类型")
            row.expect_condition(
                "试验类型",
                lambda value: self._normalize_test_type(value) in _VALID_TEST_TYPES,
                context="试验类型标准化后必须是DV/PV（允许DV测试/DV阶段/PV测试/PV阶段）",
            )
        return "PASS", ""

    @rule("印章审核")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        for fluent_sheet in table.find_sheets(_SHEET_NAME):
            if fluent_sheet.has_seal():
                return "PASS", f"工作表'{fluent_sheet.title}'检测到印章"
        return "NG", "未检测到可验证的印章证据"

    @rule("试验图片审核")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets(_SHEET_NAME)
        rows_by_sheet: dict[str, list[Any]] = {}
        for fluent_sheet, row in self._iter_main_rows(
            table,
            required=("序号",),
            optional=("试验前图片", "试验中图片", "试验后图片"),
        ):
            rows_by_sheet.setdefault(fluent_sheet.title, []).append(row)

        phase_columns = (("前", "试验前图片"), ("中", "试验中图片"), ("后", "试验后图片"))
        failed_sheets: list[str] = []
        matched_sheets = 0

        for fluent_sheet in sheets:
            rows = rows_by_sheet.get(fluent_sheet.title, [])
            if not rows:
                failed_sheets.append(f"工作表'{fluent_sheet.title}'：未找到有效数据行")
                continue

            matched_sheets += 1
            found = {"前": False, "中": False, "后": False}
            for row in rows:
                for phase, column_name in phase_columns:
                    if column_name in row.columns and row.is_present(column_name):
                        found[phase] = True

            missing = [phase for phase, present in found.items() if not present]
            if missing:
                failed_sheets.append(f"工作表'{fluent_sheet.title}'：缺少试验{','.join(missing)}阶段图片")

        if matched_sheets == 0:
            return "NG", "未找到有效工作表"
        if failed_sheets:
            return "NG", "；".join(failed_sheets)
        return "PASS", f"所有{matched_sheets}个工作表的试验阶段图片检查均通过"

    @rule("样品数量审核")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets(_SHEET_NAME)
        rows_by_sheet: dict[str, list[Any]] = {}
        for fluent_sheet, row in self._iter_main_rows(
            table,
            required=("序号",),
            optional=("试验样本描述", "数量"),
        ):
            rows_by_sheet.setdefault(fluent_sheet.title, []).append(row)

        failed_sheets: list[str] = []
        matched_sheets = 0
        for fluent_sheet in sheets:
            rows = rows_by_sheet.get(fluent_sheet.title, [])
            if not rows:
                failed_sheets.append(f"工作表'{fluent_sheet.title}'：未找到有效数据行")
                continue

            matched_sheets += 1
            available_columns = {column_name for row in rows for column_name in row.columns}
            sample_column = "试验样本描述" if "试验样本描述" in available_columns else "数量" if "数量" in available_columns else ""
            if not sample_column:
                failed_sheets.append(f"工作表'{fluent_sheet.title}'：未找到样品数量相关列")
                continue

            for row in rows:
                row.expect(sample_column, context="样品数量")
                row.expect_condition(
                    sample_column,
                    self._is_valid_sample_quantity,
                    context="样品/样本数量字段需非空，且不能是标题或说明性文本",
                )

        if matched_sheets == 0:
            return "NG", "未找到有效工作表"
        if failed_sheets:
            return "NG", "；".join(failed_sheets)
        return "PASS", f"所有{matched_sheets}个工作表的样品数量检查均通过"
