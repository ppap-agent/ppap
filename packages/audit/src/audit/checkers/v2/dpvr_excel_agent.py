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
from pathlib import Path
from typing import Any

from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Table
from audit.kinds import PpapDocumentType

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
_REPORT_NUMBER_FIELD_LABELS = ("试验报告编号", "检测报告编号", "报告编号", "文件编号", "编号", "Report No", "No.")
_TEST_TYPE_FIELD_LABELS = ("试验类型", "试验阶段", "测试阶段", "项目阶段", "Test type")
_RESULT_FIELD_LABELS = (
    "结果判定",
    "判定结果",
    "判定结论",
    "检测结论",
    "结论",
    "Result judgment",
    "Conclusion",
)
_SAMPLE_QUANTITY_FIELD_LABELS = ("样品数量", "样本数量", "测试样品数量", "试验样本描述", "Sample quantity")
_SAMPLE_QUANTITY_LABEL_TOKENS = {
    "样品数量",
    "样本数量",
    "样品数",
    "样本数",
    "试验样本描述",
    "数量",
}
_SAMPLE_QUANTITY_PROSE_HINTS = ("详见", "参见", "说明", "备注", "附件", "附页", "同上")
_LAYERED_VALUE_REJECT_LABELS = (
    *_REPORT_NUMBER_FIELD_LABELS,
    *_TEST_TYPE_FIELD_LABELS,
    *_RESULT_FIELD_LABELS,
    *_SAMPLE_QUANTITY_FIELD_LABELS,
    "样品名称",
    "试验项目",
    "试验标准",
    "试验设备",
    "设备有效日期",
    "试验室温",
    "试验人员",
    "试验地点",
    "试验起止日期",
    "样品状态描述",
    "试验方法",
    "试验要求",
    "试验数据",
    "实际试验结果",
    "试验结果",
    "判定结果",
    "编制",
    "备注",
    "页码",
    "Sample name",
    "Test item",
    "Test standard",
    "Equipment",
    "Test operator",
    "Test site",
    "Start and end date",
    "Test method",
    "Test requirements",
    "Remark",
    "Result",
    "Judgment",
    "Compiler",
    "Page",
)
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

    _dpvr_native_fields: dict[tuple[str, ...], list[str]]
    _dpvr_seal_cache: dict[Any, Any]

    DOC_TYPE = PpapDocumentType.DPVR

    def _normalize_text(self, value: Any) -> tuple[str, str]:
        raw_text = str(value or "").strip()
        compact_lower = re.sub(r"\s+", "", raw_text).lower()
        spaced_lower = re.sub(r"\s+", " ", raw_text).lower()
        return compact_lower, spaced_lower

    def _label_key(self, value: Any) -> str:
        return re.sub(r"[\s:：()（）/\\._,，;；-]+", "", str(value or "").strip().lower())

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

    def _source_pdf_path(self) -> Path | None:
        metadata = getattr(self.audit_file, "file_metadata", None)
        if not isinstance(metadata, dict):
            return None
        source_pdf = str(metadata.get("source_pdf_url", "") or "").strip()
        if not source_pdf:
            return None
        path = Path(source_pdf)
        if not path.exists() or path.suffix.lower() != ".pdf":
            return None
        return path

    def _source_pdf_text(self) -> str:
        cached = getattr(self, "_dpvr_source_pdf_text_cache", None)
        if isinstance(cached, str):
            return cached

        path = self._source_pdf_path()
        if path is None:
            self._dpvr_source_pdf_text_cache = ""
            return ""

        try:
            import fitz

            with fitz.open(str(path)) as doc:
                text = "\n".join(page.get_text("text") for page in doc)
        except Exception:
            text = ""
        self._dpvr_source_pdf_text_cache = text
        return text

    def _source_pdf_report_numbers(self) -> list[str]:
        text = self._source_pdf_text()
        if not text:
            return []
        matches = re.findall(
            r"(?:试验报告编号|检测报告编号|报告编号|文件编号|编号\s*(?:No\.?)?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})",
            text,
            flags=re.IGNORECASE,
        )
        return self._dedupe_values(matches)

    def _source_pdf_test_types(self) -> list[str]:
        text = self._source_pdf_text()
        if not text:
            return []
        matches = re.findall(
            r"(?:试验类型|试验阶段|测试阶段|项目阶段|Test\s*type)\s*[:：]?\s*((?:D\s*V|P\s*V)\s*(?:测试|阶段)?)",
            text,
            flags=re.IGNORECASE,
        )
        return self._dedupe_values(matches)

    def _dedupe_values(self, values: list[Any] | tuple[Any, ...]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
            if not text:
                continue
            key = self._label_key(text)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        return deduped

    def _matches_any_label(self, value: Any, labels: tuple[str, ...]) -> bool:
        key = self._label_key(value)
        if not key:
            return False
        return any(self._label_key(label) in key for label in labels if self._label_key(label))

    def _is_layered_value(self, value: Any, *, labels: tuple[str, ...]) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        reject_labels = tuple(dict.fromkeys((*labels, *_LAYERED_VALUE_REJECT_LABELS)))
        return not self._matches_any_label(text, reject_labels)

    def _layered_values_for_labels(self, table: Table, labels: tuple[str, ...]) -> list[str]:
        values: list[Any] = []
        sheets = table.find_sheets(_SHEET_NAME, on_missing=Decision.IGNORE)
        for sheet in sheets:
            for cell in sheet.scan_non_empty(
                row_min=1,
                row_max=min(sheet.ws.max_row, 180),
                col_min=1,
                col_max=min(sheet.ws.max_column, 80),
            ):
                if len(str(cell.value() or "").splitlines()) > 4 or len(str(cell.value() or "")) > 160:
                    continue
                if not self._matches_any_label(cell.value(), labels):
                    continue

                right = cell.find_right(max_offset=8)
                if right is not None and self._is_layered_value(right.value(), labels=labels):
                    values.append(right.value())

                blank_streak = 0
                for row_index in range(cell.row_index + 1, min(sheet.ws.max_row, cell.row_index + 20) + 1):
                    below = sheet.cell(row_index, cell.col_index).value()
                    if not str(below or "").strip():
                        blank_streak += 1
                        if blank_streak >= 2:
                            break
                        continue
                    blank_streak = 0
                    if not self._is_layered_value(below, labels=labels):
                        break
                    values.append(below)
        return self._dedupe_values(tuple(values)) or self._source_pdf_field_values(labels)

    def _source_pdf_field_values(self, labels: tuple[str, ...]) -> list[str]:
        """Bind native report fields to their table cell or explicit inline value.

        A converted paragraph is not a field's adjacent value. Read the original
        layout for reports whose pages were preserved as paragraph cells.
        """
        path = self._source_pdf_path()
        if path is None:
            return []
        cache = getattr(self, "_dpvr_native_fields", None)
        if cache is None:
            cache = self._dpvr_native_fields = {}
        if labels in cache:
            return cache[labels]
        import fitz

        values: list[str] = []
        inline = re.compile(r"^\s*(?:" + "|".join(re.escape(label) for label in labels) + r")\s*[:：]\s*(\S.*?)\s*$", re.IGNORECASE)
        with fitz.open(path) as document:
            for page in document:
                text = page.get_text(sort=True)
                if not self._matches_any_label(text, labels):
                    continue
                for line in text.splitlines():
                    match = inline.fullmatch(line)
                    if match:
                        values.append(match[1])
                words = page.get_text("words")
                for label in labels:
                    for anchor in page.search_for(label):
                        # Bilingual labels and their value can use different PDF
                        # text blocks. Vertical overlap binds the original row.
                        groups: dict[tuple[int, int], list[Any]] = {}
                        for word in words:
                            if word[0] < anchor.x1 + 2 or min(word[3], anchor.y1) <= max(word[1], anchor.y0):
                                continue
                            groups.setdefault((word[5], word[6]), []).append(word)
                        candidates = sorted(groups.values(), key=lambda group: min(word[0] for word in group))
                        if candidates:
                            value = " ".join(word[4] for word in sorted(candidates[0], key=lambda word: word[0]))
                            if self._is_layered_value(value, labels=labels):
                                values.append(value)
        cache[labels] = self._dedupe_values(tuple(values))
        return cache[labels]

    def _layered_report_numbers(self, table: Table) -> list[str]:
        del table
        return self._source_pdf_report_numbers()

    def _layered_test_types(self, table: Table) -> list[str]:
        return self._source_pdf_test_types() or self._layered_values_for_labels(table, _TEST_TYPE_FIELD_LABELS)

    def _layered_result_values(self, table: Table) -> list[str]:
        return self._layered_values_for_labels(table, _RESULT_FIELD_LABELS)

    def _layered_sample_quantities(self, table: Table) -> list[str]:
        return self._layered_values_for_labels(table, _SAMPLE_QUANTITY_FIELD_LABELS)

    def _layered_phase_hits(self, table: Table) -> set[str]:
        hits: set[str] = set()
        sheets = table.find_sheets(_SHEET_NAME, on_missing=Decision.IGNORE)
        for sheet in sheets:
            for cell in sheet.scan_non_empty(
                row_min=1,
                row_max=min(sheet.ws.max_row, 220),
                col_min=1,
                col_max=min(sheet.ws.max_column, 80),
            ):
                key = self._label_key(cell.value())
                if "试验前" in key or "beforethetest" in key:
                    hits.add("前")
                if "试验中" in key or "inprocess" in key:
                    hits.add("中")
                if "试验后" in key or "afterthetest" in key or "afterthetes" in key:
                    hits.add("后")
        return hits

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
            try:
                section = fluent_sheet.section(_SECTION_NAME)
            except GuardStop as stop:
                if stop.message.startswith("未定位到数据区"):
                    continue
                raise
            columns = section.columns(*required, optional=optional)
            for row in section.rows_from(columns):
                if not self._is_data_serial(row.value("序号")):
                    continue
                yield fluent_sheet, row

    @rule("报告编号检查")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        layered_values = self._layered_report_numbers(table)
        if layered_values:
            for value in layered_values:
                compact_text, _ = self._normalize_text(value)
                if self._is_label_only_value(compact_text, labels=_REPORT_NUMBER_LABEL_TOKENS):
                    return "NG", "报告编号字段不能仅命中标题/字段名"
            return "PASS", ""

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

    @rule("试验结果检查")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        layered_values = self._layered_result_values(table)
        if layered_values:
            for value in layered_values:
                if not self._rule2_is_pass(value):
                    return "NG", f"结果判定字段值不合格: {value}"
            return "PASS", ""

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

    @rule("试验类型检查")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        layered_values = self._layered_test_types(table)
        if layered_values:
            for value in layered_values:
                if self._normalize_test_type(value) not in _VALID_TEST_TYPES:
                    return "NG", f"试验类型字段值不合格: {value}"
            return "PASS", ""

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

    @rule("印章检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        path = self._source_pdf_path()
        if path is not None:
            import fitz

            from audit.checkers.v4.visual_facts import read_original_pdf_visual_facts

            with fitz.open(path) as document:
                count = len(document)
            # Presence needs one located witness. Unexamined pages can never
            # establish absence, so a negative partial observation stays unknown.
            pages = sorted({1, count}) if count else []
            original = self.audit_file.model_copy(update={"file_url": str(path)})
            cache = getattr(self, "_dpvr_seal_cache", None)
            if cache is None:
                cache = self._dpvr_seal_cache = {}
            sources = []
            facts = read_original_pdf_visual_facts(
                original,
                {
                    "seal": (
                        "原件中是否存在实际印章。本条仅判断是否存在，不要求完整圆形、完整章名、日期或鉴真。"
                        "被页面裁切后仍可见的印章弧线及沿弧排列的文字，足以确认印章存在。"
                        "单独的红色线条、公司标识或印章字段文字不能代替实际印章。"
                    )
                },
                lambda locator, excerpt, **kwargs: sources.append(locator),
                operation="dpvr_seal",
                cache=cache,
                source_pages=pages,
            )
            ctx.record_execution()
            if facts["seal"] is True:
                located = sorted({item["page"] for item in sources if type(item.get("page")) is int and (item.get("observed_value") is True or item.get("element") == "seal")})
                location = "第" + "、".join(map(str, located)) + "页" if located else "中"
                return "PASS", f"原PDF{location}检测到实际印章。"
            if facts["seal"] is False and len(pages) == count:
                return "NG", "完整原PDF中未检测到印章。"
            return "UNABLE", "原PDF印章尚不能确认，请核对原件。"
        table = Table(ctx)
        for fluent_sheet in table.find_sheets(_SHEET_NAME):
            if fluent_sheet.has_seal():
                return "PASS", f"工作表'{fluent_sheet.title}'检测到印章"
        return "NG", "未检测到可验证的印章证据"

    @rule("试验阶段图片检查")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        phase_hits = self._layered_phase_hits(table)
        if phase_hits:
            missing = [phase for phase in ("前", "中", "后") if phase not in phase_hits]
            if missing:
                return "NG", f"试验报告缺少试验{','.join(missing)}阶段图片证据"
            return "PASS", "试验报告包含试验前、中、后阶段图片证据"

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

    @rule("样品数量检查")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        table = Table(ctx)
        layered_values = self._layered_sample_quantities(table)
        if layered_values:
            for value in layered_values:
                if not self._is_valid_sample_quantity(value):
                    return "NG", f"样品数量字段值不合格: {value}"
            return "PASS", ""

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
