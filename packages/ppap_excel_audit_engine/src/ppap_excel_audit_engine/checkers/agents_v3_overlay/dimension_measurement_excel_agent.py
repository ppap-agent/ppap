"""尺寸检验报告 Excel 审核代理。"""

from __future__ import annotations

import re
from typing import Any

from openpyxl.utils import get_column_letter
from ppap_core.types import PpapDocumentType

from ppap_excel_audit_engine.rule_engine import Decision, RuleContext, Table

from .excel_agent import ExcelAgent, rule

RULE_8_11_TARGET_DIRECTIONS = {"x", "y", "z", "nd"}
RULE_3_4_TARGET_DIRECTIONS = {"nd", "gap", "flush", "nd-", "gap-", "flush-"}
RULE_7_TARGET_SHEET_KEYWORDS = ("hole&slot", "surface", "trim", "subdatum", "gap&flush")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", "").replace("\r", "").replace(" ", "").strip().lower()


def _is_blank(value: Any) -> bool:
    return _normalize_text(value) in {"", "none", "nan"}


def _is_na(value: Any) -> bool:
    return _normalize_text(value) in {"na", "n/a"}


def _tokenize_label_text(value: Any) -> list[str]:
    if value is None:
        return []
    return re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", str(value).replace("\n", " ").replace("\r", " ").lower())


def _is_exact_label_value(value: Any, aliases: tuple[str, ...]) -> bool:
    tokens = tuple(_tokenize_label_text(value))
    if not tokens:
        return False

    alias_options = [tuple(_tokenize_label_text(alias)) for alias in aliases if _tokenize_label_text(alias)]
    if tokens in alias_options:
        return True

    for first in alias_options:
        if tokens[: len(first)] != first:
            continue
        if tokens[len(first) :] in alias_options:
            return True
    return False


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if _normalize_text(text) in {"none", "nan", "#ref!", "na", "n/a"}:
        return None

    try:
        return float(text)
    except (TypeError, ValueError):
        pass

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except (TypeError, ValueError):
        return None


def _numeric_values(raw_values: list[Any]) -> list[float]:
    values: list[float] = []
    for raw_value in raw_values:
        value = _to_float(raw_value)
        if value is not None:
            values.append(value)
    return values


def _format_threshold_preview(violations: list[tuple[str, int, int, float]], metric_label: str) -> str:
    return ", ".join(f"{sheet} 行{row} ({metric_label}/{get_column_letter(col)}列)" for sheet, row, col, _ in violations[:8])


def _part_measurement_values(row: Any, columns: dict[str, Any]) -> list[float]:
    stop_column = "IQG" if "IQG" in columns else "Centering"
    return _numeric_values(row.values_between("测量偏差值", stop_column))


class DimensionMeasurementExcelAgent(ExcelAgent):
    """尺寸检验报告 Excel 文件专用审核代理。"""

    DOC_TYPE = PpapDocumentType.DIMENSION_MEASUREMENT_REPORT
    EXPECTED_RULE_CATEGORIES = (
        "CoverSheet页存在性及内容完整性审核",
        "测量报告列存在性及数据非空审核",
        "特定控制方向的公差NA值一致性审核",
        "名义值为空时的偏差值存在性审核",
        "类型列内容合规性审核",
        "测点号与测点图标记点对应性审核",
        "特定表类型的偏差值在公差范围内审核",
        "中性值（Centering）计算准确性审核",
        "中性值（Centering）阈值判定及预警审核",
        "极差值（Range）计算准确性审核",
        "极差值（Range）阈值判定及预警审核",
    )

    COVER_FIELD_CONFIG: dict[str, dict[str, Any]] = {
        "零件号": {
            "aliases": ("零件号", "part no", "part number", "partno", "partnumber"),
            "max_rows": 12,
            "max_cols": 40,
            "value_mode": "right_then_down",
            "max_down": 1,
        },
        "零件名": {
            "aliases": ("零件名", "零件名称", "part name", "partname"),
            "max_rows": 12,
            "max_cols": 40,
            "value_mode": "right_then_down",
            "max_down": 1,
        },
        "项目": {
            "aliases": ("项目", "project"),
            "max_rows": 12,
            "max_cols": 40,
            "value_mode": "right_then_down",
            "max_down": 1,
        },
        "项目阶段": {
            "aliases": ("项目阶段", "timing", "phase"),
            "max_rows": 12,
            "max_cols": 40,
            "value_mode": "right_then_down",
            "max_down": 1,
        },
        "日期": {
            "aliases": ("日期", "date"),
            "max_rows": None,
            "max_cols": 40,
            "value_mode": "down_only",
            "max_down": 3,
        },
    }

    def _is_cover_label_value(self, value: Any) -> bool:
        return any(_is_exact_label_value(value, config["aliases"]) for config in self.COVER_FIELD_CONFIG.values())

    @staticmethod
    def _is_blank_or_zero_nominal(value: Any) -> bool:
        if _is_blank(value):
            return True
        numeric_value = _to_float(value)
        return numeric_value is not None and abs(numeric_value) < 1e-9

    @staticmethod
    def _is_target_measurement_sheet(sheet_title: str) -> bool:
        normalized_name = _normalize_text(sheet_title)
        return any(keyword in normalized_name for keyword in RULE_7_TARGET_SHEET_KEYWORDS)

    def _sections(
        self,
        ctx: RuleContext,
        *required: str,
        optional: tuple[str, ...] = (),
    ) -> list[tuple[Any, Any, dict[str, Any]]]:
        sections = []
        for sheet in Table(ctx).find_sheets("主表"):
            section = sheet.section("主数据区")
            columns = section.columns(*required, optional=optional)
            sections.append((sheet, section, columns))
        return sections

    @rule("CoverSheet页存在性及内容完整性审核")
    def rule_1(self, ctx: RuleContext) -> tuple[str, str]:
        missing_fields: list[str] = []
        empty_values: list[str] = []
        cover_sheets = Table(ctx).find_sheets("CoverSheet", on_missing=Decision.IGNORE)
        if not cover_sheets:
            return "NG", "未找到CoverSheet工作表"

        for field, config in self.COVER_FIELD_CONFIG.items():
            field_found = False
            field_has_value = False
            for sheet in cover_sheets:
                for alias in config["aliases"]:
                    cell = sheet.find_label(
                        ((alias,),),
                        row_max=config["max_rows"] or sheet.ws.max_row,
                        col_max=config["max_cols"],
                    )
                    if cell is None:
                        continue
                    field_found = True
                    candidates = cell.scan_nearby(
                        row_span=config.get("max_down", 3),
                        col_span=0 if config["value_mode"] == "down_only" else 6,
                    )
                    if any(candidate.has_data() and not self._is_cover_label_value(candidate.value()) for candidate in candidates):
                        field_has_value = True
                        break
                if field_has_value:
                    break
            if not field_found:
                missing_fields.append(field)
            elif not field_has_value:
                empty_values.append(field)

        if missing_fields:
            return "NG", f"缺少字段: {', '.join(missing_fields)}"
        if empty_values:
            return "NG", f"字段内容为空: {', '.join(empty_values)}"
        return "PASS", "CoverSheet字段完整且内容不为空"

    @rule("测量报告列存在性及数据非空审核")
    def rule_2(self, ctx: RuleContext) -> tuple[str, str]:
        for _sheet, section, columns in self._sections(
            ctx,
            "测点号",
            "坐标X",
            "坐标Y",
            "坐标Z",
            "上公差",
            "下公差",
            "主要控制方向",
        ):
            for row in section.rows_from(columns):
                if not row.is_present("测点号"):
                    continue
                missing = row.missing("坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "主要控制方向")
                if missing:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index}测点号不为空但字段为空: {', '.join(missing)}"
        return "PASS", "规则2检查通过"

    @rule("特定控制方向的公差NA值一致性审核")
    def rule_3(self, ctx: RuleContext) -> tuple[str, str]:
        for _sheet, section, columns in self._sections(ctx, "测点号", "主要控制方向", "上公差", "下公差"):
            for row in section.rows_from(columns):
                direction = _normalize_text(row.value("主要控制方向"))
                if direction not in RULE_3_4_TARGET_DIRECTIONS:
                    continue
                if not _is_na(row.value("上公差")) or not _is_na(row.value("下公差")):
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} 方向{direction}时上下公差应为NA"
        return "PASS", "规则3检查通过"

    @rule("名义值为空时的偏差值存在性审核")
    def rule_4(self, ctx: RuleContext) -> tuple[str, str]:
        target_directions = RULE_8_11_TARGET_DIRECTIONS | RULE_3_4_TARGET_DIRECTIONS
        for _sheet, section, columns in self._sections(
            ctx,
            "测点号",
            "主要控制方向",
            "名义值",
            "测量偏差值",
            "Centering",
            optional=("IQG",),
        ):
            for row in section.rows_from(columns):
                direction = _normalize_text(row.value("主要控制方向"))
                if direction not in target_directions:
                    continue
                if not self._is_blank_or_zero_nominal(row.value("名义值")):
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} 方向{direction}时名义值应为空(或0)"
                if not _part_measurement_values(row, columns):
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} 方向{direction}时测量偏差值为空"
        return "PASS", "规则4检查通过"

    @rule("类型列内容合规性审核")
    def rule_5(self, ctx: RuleContext) -> tuple[str, str]:
        allowed = {"cc", "sc", "ac"}
        for _sheet, section, columns in self._sections(ctx, "测点号", "类型"):
            for row in section.rows_from(columns):
                if not row.is_present("测点号"):
                    continue
                type_value = _normalize_text(row.value("类型"))
                if type_value and type_value not in allowed:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} 类型值非法: {type_value}"
        return "PASS", "规则5检查通过"

    @rule("测点号与测点图标记点对应性审核")
    def rule_6(self, ctx: RuleContext) -> tuple[str, str]:
        marker_pattern = re.compile(r"[A-Za-z]{1,4}\d{1,4}")
        for _sheet, section, columns in self._sections(ctx, "测点号", optional=("测点图/截面",)):
            point_set = {_normalize_text(row.value("测点号")).upper() for row in section.rows_from(columns) if row.is_present("测点号")}
            if "测点图/截面" not in columns:
                continue
            markers: set[str] = set()
            for row in section.rows_from(columns):
                raw = row.value("测点图/截面")
                if _is_blank(raw):
                    continue
                markers.update(token.upper() for token in marker_pattern.findall(str(raw)))
            missing = sorted(marker for marker in markers if marker not in point_set)
            if missing:
                return "NG", f"工作表{section.sheet_title} 测点图标记未在测点号列找到: {', '.join(missing[:8])}"
        return "PASS", "规则6检查通过"

    @rule("特定表类型的偏差值在公差范围内审核")
    def rule_7(self, ctx: RuleContext) -> tuple[str, str]:
        for sheet, section, columns in self._sections(
            ctx,
            "测点号",
            "名义值",
            "上公差",
            "下公差",
            "测量偏差值",
            "Centering",
            optional=("IQG",),
        ):
            if not self._is_target_measurement_sheet(sheet.title):
                continue
            for row in section.rows_from(columns):
                part_values = _part_measurement_values(row, columns)
                if not part_values:
                    continue
                upper = _to_float(row.value("上公差"))
                lower = _to_float(row.value("下公差"))
                if upper is None or lower is None:
                    continue
                nominal = _to_float(row.value("名义值"))
                if nominal is None:
                    nominal = 0.0
                min_bound = min(nominal + lower, nominal + upper)
                max_bound = max(nominal + lower, nominal + upper)
                for value in part_values:
                    if value < min_bound - 1e-9 or value > max_bound + 1e-9:
                        return "NG", f"工作表{row.sheet_title} 行{row.row_index} 偏差值{value}超出公差范围[{min_bound}, {max_bound}]"
        return "PASS", "规则7检查通过"

    @rule("中性值（Centering）计算准确性审核")
    def rule_8(self, ctx: RuleContext) -> tuple[str, str]:
        for _sheet, section, columns in self._sections(
            ctx,
            "测点号",
            "主要控制方向",
            "名义值",
            "上公差",
            "下公差",
            "测量偏差值",
            "Centering",
            optional=("IQG",),
        ):
            for row in section.rows_from(columns):
                direction = _normalize_text(row.value("主要控制方向"))
                if direction not in RULE_8_11_TARGET_DIRECTIONS:
                    continue
                part_values = _part_measurement_values(row, columns)
                if not part_values:
                    continue
                upper_raw = row.value("上公差")
                lower_raw = row.value("下公差")
                upper = _to_float(upper_raw)
                lower = _to_float(lower_raw)
                if upper is None or lower is None or _is_na(upper_raw) or _is_na(lower_raw) or abs(upper - lower) < 1e-9:
                    continue
                nominal = _to_float(row.value("名义值")) or 0.0
                expected = (-nominal + (sum(part_values) / len(part_values))) / (upper - lower)
                actual = _to_float(row.value("Centering"))
                if actual is None:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} Centering为空或不是有效数字"
                if actual is not None and abs(actual - expected) > 0.02:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} Centering不一致: 期望{expected:.4f}, 实际{actual:.4f}"
        return "PASS", "Centering计算校验通过"

    @rule("中性值（Centering）阈值判定及预警审核")
    def rule_9(self, ctx: RuleContext) -> tuple[str, str]:
        over_066: list[tuple[str, int, int, float]] = []
        warn_033: list[tuple[str, int, int, float]] = []
        for _sheet, section, columns in self._sections(ctx, "测点号", "Centering"):
            centering_col = columns["Centering"].header_col
            for row in section.rows_from(columns):
                value = _to_float(row.value("Centering"))
                if value is None:
                    continue
                abs_value = abs(value)
                if abs_value > 0.66:
                    over_066.append((row.sheet_title, row.row_index, centering_col, abs_value))
                elif abs_value > 0.33:
                    warn_033.append((row.sheet_title, row.row_index, centering_col, abs_value))
        if over_066:
            return "NG", f"Centering绝对值>0.66: {_format_threshold_preview(over_066, 'Centering')}"
        if warn_033:
            return "PASS", f"通过，但以下行Centering绝对值>0.33: {_format_threshold_preview(warn_033, 'Centering')}"
        return "PASS", "Centering阈值检查通过"

    @rule("极差值（Range）计算准确性审核")
    def rule_10(self, ctx: RuleContext) -> tuple[str, str]:
        for _sheet, section, columns in self._sections(
            ctx,
            "测点号",
            "主要控制方向",
            "上公差",
            "下公差",
            "测量偏差值",
            "Centering",
            "Range",
            optional=("IQG",),
        ):
            for row in section.rows_from(columns):
                direction = _normalize_text(row.value("主要控制方向"))
                if direction not in RULE_8_11_TARGET_DIRECTIONS:
                    continue
                part_values = _part_measurement_values(row, columns)
                if not part_values:
                    continue
                upper_raw = row.value("上公差")
                lower_raw = row.value("下公差")
                upper = _to_float(upper_raw)
                lower = _to_float(lower_raw)
                if upper is None or lower is None or _is_na(upper_raw) or _is_na(lower_raw) or abs(upper - lower) < 1e-9:
                    continue
                expected = (max(part_values) - min(part_values)) / (upper - lower)
                actual = _to_float(row.value("Range"))
                if actual is None:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} Range为空或不是有效数字"
                if actual is not None and abs(actual - expected) > 0.02:
                    return "NG", f"工作表{row.sheet_title} 行{row.row_index} Range不一致: 期望{expected:.4f}, 实际{actual:.4f}"
        return "PASS", "Range计算校验通过"

    @rule("极差值（Range）阈值判定及预警审核")
    def rule_11(self, ctx: RuleContext) -> tuple[str, str]:
        over_066: list[tuple[str, int, int, float]] = []
        warn_033: list[tuple[str, int, int, float]] = []
        for _sheet, section, columns in self._sections(ctx, "测点号", "Range"):
            range_col = columns["Range"].header_col
            for row in section.rows_from(columns):
                value = _to_float(row.value("Range"))
                if value is None:
                    continue
                abs_value = abs(value)
                if abs_value > 0.66:
                    over_066.append((row.sheet_title, row.row_index, range_col, abs_value))
                elif abs_value > 0.33:
                    warn_033.append((row.sheet_title, row.row_index, range_col, abs_value))
        if over_066:
            return "NG", f"Range绝对值>0.66: {_format_threshold_preview(over_066, 'Range')}"
        if warn_033:
            return "PASS", f"通过，但以下行Range绝对值>0.33: {_format_threshold_preview(warn_033, 'Range')}"
        return "PASS", "Range阈值检查通过"
