"""尺寸检验报告 Excel 审核代理。"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Mapping

from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource
from audit.engine.checkers.dimension_measurement_policy import check_duplicates, check_qualitative_method, check_tolerance, is_strict_ok_text, normalize_quality_standard_text, split_tokens
from audit.engine.rule_engine import Row, RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

REQUIRED_FOOTER_FIELDS = ("车型", "零件名称", "零件号", "供应商", "责任人", "日期")


@dataclass(frozen=True)
class Doc01RowObservation:
    row_index: int
    section_group: str | None
    standard_text: str
    method_text: str
    result_text: str
    raw_measurements: tuple[Any, ...]
    unit: str = ""


def _resolve_row_index(row: Any, columns: Mapping[str, Any]) -> int:
    preferred_columns = ("判定结果", "检查数据", "供方检查方法/检查工具")
    for column_name in preferred_columns:
        if column_name not in columns:
            continue
        cell = row.anchor(column_name, strict=False)
        if cell is not None:
            return int(cell.row_index)
    for column_name in columns:
        cell = row.anchor(column_name, strict=False)
        if cell is not None:
            return int(cell.row_index)
    return 0


def _observe_doc01_row(
    row: Any,
    columns: Mapping[str, Any],
) -> Doc01RowObservation:
    raw_measurements: tuple[Any, ...] = ()
    if "检查数据" in columns and "判定结果" in columns:
        raw_measurements = tuple(
            row.values_between("检查数据", "判定结果"),
        )

    standard_value = row.value("判定标准") if "判定标准" in columns else None
    method_value = row.value("供方检查方法/检查工具") if "供方检查方法/检查工具" in columns else None
    result_value = row.value("判定结果") if "判定结果" in columns else None

    return Doc01RowObservation(
        row_index=_resolve_row_index(row, columns),
        section_group=row.group("区域"),
        standard_text="" if standard_value is None else str(standard_value),
        method_text="" if method_value is None else str(method_value),
        result_text="" if result_value is None else str(result_value),
        raw_measurements=raw_measurements,
        unit=str(row.value("单位") or "") if "单位" in columns else "",
    )


def _is_doc01_sequence_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return re.fullmatch(r"\d+(?:\.0+)?", str(value).strip()) is not None


def _iter_doc01_inspection_rows(section: Any, columns: Mapping[str, Any]) -> Iterator[Row]:
    if "序号" not in columns or "检查项目" not in columns:
        yield from section.rows_from(columns)
        return

    for row_index in range(1, section.resolved.ws.max_row + 1):
        row = Row(section.ctx, section.resolved.ws, columns, row_index, section.resolved)
        if row.is_header():
            continue
        if not _is_doc01_sequence_number(row.value("序号")):
            continue
        if not row.is_present("检查项目"):
            continue
        if "检查数据" in columns and "判定结果" in columns and not row.values_between("检查数据", "判定结果"):
            continue
        yield row


def _iter_doc01_main_sections(ctx: RuleContext) -> Iterator[Any]:
    for sheet in Table(ctx).find_sheets("主表"):
        yield sheet.section("主数据区")


class DimensionMeasurementExcelAgent(ExcelAgent):
    """尺寸检验报告 Excel 文件专用审核代理。"""

    _original_dimension_cells: dict[tuple[str, str], list[tuple[tuple[Any, ...], int, str, tuple[float, float, float, float]]]]

    DOC_TYPE = PpapDocumentType.DIMENSION_MEASUREMENT_REPORT
    _numeric_evidence: AuditEvidence | None = None
    _original_measurement_source: EvidenceSource | None = None
    _numeric_failure_source: EvidenceSource | None = None

    def _finalize_audit_result(self, result: AuditResultItem, *, rule_no: int, phase: Any) -> AuditResultItem:
        result = super()._finalize_audit_result(result, rule_no=rule_no, phase=phase)
        if rule_no == 3 and self._numeric_evidence is not None:
            return result.model_copy(update={"evidence": self._numeric_evidence})
        return result

    def _original_pdf_measurements(self, row: Row, observation: Doc01RowObservation) -> tuple[tuple[Any, ...], str]:
        """Recover cell boundaries using the same serial and standard in the source.

        Conversion can put several original measurement cells into one cell.
        A line break then cannot distinguish a wrapped decimal from a new value.
        """
        from pathlib import Path

        self._original_measurement_source = None
        source = (self.audit_file.file_metadata or {}).get("source_pdf_url")
        if not source or not Path(source).is_file() or "序号" not in row.columns:
            return observation.raw_measurements, ""

        def key(serial, standard):
            return str(serial).strip(), re.sub(r"\s+", "", normalize_quality_standard_text(standard))

        cache = getattr(self, "_original_dimension_cells", None)
        if cache is None:
            import fitz

            cache = self._original_dimension_cells = {}
            with fitz.open(source) as document:
                for page in document:
                    page.set_rotation(0)
                    for table in page.find_tables().tables:
                        for index, cells in enumerate(table.extract()):
                            if len(cells) not in (10, 11) or not str(cells[0] or "").isdigit():
                                continue
                            if not cells[2] or not cells[-1]:
                                continue
                            measurements = tuple("" if value is None else "".join(str(value).split()) for value in cells[-6:-1])
                            bounds = table.rows[index].bbox
                            cache.setdefault(key(cells[0], cells[2]), []).append((measurements, page.number + 1, str(cells[-1]).strip(), (bounds[0], bounds[1], bounds[2], bounds[3])))
        serial = row.value("序号", compact=True)
        matches = cache.get(key(serial, observation.standard_text), [])
        if len(matches) == 1 and matches[0][2].lower() == observation.result_text.strip().lower():
            import json

            measurements, page, _, bounds = matches[0]
            self._original_measurement_source = EvidenceSource(
                kind="structure",
                locator=json.dumps(
                    {
                        "kind": "pdf_table_row",
                        "page": page,
                        "serial": serial,
                        "bbox": bounds,
                        "coordinate_system": "pdf_points_unrotated",
                        "file_version_id": str((self.audit_file.file_metadata or {}).get("file_version_id") or self.audit_file.audit_id),
                    },
                    ensure_ascii=False,
                ),
                excerpt=str(measurements)[:500],
            )
            return measurements, f"原PDF第{page}页序号{serial}"
        return observation.raw_measurements, ""

    @rule("标题识别检查")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        block = Table(ctx).field_block("标题区")
        block.expect("报告标题", context="标题区")
        title = block.field("报告标题")
        if not title.exact("全功能检验报告", compact=True):
            return "NG", "文档标题不匹配"
        return "PASS", ""

    @rule("重要度标记检查")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        block = Table(ctx).field_block("重要度区")
        block.expect("重要度区分", context="重要度区")
        importance = block.field("重要度区分")
        if importance.checked_count() != 1:
            return "NG", "重要度标记不规范"
        return "PASS", ""

    @rule("尺寸区数值公差带检查")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        from audit.engine.checkers.excel_field_evidence import native_cell_evidence

        self._numeric_failure_source = None
        try:
            return self._check_tolerance_rows(ctx)
        finally:
            self._numeric_evidence = native_cell_evidence(ctx, str(self.audit_file.file_url))
            if self._numeric_evidence is not None and self._numeric_failure_source is not None:
                self._numeric_evidence = self._numeric_evidence.model_copy(update={"sources": (self._numeric_failure_source, *self._numeric_evidence.sources)})

    def _check_tolerance_rows(self, ctx: RuleContext) -> tuple[str, str]:
        checked_rows = 0
        numeric_rows = 0
        unresolved = ""

        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns(
                "检查数据",
                "判定标准",
                "判定结果",
                optional=("序号", "检查项目", "供方检查方法/检查工具", "单位"),
            )

            for row in _iter_doc01_inspection_rows(section, columns):
                obs = _observe_doc01_row(row, columns)
                if obs.section_group != "尺寸":
                    continue

                measurements, original_location = self._original_pdf_measurements(row, obs)
                decision = check_tolerance(obs.standard_text, measurements, obs.result_text, obs.method_text, unit=obs.unit)
                if decision.status == "NG":
                    self._numeric_failure_source = self._original_measurement_source
                    location = original_location or f"工作表{row.sheet_title}第{obs.row_index}行"
                    return "NG", f"{location}{decision.reason}"
                if decision.status == "UNABLE" and not unresolved:
                    self._numeric_failure_source = self._original_measurement_source
                    location = original_location or f"工作表{row.sheet_title}第{obs.row_index}行"
                    unresolved = f"{location}{decision.reason}"
                checked_rows += int(decision.status == "PASS")
                numeric_rows += int(decision.numeric_checked)

        if unresolved:
            return "UNABLE", unresolved
        if checked_rows == 0:
            return "NG", "未识别到可校验的尺寸区检查数据"
        if numeric_rows == 0:
            return "PASS", "规则3按通过处理"
        return "PASS", "所有检查数据均在公差范围内"

    @rule("检查方法/工具列检查")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        inspected_rows = 0

        reject_tokens = {"/", "-", "—", "－", "na", "n/a", "none", "null", "／", "ok", "合格", "pass", "正确"}

        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns("供方检查方法/检查工具", optional=("序号", "检查项目"))

            for row in _iter_doc01_inspection_rows(section, columns):
                if not row.is_present("供方检查方法/检查工具"):
                    continue

                if row.exact("供方检查方法/检查工具", "尺寸", compact=True) or row.exact("供方检查方法/检查工具", "非尺寸", compact=True):
                    continue

                method_text = str(row.value("供方检查方法/检查工具") or "").strip()
                method_tokens = split_tokens(method_text)
                numeric_only_method = bool(method_tokens) and all(re.fullmatch(r"-?\d+(?:\.\d+)?", token) for token in method_tokens)

                inspected_rows += 1
                if "/" in method_text or "或" in method_text or any(row.exact("供方检查方法/检查工具", token, compact=True) for token in reject_tokens) or numeric_only_method:
                    row_index = _resolve_row_index(row, columns)
                    return "NG", f"工作表{row.sheet_title}第{row_index}行方法/工具列内容不合格: {method_text}"

        if inspected_rows == 0:
            return "NG", "未找到供方检查方法/检查工具列或可识别的数据区"
        return "PASS", ""

    @rule("尺寸区数值数据唯一性检查")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns(
                "检查数据",
                "判定结果",
                optional=("序号", "检查项目"),
            )

            for row in _iter_doc01_inspection_rows(section, columns):
                obs = _observe_doc01_row(row, columns)
                if obs.section_group != "尺寸":
                    continue

                decision = check_duplicates(obs.raw_measurements)
                if decision.status == "NG":
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行{decision.reason}"

        return "PASS", "尺寸区同行检查数据无重复值"

    @rule("非尺寸数据检查")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        checked_rows = 0

        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns("检查数据", "判定结果", optional=("序号", "检查项目"))

            for row in _iter_doc01_inspection_rows(section, columns):
                if row.group("区域") != "非尺寸":
                    continue

                values = row.values_between("检查数据", "判定结果")
                if not values:
                    continue

                checked_rows += 1
                for value in values:
                    if is_strict_ok_text(value):
                        continue
                    row_index = _resolve_row_index(row, columns)
                    return "NG", f"工作表{row.sheet_title}第{row_index}行非尺寸数据不合规: {value}"

        if checked_rows == 0:
            return "NG", "未识别到非尺寸区域数据"
        return "PASS", ""

    @rule("尺寸区OK数据方法匹配检查")
    def rule_7(self, ctx: RuleContext) -> tuple[str, str]:
        checked_rows = 0

        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns("检查数据", "判定结果", "供方检查方法/检查工具", optional=("序号", "检查项目"))

            for row in _iter_doc01_inspection_rows(section, columns):
                obs = _observe_doc01_row(row, columns)
                if obs.section_group != "尺寸":
                    continue

                decision = check_qualitative_method(obs.raw_measurements, obs.method_text)
                if decision.status == "NA":
                    continue
                checked_rows += 1
                if decision.status == "NG":
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行{decision.reason}"

        if checked_rows == 0:
            return "PASS", "规则7按通过处理"
        return "PASS", "所有定性方法数据均为OK/合格"

    @rule("关键字段识别检查")
    def rule_8(self, ctx: RuleContext) -> tuple[str, str]:
        footer = Table(ctx).field_block("表尾区")
        footer.expect(*REQUIRED_FOOTER_FIELDS, context="表尾区")
        return "PASS", ""
