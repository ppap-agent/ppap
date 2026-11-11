"""
PSW零件提交保证书Excel文件审核代理

### PSW零件提交保证书评审规则逻辑

1.识别“生产件批准交付物文件清单”表中除“零件认证相关”行之外的所有行，对应的“附件”列内容是否为空，如果不为空则通过，否则不通过
2.识别“零件提交保证书”中是否包含“重量”字段，如果不包含则不通过，否则继续识别；当“重量”对应的内容为数值，且数值保留小数点后四位则通过，否则不通过
3.识别“零件提交保证书”中是否包含印章，如果印章中文字的最后两个字为“公司”，则直接通过；否则继续识别文件中是否包含“部门章授权声明”表格，如果不包含则不通过，否则继续识别；当“部门章授权声明”表格中有公司章则通过，否则不通过
4.识别“零件提交保证书”中“供应商名称/供应商代码”的内容是否为“总公司名称/代码”这种格式
5.“经授权的供方代表签字”和“盖章”缺一不可
6.当“零件名称”、“理想汽车零件号”、“供方料号”、“图纸号”、“模具穴号”、“工程更改版本”、“版本日期”、“重量(kg)”、“供应商名称/供应商代码”、“街道地址”、“城市”、“省”、“国家”、“顾客名称”、“设计开发”、“采购工程”、“PTC”的内容均不为空则通过，否则不通过
7.“提交原因”区域的选项至少有一个选项被选中，或者“其他-请在下方说明”下的内容不为空则通过，否则不通过
8.识别PSW是否包含“认可情况”字段，如果不包含则不通过，否则继续识别；“认可情况”字段是否有且仅有一个选项被勾选，如果不是则不通过，否则继续识别；当“认可情况”的“批准”选项被勾选，且“提交结果”区域的选项“尺寸测量”、“外观标准”、“材料和功能试验”、“统计过程数据”均被勾选则通过，且“这些结果均满足所有图纸和规范要求”也有选项被勾选则通过，否则不通过
9.当认可情况勾选“条件批准”，则继续识别识别文件右下角日期和当前日期是否在3个月之内，在3个月内则通过，否则不通过
10.当认可情况勾选“条件批准”，且补充评论内容不为空则通过，否则不通过

"""

import logging
import re
from datetime import date, datetime
from typing import Optional, Tuple

from audit.contracts import AuditResultItem as AuditResult
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.checkers.psw_evidence_trace import (
    finalize_excel_psw_result,
)
from audit.engine.rule_engine import Cell, RuleContext, Sheet, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, RuleEvidencePhase, rule

logger = logging.getLogger(__name__)
_EMPTY_MARKERS = {"", "none", "nan"}
_RULE6_LABEL_NOISE_RAW = (
    "重量(kg)",
    "重量",
    "零件名称",
    "理想汽车零件号",
    "供方料号",
    "图纸号",
    "模具穴号",
    "工程更改版本",
    "版本日期",
    "供应商名称/供应商代码",
    "街道地址",
    "城市",
    "省",
    "国家",
    "顾客名称",
    "设计开发",
    "采购工程",
    "PTC",
    "customername",
    "streetaddress",
    "city",
    "province",
    "country",
    "dre",
    "pur",
    "tpm",
)


class PswExcelAgent(ExcelAgent):
    """PSW零件提交保证书Excel文件专用审核代理"""

    DOC_TYPE = PpapDocumentType.PSW

    def _finalize_audit_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        phase: RuleEvidencePhase,
    ) -> AuditResult:
        result = finalize_excel_psw_result(
            super()._finalize_audit_result(
                result,
                rule_no=rule_no,
                phase=phase,
            ),
            audit=self.audit_file,
            rule_no=rule_no,
            phase=phase,
        )
        field_evidence = getattr(self, "_psw_field_evidence", {}).get(rule_no)
        return result.model_copy(update={"evidence": field_evidence}) if field_evidence is not None else result

    def _extract_supplier_name_code(self, sheet: Sheet) -> str:
        section = sheet.section("PSW供应商信息区")
        columns = section.columns("供应商名称/供应商代码")
        for row in section.rows_from(columns):
            value_compact = row.value("供应商名称/供应商代码", compact=True).lower()
            if value_compact in _EMPTY_MARKERS:
                continue
            if "organizationname" in value_compact and "vendorcode" in value_compact:
                continue
            value = str(row.value("供应商名称/供应商代码", compact=True))
            return value
        return ""

    def _classify_disposition_option(
        self,
        sheet: Sheet,
        *,
        checked_row: int,
        checked_col: int,
        disposition_row: int,
    ) -> str:
        from audit.engine.checkers.excel_field_evidence import classify_psw_disposition

        return classify_psw_disposition(sheet, checked_row, checked_col, disposition_row)

    @staticmethod
    def _displayed_text(ctx: RuleContext, field) -> str:
        """A number reads as Excel shows it: “0.0000” displays 0.12 as 0.1200."""
        raw = field.value()
        resolved = field.resolved
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or resolved is None or resolved.value_row is None or resolved.value_col is None:
            return str(raw or "")
        number_format = Cell(ctx, resolved.ws, resolved.value_row, resolved.value_col).number_format().split(";", 1)[0]
        if number_format.lower() in {"", "general", "@"} or "%" in number_format or not re.search(r"[0#?]", number_format):
            return str(raw)
        decimals = re.search(r"\.([0#?]+)", number_format)
        return f"{raw:.{len(decimals.group(1)) if decimals else 0}f}"

    def _checked_controls(self, sheet: Sheet) -> list[tuple[int, int]]:
        return [(row, col) for row, col, checked in sheet.checkbox_controls() if checked]

    @staticmethod
    def _checked_glyphs(sheet: Sheet, row_min: int, row_max: int) -> list[tuple[int, int, str]]:
        """Checked glyphs written in cells, e.g. “☑批准”, with the text that follows each glyph."""
        glyphs: list[tuple[int, int, str]] = []
        for row in sheet.ws.iter_rows(min_row=row_min, max_row=row_max, max_col=min(sheet.ws.max_column, 30)):
            for cell in row:
                if cell.data_type == "e":
                    continue
                segments = re.findall(r"([☑☒✓✔√□☐])([^☑☒✓✔√□☐]*)", str(cell.value or ""))
                checked = [text for mark, text in segments if mark not in "□☐"]
                if checked:
                    sheet.ctx.note_cell(sheet.title, cell.row, cell.column)
                glyphs.extend((cell.row, cell.column, text) for text in checked)
        return glyphs

    @staticmethod
    def _glyph_option(text: str) -> str:
        compact = re.sub(r"\s+", "", text).lower()
        ordinary = re.sub(r"条件批准|conditional(?:approved)?", "", compact)
        options = [
            name
            for name, found in (
                ("conditional", "条件批准" in compact or "conditional" in compact),
                ("rejected", "拒绝" in compact or "rejected" in compact),
                ("approved", "批准" in ordinary or "approved" in ordinary),
            )
            if found
        ]
        return options[0] if len(options) == 1 else "unknown"

    def _resolve_warrant_disposition(
        self,
        sheet: Sheet,
        checked_controls: list[tuple[int, int]],
    ) -> tuple[str, list[tuple[int, int]], Optional[int]]:
        ws = sheet.ws
        disposition_cell = sheet.find_label(
            (("认可", "情况"), ("warrant", "disposition")),
            row_max=120,
            col_max=25,
        )
        sheet.ctx.record_execution()  # Evaluate the located disposition and unique selection.
        if disposition_cell is None:
            return "missing_field", [], None
        disposition_row = disposition_cell.row_index

        row_min, row_max = max(1, disposition_row - 2), min(ws.max_row, disposition_row + 3)
        disposition_checks = [(row, col) for row, col in checked_controls if row_min <= row <= row_max]
        # 单元格里写的勾选符号（如“☑批准”）与表单控件同样计为勾选。
        glyphs = [glyph for glyph in self._checked_glyphs(sheet, row_min, row_max) if glyph[:2] not in disposition_checks]
        if len(disposition_checks) + len(glyphs) != 1:
            return "invalid_selection", disposition_checks + [glyph[:2] for glyph in glyphs], disposition_row

        if glyphs:
            checked_row, checked_col, text = glyphs[0]
            option = self._glyph_option(text)
            if option != "unknown":
                sheet.ctx.note_cell(sheet.title, checked_row, checked_col)
                return option, [(checked_row, checked_col)], disposition_row
            disposition_checks = [(checked_row, checked_col)]

        checked_row, checked_col = disposition_checks[0]
        option = self._classify_disposition_option(
            sheet,
            checked_row=checked_row,
            checked_col=checked_col,
            disposition_row=disposition_row,
        )
        return option, disposition_checks, disposition_row

    @staticmethod
    def _compact_token(value: object) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @classmethod
    def _compact_label_token(cls, value: object) -> str:
        compact = cls._compact_token(value)
        return re.sub(r"[:：#()（）\\/_.\-]", "", compact)

    @classmethod
    def _build_label_tokens(cls, raw_values: tuple[str, ...]) -> set[str]:
        return {cls._compact_label_token(value) for value in raw_values if str(value).strip()}

    @classmethod
    def _is_effective_business_value(
        cls,
        value: object,
        *,
        missing_tokens: set[str],
        label_noise_tokens: set[str] | None = None,
        reject_date_like: bool = False,
    ) -> bool:
        compact = cls._compact_token(value)
        if compact in _EMPTY_MARKERS:
            return False

        text = str(value or "").strip().lower()
        if text in missing_tokens or compact in missing_tokens:
            return False

        if label_noise_tokens and cls._compact_label_token(value) in label_noise_tokens:
            return False

        if reject_date_like and re.fullmatch(r"[0-9年月日:/.-]+", compact):
            return False

        return True

    def _parse_date_from_text(self, value: object) -> date | str | None:
        """A date; "invalid" for a written year-month-day that is not a calendar date; None when no date is seen."""
        normalized_date = normalize_excel_date(value)
        if normalized_date is not None:
            return normalized_date

        text = str(value or "").replace("\n", "").replace("\r", "").strip()
        if not text:
            return None

        patterns = [
            r"(20\d{2})\D{0,3}(1[0-2]|0?[1-9])\D{0,3}(3[01]|[12]\d|0?[1-9])",
            r"(20\d{2})[./-](1[0-2]|0?[1-9])[./-](3[01]|[12]\d|0?[1-9])",
        ]
        invalid = False
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year, month, day = (int(group) for group in match.groups())
            try:
                return datetime(year, month, day).date()
            except ValueError:
                invalid = True
        return "invalid" if invalid else None

    def _extract_conditional_approval_date(self, sheet: Sheet, disposition_row: int) -> date | str | None:
        ws = sheet.ws
        candidate_values: list[str] = []
        row_min = max(1, disposition_row - 2)
        row_max = min(ws.max_row, disposition_row + 8)
        col_min = max(10, ws.max_column - 10)
        for candidate_cell in sheet.scan_non_empty(
            row_min=row_min,
            row_max=row_max,
            col_min=col_min,
            col_max=ws.max_column,
        ):
            candidate_values.append(str(candidate_cell.value() or ""))

        date_label = sheet.find_label(
            (("日期",), ("date",)),
            row_max=row_max,
            col_max=ws.max_column,
        )
        if date_label is not None:
            if date_label.row_index >= row_min and date_label.col_index >= col_min:
                right_value_cell = date_label.find_right(
                    max_offset=8,
                    use_merged_anchor=True,
                )
                if right_value_cell is not None:
                    candidate_values.append(str(right_value_cell.value() or ""))
                for candidate_cell in date_label.scan_nearby(
                    row_span=2,
                    col_span=8,
                ):
                    candidate_values.append(str(candidate_cell.value() or ""))

        invalid = False
        for value in candidate_values:
            parsed_date = self._parse_date_from_text(value)
            if isinstance(parsed_date, date):
                return parsed_date
            invalid = invalid or parsed_date == "invalid"
        return "invalid" if invalid else None

    def _extract_comments_value(self, table: Table, sheet: Sheet) -> str:
        comments_field = table.field_block("PSW主表字段区").field("补充评论字段")
        if comments_field.has_business_value():
            return str(comments_field.value(compact=True))

        label_cell = sheet.find_label(
            (("补充评论",), ("comments",)),
            row_max=120,
            col_max=25,
        )
        if label_cell is None:
            return ""

        right_value_cell = label_cell.find_right(
            max_offset=15,
            use_merged_anchor=True,
        )
        if right_value_cell is not None:
            right_value = str(right_value_cell.value() or "").replace("\n", "").replace("\r", "").strip()
            if right_value:
                return right_value

        for candidate_cell in label_cell.scan_nearby(row_span=2, col_span=16):
            value = str(candidate_cell.value() or "").replace("\n", "").replace("\r", "").strip()
            if value:
                return value
        return ""

    def _is_submission_result_all_checked(
        self,
        sheet: Sheet,
        checked_controls: list[tuple[int, int]],
    ) -> bool | None:
        from audit.engine.checkers.excel_field_evidence import psw_submission_results

        return psw_submission_results(sheet)

    @rule("附件列内容检查")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则1：识别“生产件批准交付物文件清单”表中除“零件认证相关”行之外的所有行，
        对应的“附件”列内容是否为空，如果不为空则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        section = table.sheet("PSW交付物清单").section("PSW交付物清单数据区")
        columns = section.columns("项目", "附件")
        for row in section.rows_from(columns):
            if (not row.is_present("项目")) or row.exact("项目", "零件认证相关", case_insensitive=False, compact=True):
                continue
            row.expect("附件", context="PSW交付物清单")
        logger.info("")
        return "PASS", ""

    @rule("重量字段检查")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则2：识别“零件提交保证书”中是否包含“重量”字段，如果不包含则不通过，否则继续识别；
        当“重量”对应的内容为数值，且数值保留小数点后四位则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        field_block = table.field_block("PSW主表字段区")
        field_block.expect("重量字段", context="PSW主表字段区")

        weight_field = field_block.field("重量字段")
        weight_value = self._displayed_text(ctx, weight_field).replace("\n", "").replace("\r", "").strip()

        numeric_match = re.search(r"[-+]?\d+(?:\.\d+)?", weight_value.replace(",", ""))
        if not numeric_match:
            logger.error("规则2检查未通过，“重量”对应的内容不是有效数值")
            shown_value = weight_value or "空"
            return "NG", f"重量字段非数值，识别值={shown_value}"

        number_text = numeric_match.group(0)
        decimal_part = number_text.split(".", 1)[1] if "." in number_text else ""
        if len(decimal_part) == 4:
            logger.info("")
            return "PASS", ""

        logger.error("规则2检查未通过，“重量”数值未保留小数点后四位")
        return "NG", f"重量小数位数应为4位，识别值={number_text}，小数位={len(decimal_part)}"

    @rule("印章检查")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则3：识别“零件提交保证书”中是否包含印章，如果印章中文字的最后两个字为“公司”，则直接通过；
        否则继续识别文件中是否包含“部门章授权声明”表格，如果不包含则不通过，否则继续识别；
        当“部门章授权声明”表格中有公司章则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        from audit.engine.checkers.excel_field_evidence import check_psw_excel_fields

        result = check_psw_excel_fields(self, ctx, 3)
        return result.status, result.suggestions

    @rule("供应商名称/供应商代码格式检查")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则4：识别“零件提交保证书”中“供应商名称/供应商代码”的内容是否为“总公司名称/代码”这种格式。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        supplier_value = self._extract_supplier_name_code(main_sheet)
        if not supplier_value:
            logger.error("规则4检查未通过，“供应商名称/供应商代码”对应的内容为空")
            return "NG", "供应商名称/供应商代码字段为空。"

        if "/" in supplier_value:
            parts = [part.strip() for part in supplier_value.split("/") if part.strip()]
            if len(parts) == 2:
                logger.info("")
                return "PASS", ""
        logger.error("规则4检查未通过，“供应商名称/供应商代码”内容格式不正确，应为“总公司名称/代码”格式")
        return "NG", f"供应商名称/供应商代码格式错误，识别值={supplier_value}"

    @rule("签字和盖章检查")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则5：“经授权的供方代表签字”和“盖章”缺一不可。

        Returns:
            bool: 规则是否通过
        """
        from audit.engine.checkers.excel_field_evidence import check_psw_excel_fields

        result = check_psw_excel_fields(self, ctx, 5)
        return result.status, result.suggestions

    @rule("必填字段检查")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则6：当“零件名称”、“理想汽车零件号”、“供方料号”、“图纸号”、“模具穴号”、“工程更改版本”、“版本日期”、“重量(kg)”、“供应商名称/供应商代码”、“街道地址”、“城市”、“省”、“国家”、“顾客名称”、“设计开发”、“采购工程”、“PTC”的内容均不为空则通过，否则不通过。

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        field_block = table.field_block("PSW主表字段区")
        field_missing_tokens = {field_name: {str(token).strip().lower() for token in ctx.resource.fields[field_name].missing_tokens if str(token).strip()} for field_name in ctx.resource.fields}
        failed_fields = []
        field_specs = [
            ("重量(kg)", "重量字段", (("重量(kg)",), ("weight", "kg"), ("重量",))),
            ("零件名称", "零件名称字段", (("零件名称",), ("product",))),
            ("理想汽车零件号", "理想汽车零件号字段", (("理想汽车零件号",), ("lipartnumber",))),
            ("供方料号", "供方料号字段", (("供方料号",), ("vendorpartnumber",), ("vendorpar",))),
            ("图纸号", "图纸号字段", (("图纸号",), ("drawingnumber",), ("shownondrawingnumber",))),
            ("模具穴号", "模具穴号字段", (("模具穴号",), ("toolcavity",))),
            ("工程更改版本", "工程更改版本字段", (("工程更改版本",), ("工程", "更改"), ("改版",), ("changerevision",))),
            ("版本日期", "版本日期字段", (("版本日期",), ("ecdated",))),
            ("街道地址", "街道地址字段", (("街道地址",), ("streetaddress",))),
            ("城市", "城市字段", (("城市",), ("city",))),
            ("省", "省字段", (("省",), ("province",))),
            ("国家", "国家字段", (("国家",), ("country",))),
            ("顾客名称", "顾客名称字段", (("顾客", "名称"), ("customername",), ("customer", "name"))),
            ("设计开发", "设计开发字段", (("设计开发",), ("dre",))),
            ("采购工程", "采购工程字段", (("采购工程",), ("pur",))),
            ("PTC", "PTC字段", (("ptc",), ("tpm",))),
        ]
        nearby_fallback_spans = {
            "顾客名称字段": (2, 2),
            "设计开发字段": (1, 2),
            "采购工程字段": (1, 2),
            "PTC字段": (1, 2),
        }
        label_noise_tokens = self._build_label_tokens(_RULE6_LABEL_NOISE_RAW)

        for field_name, resource_field_name, anchors in field_specs:
            field = field_block.field(resource_field_name)
            missing_tokens = field_missing_tokens.get(resource_field_name, set())
            if field.has_business_value() and self._is_effective_business_value(
                field.value(),
                missing_tokens=missing_tokens,
                label_noise_tokens=label_noise_tokens,
            ):
                continue
            label_cell = main_sheet.find_label(
                anchors,
                row_max=120,
                col_max=45,
            )
            if label_cell is not None:
                fallback_cell = label_cell.find_right(
                    max_offset=12,
                    use_merged_anchor=True,
                )
                fallback_value = ""
                if fallback_cell is not None:
                    fallback_value = str(fallback_cell.value() or "")
                if self._is_effective_business_value(
                    fallback_value,
                    missing_tokens=missing_tokens,
                    label_noise_tokens=label_noise_tokens,
                ):
                    continue

                nearby_span = nearby_fallback_spans.get(resource_field_name)
                if nearby_span is not None:
                    row_span, col_span = nearby_span
                    nearby_hit = False
                    for nearby_cell in label_cell.scan_nearby(
                        row_span=row_span,
                        col_span=col_span,
                    ):
                        if self._is_effective_business_value(
                            nearby_cell.value(),
                            missing_tokens=missing_tokens,
                            label_noise_tokens=label_noise_tokens,
                        ):
                            nearby_hit = True
                            break
                    if nearby_hit:
                        continue
            failed_fields.append(field_name)
            ctx.record_ng(f"必填字段缺失或为空：{field_name}")

        supplier_value = self._extract_supplier_name_code(main_sheet)
        if not supplier_value:
            failed_fields.append("供应商名称/供应商代码")

        if failed_fields:
            logger.error("规则6检查未通过，字段缺失或为空: %s", ", ".join(failed_fields))
            return "NG", f"以下字段缺失或为空：{', '.join(failed_fields)}"

        logger.info("")
        return "PASS", ""

    @rule("提交原因检查")
    def rule_7(self, ctx: RuleContext) -> Tuple[str, str]:
        from audit.engine.checkers.excel_field_evidence import check_psw_submission_reason

        return check_psw_submission_reason(self, ctx)

    @rule("认可情况检查")
    def rule_8(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则8：识别PSW是否包含“认可情况”字段，如果不包含则不通过，否则继续识别；“认可情况”字段是否有且仅有一个选项被勾选，如果不是则不通过，否则继续识别；当“认可情况”的“批准”选项被勾选，且“提交结果”区域的选项“尺寸测量”、“外观标准”、“材料和功能试验”、“统计过程数据”均被勾选则通过，且“这些结果均满足所有图纸和规范要求”也有选项被勾选则通过，否则不通过

        Returns:
            bool: 规则是否通过
        """
        main_sheet = Table(ctx).sheet("PSW主表")
        checked_controls = self._checked_controls(main_sheet)
        disposition, disposition_checks, disposition_row = self._resolve_warrant_disposition(main_sheet, checked_controls)
        if disposition == "missing_field":
            return "NG", "未找到“认可情况”字段。"
        if disposition == "invalid_selection":
            return "NG", "“认可情况”需且仅需勾选一个选项。"

        if disposition == "approved":
            all_checked = self._is_submission_result_all_checked(main_sheet, checked_controls)
            ctx.record_execution()
            if all_checked is True:
                return "PASS", ""
            if all_checked is None:
                return "UNABLE", "无法将提交结果的全部选项与各自的勾选状态绑定。"
            return "NG", "“批准”状态下提交结果勾选不完整。"

        if disposition in {"conditional", "rejected"}:
            return "PASS", ""

        logger.error("规则8无法识别认可情况选项: %s / %s", disposition, disposition_checks)
        if disposition_row is None:
            return "NG", "认可情况定位异常，无法判断勾选状态。"
        return "UNABLE", "无法确定控件对应的“认可情况”选项。"

    @rule("条件批准日期检查")
    def rule_9(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则9：当认可情况勾选“条件批准”，则继续识别识别文件右下角日期和当前日期是否在3个月之内，在3个月内则通过，否则不通过

        Returns:
            bool: 规则是否通过
        """
        main_sheet = Table(ctx).sheet("PSW主表")
        checked_controls = self._checked_controls(main_sheet)
        disposition, disposition_checks, disposition_row = self._resolve_warrant_disposition(main_sheet, checked_controls)
        if disposition == "missing_field":
            return "NG", "未找到“认可情况”字段。"
        if disposition == "invalid_selection":
            return "NG", "“认可情况”需且仅需勾选一个选项。"
        if disposition == "unknown":
            return "UNABLE", "无法确定控件对应的“认可情况”选项。"
        if disposition != "conditional":
            return "PASS", ""
        if disposition_row is None:
            return "NG", "条件批准行定位失败。"

        approval_date = self._extract_conditional_approval_date(main_sheet, disposition_row)
        if approval_date is None:
            return "UNABLE", "无法识别条件批准日期。"
        if not isinstance(approval_date, date):
            return "NG", "无法识别条件批准日期：日期无效。"

        today = datetime.now().date()
        days_delta = (today - approval_date).days
        ctx.record_execution()
        if 0 <= days_delta <= 92:
            return "PASS", ""
        return "NG", "条件批准日期超出3个月。"

    @rule("条件批准补充评论检查")
    def rule_10(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则10：当认可情况勾选“条件批准”，且补充评论内容不为空则通过，否则不通过

        Returns:
            bool: 规则是否通过
        """
        table = Table(ctx)
        main_sheet = table.sheet("PSW主表")
        checked_controls = self._checked_controls(main_sheet)
        disposition, disposition_checks, _ = self._resolve_warrant_disposition(main_sheet, checked_controls)
        if disposition == "missing_field":
            return "NG", "未找到“认可情况”字段。"
        if disposition == "invalid_selection":
            return "NG", "“认可情况”需且仅需勾选一个选项。"
        if disposition == "unknown":
            return "UNABLE", "无法确定控件对应的“认可情况”选项。"
        if disposition != "conditional":
            return "PASS", ""

        comments_value = self._extract_comments_value(table, main_sheet)
        ctx.record_execution()
        if str(comments_value or "").strip().lower() not in _EMPTY_MARKERS:
            return "PASS", ""
        return "NG", "条件批准需填写补充评论。"
