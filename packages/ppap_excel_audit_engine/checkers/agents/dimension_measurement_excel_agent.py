"""尺寸检验报告 Excel 审核代理。"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import Row, RuleContext, Table

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)

STRICT_OK_TOKENS = {"ok", "合格"}
INVALID_MEASUREMENT_TOKENS = {"/", "-", "—", "－", "na", "n/a", "none", "null", "／"}
FORBIDDEN_RESULT_EQUIVALENTS = {"pass", "正确"}
GEOMETRIC_STANDARD_PREFIXES = ("位置度", "同轴度", "圆柱度", "平面度", "垂直度", "平行度")
PURE_NOMINAL_STANDARD_RE = re.compile(r"(?:\d+\s*[xX×]\s*)?[Φφ]?-?\d+(?:\.\d+)?")
MEASUREMENT_NUMBER_WITH_UNIT_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:[a-zA-ZμΩ℃°%·.]+)?\s*$")
RULE_7_ALLOWED_METHOD_KEYWORDS = ("通止规", "菲林尺", "卡板")
REQUIRED_FOOTER_FIELDS = ("车型", "零件名称", "零件号", "供应商", "责任人", "日期")


def _split_tokens(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    text = str(value).replace("\n", " ").replace("\r", " ").strip().lower()
    if not text:
        return ()
    return tuple(token for token in re.split(r"[/,，;；\s]+", text) if token)


def _normalize_quality_standard_text(value: Any) -> str:
    normalized = str(value or "").strip()
    replacements = {
        "（": "(",
        "）": ")",
        "，": ",",
        "＋": "+",
        "－": "-",
        "—": "-",
        "–": "-",
        "／": "/",
        "﹣": "-",
        "°": "",
    }
    for src, target in replacements.items():
        normalized = normalized.replace(src, target)
    return normalized


def _parse_quality_standard_range(value: Any) -> tuple[float, float] | None:
    normalized = _normalize_quality_standard_text(value)
    s = re.sub(r"^\d+\s*[xX×]\s*", "", normalized)
    s = re.sub(r"^[Φφ]", "", s)

    match = re.fullmatch(r"[rR]\s*(\d+\.?\d*)\s*max", s, flags=re.IGNORECASE)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*min", s, flags=re.IGNORECASE)
    if match:
        return (float(match.group(1)), float("inf"))

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*±\s*(\d+\.?\d*)(?:\s*[a-zA-Z]*)?", s)
    if match:
        base, tolerance = map(float, match.groups())
        return (base - tolerance, base + tolerance)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\+\s*(\d+\.?\d*)\s*/\s*-\s*(\d+\.?\d*)", s)
    if match:
        base, upper_tol, lower_tol = map(float, match.groups())
        return (base - lower_tol, base + upper_tol)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\(\s*\+?(\d+\.?\d*)\s*[,/]\s*(-?\d+\.?\d*)\s*\)", s)
    if match:
        base = float(match.group(1))
        upper_tol = float(match.group(2))
        lower_tol = abs(float(match.group(3)))
        return (base - lower_tol, base + upper_tol)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*\+\s*(\d+\.?\d*)", s)
    if match:
        base, upper_tol = map(float, match.groups())
        return (base, base + upper_tol)

    match = re.fullmatch(r"(-?\d+\.?\d*)\s*/\s*-\s*(\d+\.?\d*)", s)
    if match:
        base, lower_tol = map(float, match.groups())
        return (base - lower_tol, base)

    match = re.fullmatch(r"(?:≤|<=)\s*(-?\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"(?:<|＜)\s*(-?\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    match = re.fullmatch(r"[rR][aA]?\s*(\d+\.?\d*)", s)
    if match:
        return (0.0, float(match.group(1)))

    return None


def _is_strict_ok_text(value: Any) -> bool:
    tokens = _split_tokens(value)
    if not tokens:
        return False
    return all(token in STRICT_OK_TOKENS for token in tokens)


def _contains_strict_ok_token(value: Any) -> bool:
    return any(token in STRICT_OK_TOKENS for token in _split_tokens(value))


def _parse_measurement_numbers(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return ()
    if isinstance(value, (int, float)):
        return (float(value),)

    text = str(value).strip()
    if not text:
        return ()
    compact_text = "".join(text.lower().split())
    if compact_text in INVALID_MEASUREMENT_TOKENS:
        return None

    numbers: list[float] = []
    for token in _split_tokens(text):
        if token in INVALID_MEASUREMENT_TOKENS:
            return None
        if token in STRICT_OK_TOKENS:
            return None
        if token in FORBIDDEN_RESULT_EQUIVALENTS:
            return None
        match = MEASUREMENT_NUMBER_WITH_UNIT_RE.fullmatch(token)
        if match is None:
            return None
        numbers.append(float(match.group(1)))
    return tuple(numbers)


def _inspect_measurements(
    raw_values: tuple[Any, ...],
) -> tuple[Literal["empty", "numeric_values", "strict_ok_values", "mixed_or_invalid"], tuple[tuple[float, ...], ...]]:
    if not raw_values:
        return "empty", ()

    parsed_groups: list[tuple[float, ...]] = []
    saw_strict_ok = False

    for value in raw_values:
        if _is_strict_ok_text(value):
            saw_strict_ok = True
            parsed_groups.append(())
            continue

        numbers = _parse_measurement_numbers(value)
        if numbers is None:
            return "mixed_or_invalid", ()
        parsed_groups.append(numbers)

    if saw_strict_ok:
        if all(_is_strict_ok_text(value) for value in raw_values):
            return "strict_ok_values", ()
        return "mixed_or_invalid", ()

    return "numeric_values", tuple(parsed_groups)


def _classify_quality_standard(value: Any) -> tuple[str, tuple[float, float] | None]:
    numeric_range = _parse_quality_standard_range(value)
    if numeric_range is not None:
        return "numeric_range", numeric_range

    normalized = re.sub(r"\s+", "", _normalize_quality_standard_text(value))
    if not normalized:
        return "unknown", None

    matched_prefix = next((prefix for prefix in GEOMETRIC_STANDARD_PREFIXES if normalized.startswith(prefix)), None)
    if matched_prefix is not None:
        suffix = normalized.removeprefix(matched_prefix)
        if suffix and re.fullmatch(r"-?\d+(?:\.\d+)?", suffix):
            return "recognized_non_range", None

    if PURE_NOMINAL_STANDARD_RE.fullmatch(normalized):
        return "recognized_non_range", None

    return "unknown", None


@dataclass(frozen=True)
class Doc01RowObservation:
    row_index: int
    section_group: str | None
    standard_text: str
    method_text: str
    result_text: str
    raw_measurements: tuple[Any, ...]


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
    *,
    compact_measurements: bool = False,
) -> Doc01RowObservation:
    raw_measurements: tuple[Any, ...] = ()
    if "检查数据" in columns and "判定结果" in columns:
        raw_measurements = tuple(
            row.values_between("检查数据", "判定结果", compact=compact_measurements),
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

    DOC_TYPE = PpapDocumentType.DIMENSION_MEASUREMENT_REPORT

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
        checked_rows = 0

        for section in _iter_doc01_main_sections(ctx):
            columns = section.columns(
                "检查数据",
                "判定标准",
                "判定结果",
                optional=("序号", "检查项目", "供方检查方法/检查工具"),
            )

            for row in _iter_doc01_inspection_rows(section, columns):
                obs = _observe_doc01_row(row, columns, compact_measurements=True)
                if obs.section_group != "尺寸":
                    continue

                measurement_kind, parsed_groups = _inspect_measurements(obs.raw_measurements)
                if measurement_kind == "empty":
                    continue

                if measurement_kind == "strict_ok_values":
                    if not obs.method_text.strip():
                        return "NG", f"工作表{row.sheet_title}第{obs.row_index}行检查数据为OK/合格但缺少检查方法，无法完成校验"
                    if not _is_strict_ok_text(obs.result_text):
                        return "NG", f"工作表{row.sheet_title}第{obs.row_index}行判定结果不规范: {obs.result_text}"
                    continue
                if measurement_kind == "mixed_or_invalid":
                    invalid_value = next(
                        (value for value in obs.raw_measurements if _parse_measurement_numbers(value) is None),
                        obs.raw_measurements[0],
                    )
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行检查数据非数值或占位符: {invalid_value}"

                standard_kind, quality_range = _classify_quality_standard(obs.standard_text)
                if standard_kind == "recognized_non_range":
                    continue
                if standard_kind != "numeric_range" or quality_range is None:
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行判定标准无法解析: {obs.standard_text}"
                if not _is_strict_ok_text(obs.result_text):
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行判定结果不规范: {obs.result_text}"

                row_numbers = [number for numbers in parsed_groups for number in numbers]

                if not row_numbers:
                    continue

                checked_rows += 1
                lower, upper = quality_range
                for number in row_numbers:
                    if not (lower <= number <= upper):
                        return "NG", f"工作表{row.sheet_title}第{obs.row_index}行检查数据{number}超出公差范围{(lower, upper)}"

        if checked_rows == 0:
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
                method_tokens = _split_tokens(method_text)
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

                measurement_kind, parsed_groups = _inspect_measurements(obs.raw_measurements)
                if measurement_kind == "empty":
                    continue
                if measurement_kind == "strict_ok_values":
                    continue
                if measurement_kind == "mixed_or_invalid":
                    continue

                row_values = [number for numbers in parsed_groups for number in numbers or ()]

                if len(row_values) < 2:
                    continue
                if len(set(row_values)) < len(row_values):
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行同行检查数据存在重复值"

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
                    if _is_strict_ok_text(value):
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

                measurement_kind, _ = _inspect_measurements(obs.raw_measurements)
                if measurement_kind in {"empty", "numeric_values"}:
                    continue

                if measurement_kind == "mixed_or_invalid" and not any(_contains_strict_ok_token(value) for value in obs.raw_measurements):
                    continue

                checked_rows += 1
                if measurement_kind != "strict_ok_values":
                    invalid_value = next(
                        (value for value in obs.raw_measurements if not _is_strict_ok_text(value)),
                        obs.raw_measurements[0],
                    )
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行定性方法数据不合规: {invalid_value}"

                method_text = row.value("供方检查方法/检查工具", compact=True)
                if not any(keyword in method_text for keyword in RULE_7_ALLOWED_METHOD_KEYWORDS):
                    return "NG", f"工作表{row.sheet_title}第{obs.row_index}行检查数据为OK/合格但方法不匹配: {obs.method_text}"

        if checked_rows == 0:
            return "PASS", "规则7按通过处理"
        return "PASS", "所有定性方法数据均为OK/合格"

    @rule("关键字段识别检查")
    def rule_8(self, ctx: RuleContext) -> tuple[str, str]:
        footer = Table(ctx).field_block("表尾区")
        footer.expect(*REQUIRED_FOOTER_FIELDS, context="表尾区")
        return "PASS", ""
