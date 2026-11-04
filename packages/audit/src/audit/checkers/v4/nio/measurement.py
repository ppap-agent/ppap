"""N01 keeps the customer's empty-value and arithmetic requirements explicit."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

from audit.checkers.v3.dimension_measurement_excel_agent import (
    RULE_3_4_TARGET_DIRECTIONS,
    RULE_8_11_TARGET_DIRECTIONS,
    DimensionMeasurementExcelAgent,
    _is_exact_label_value,
)
from audit.checkers.v3.excel_agent import rule
from audit.document_tools import snapshot
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Table, get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.DIMENSION_MEASUREMENT_REPORT)
assert _BASE is not None
_SECTIONS = dict(_BASE.sections)
_SECTIONS["主数据区"] = _SECTIONS["主数据区"].model_copy(update={"row_expand_merged_anchors": True})
_RESOURCE = replace(_BASE, sections=MappingProxyType(_SECTIONS))


def _blank(value):
    return value is None or isinstance(value, str) and not value.strip()


def _text(value):
    return "".join(str(value or "").split()).casefold()


def _number(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise ValueError
        return number
    except (InvalidOperation, ValueError):
        raise GuardStop(Decision.UNABLE, "数值无法可靠读取，尚不能完成计算。") from None


def _same_value(row, column, expected):
    actual = _number(row.value(column))
    # Only allow numeric transport noise, never V3's business-sized ±0.02.
    return abs(actual - expected) <= Decimal("1e-10")


class NioMeasurementAgent(DimensionMeasurementExcelAgent):
    CLAUSE_BOUND_CHECKS = True

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    def _load_workbook(self):
        workbook = super()._load_workbook()
        if getattr(self, "_formula_checked_workbook", None) is workbook:
            return workbook
        source = getattr(workbook, "_ppap_source_path", None) or self._source_path
        self._native_source_tables = {}
        if source and Path(source).suffix.casefold() in {".xlsx", ".xlsm"}:
            # Reuse the native reader's formula and visual facts. No formula is
            # recalculated, no linked image fetched, and no original is edited.
            native = snapshot(source)
            self._native_source_tables = {table["id"]: table for table in native["tables"]}
            for table in native["tables"]:
                for address, item in table["cells"].items():
                    if item["state"] in {"unknown_formula_cache", "unknown_excel_error"}:
                        target = workbook[table["id"]][address]
                        target.value = "#N/A"
                        target.data_type = "e"
        self._formula_checked_workbook = workbook
        return workbook

    def _finalize_rule_result(self, *, status, message, ctx):
        if ctx.has_unresolved():
            return self._apply_scope("UNABLE", "存在未算出的公式或不可读取单元格，尚不能判定本条。", ctx)
        return super()._finalize_rule_result(status=status, message=message, ctx=ctx)

    def _rows(self, ctx, *fields, optional=()):
        result = []
        for sheet, section, columns in self._sections(ctx, "测点号", *fields, optional=optional):
            result.extend((sheet, row, columns) for row in section.rows_from(columns) if row.is_present("测点号"))
        if not result:
            raise GuardStop(Decision.UNABLE, "没有有效测点行，不能按空范围通过。")
        return result

    @staticmethod
    def _done(ctx, applicable=True, message=""):
        ctx.mark_scope_complete()
        return ("PASS", message) if applicable else ("NA", "已检查测点范围，本条条件不适用。")

    @staticmethod
    def _parts(row, columns):
        stop = "IQG" if "IQG" in columns else "Centering"
        values = row.values_between("测量偏差值", stop)
        return [_number(value) for value in values]

    @rule("CoverSheet页存在性及内容完整性审核", clause_id="1")
    def rule_1(self, ctx: RuleContext):
        covers = Table(ctx).find_sheets("CoverSheet")
        if len(covers) != 1:
            return "UNABLE", "无法唯一确定CoverSheet。"
        sheet = covers[0]
        if sheet.ws.max_row > 120 or sheet.ws.max_column > 80:
            return "UNABLE", "封面超出本次字段定位范围，需核对原件。"
        for field, config in self.COVER_FIELD_CONFIG.items():
            values = set()
            for cells in sheet.ws.iter_rows():
                for raw in cells:
                    if not _is_exact_label_value(raw.value, config["aliases"]):
                        continue
                    label = sheet.cell(raw.row, raw.column)
                    label.value()
                    merged = ctx.ops.get_detailed_merge_info(sheet.ws, raw.row, raw.column)
                    right = merged.max_col + 1 if merged else raw.column + 1
                    below = merged.max_row + 1 if merged else raw.row + 1
                    candidates = [sheet.cell(raw.row, right)]
                    candidates.extend(sheet.cell(index, raw.column) for index in range(below, min(below + 3, sheet.ws.max_row + 1)))
                    # Stop at a different label; its value cannot fill this field.
                    for candidate in candidates:
                        value = candidate.value()
                        if self._is_cover_label_value(value):
                            break
                        if not _blank(value):
                            values.add(str(value).strip())
                            break
            if not values:
                return "NG", f"CoverSheet缺少或未填写{field}。"
            if len(values) > 1:
                return "UNABLE", f"CoverSheet中{field}有多个不同取值，需确认。"
        return self._done(ctx)

    @rule("测量报告列存在性及数据非空审核", clause_id="2")
    def rule_2(self, ctx: RuleContext):
        fields = ("坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "主要控制方向")
        for _sheet, row, _columns in self._rows(ctx, *fields):
            if row.missing(*fields):
                return "NG", f"测点行{row.row_index}缺少坐标、公差或主要控制方向。"
        return self._done(ctx)

    @rule("特定控制方向的公差NA值一致性审核", clause_id="3")
    def rule_3(self, ctx: RuleContext):
        applied = False
        for _sheet, row, _columns in self._rows(ctx, "主要控制方向", "上公差", "下公差"):
            if _text(row.value("主要控制方向")) not in RULE_3_4_TARGET_DIRECTIONS:
                continue
            applied = True
            if any(_text(row.value(field)) != "na" for field in ("上公差", "下公差")):
                return "NG", f"测点行{row.row_index}指定方向的上下公差须均为NA。"
        return self._done(ctx, applied)

    @rule("名义值为空时的偏差值存在性审核", clause_id="4")
    def rule_4(self, ctx: RuleContext):
        applied = False
        for _sheet, row, columns in self._rows(ctx, "主要控制方向", "名义值", "测量偏差值", "Centering", optional=("IQG",)):
            if _text(row.value("主要控制方向")) not in RULE_3_4_TARGET_DIRECTIONS | RULE_8_11_TARGET_DIRECTIONS:
                continue
            applied = True
            if not _blank(row.value("名义值")):
                return "NG", f"测点行{row.row_index}名义值必须为空，数值0不能代替空值。"
            if not self._parts(row, columns):
                return "NG", f"测点行{row.row_index}缺少测量偏差值。"
        return self._done(ctx, applied)

    @rule("类型列内容合规性审核", clause_id="5")
    def rule_5(self, ctx: RuleContext):
        for _sheet, row, _columns in self._rows(ctx, "类型"):
            if _text(row.value("类型")) not in {"cc", "sc", "ac"}:
                return "NG", f"测点行{row.row_index}类型必须为CC、SC或AC。"
        return self._done(ctx)

    @rule("测点号与测点图标记点对应性审核", clause_id="6")
    def rule_6(self, ctx: RuleContext):
        rows = self._rows(ctx, optional=("测点图/截面",))
        points = {_text(row.value("测点号")) for _sheet, row, _columns in rows}
        for sheet, row, columns in rows:
            if "测点图/截面" not in columns:
                continue
            column = columns["测点图/截面"].header_col
            source = self._native_source_tables.get(sheet.title)
            if source is None:
                return "UNABLE", "尚不能核实原件中的全部图示标记。"
            for visual in source.get("visual_evidence", []):
                region = visual.get("range")
                if region is None or region[0] <= row.row_index <= region[2] and region[3] >= column:
                    return "UNABLE", "图示区域含未读取的视觉内容，文字标记不能代替完整图示核对。"
        markers: set[str] = set()
        for _sheet, row, columns in rows:
            if "测点图/截面" in columns:
                tokens = [value for value in re.split(r"[\s,，;；、]+", str(row.value("测点图/截面") or "")) if value]
                for token in tokens:
                    if _text(token) not in points and not re.fullmatch(r"(?:[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*|\d+)", token):
                        return "UNABLE", "图示文字含尚不能确认的标记或注释，需核对完整原件。"
                    markers.add(_text(token))
        if not markers:
            return "UNABLE", "未取得可读取的图示标记，不能用空标记集合判为通过；需核对图像原件。"
        missing = markers - points
        if missing:
            return "NG", "图示测点在测点号中缺失：" + "、".join(sorted(missing))
        return self._done(ctx)

    @rule("特定表类型的偏差值在公差范围内审核", clause_id="7")
    def rule_7(self, ctx: RuleContext):
        applied = False
        for sheet, row, columns in self._rows(ctx, "名义值", "上公差", "下公差", "测量偏差值", "Centering", optional=("IQG",)):
            if not self._is_target_measurement_sheet(sheet.title):
                continue
            applied = True
            values = self._parts(row, columns)
            if not values:
                return "NG", f"测点行{row.row_index}缺少实测值。"
            if any(_text(row.value(key)) == "na" for key in ("上公差", "下公差")):
                return "UNABLE", "本行公差为NA，不能据此宣称已完成定量公差核对。"
            nominal = Decimal(0) if _blank(row.value("名义值")) else _number(row.value("名义值"))
            lower, upper = _number(row.value("下公差")), _number(row.value("上公差"))
            if lower > upper:
                return "NG", "下公差大于上公差。"
            if any(value < nominal + lower or value > nominal + upper for value in values):
                return "NG", f"测点行{row.row_index}存在超出名义值加公差范围的实测值。"
        return self._done(ctx, applied)

    def _calculation(self, ctx, *, metric):
        applied = False
        for _sheet, row, columns in self._rows(ctx, "主要控制方向", "名义值", "上公差", "下公差", "测量偏差值", "Centering", "Range", optional=("IQG",)):
            if _text(row.value("主要控制方向")) not in RULE_8_11_TARGET_DIRECTIONS or _blank(row.value("测量偏差值")):
                continue
            if any(_text(row.value(key)) == "na" for key in ("上公差", "下公差")):
                continue
            applied = True
            values = self._parts(row, columns)
            upper, lower = _number(row.value("上公差")), _number(row.value("下公差"))
            if upper <= lower:
                return "UNABLE", "公差区间没有正宽度，尚不能完成统计计算。"
            if metric == "Centering":
                nominal = Decimal(0) if _blank(row.value("名义值")) else _number(row.value("名义值"))
                expected = (sum(values) / len(values) - nominal) / (upper - lower)
            else:
                expected = (max(values) - min(values)) / (upper - lower)
            if not _same_value(row, metric, expected):
                return "NG", f"测点行{row.row_index}的{metric}与源数据计算值不一致。"
        return self._done(ctx, applied)

    @rule("中性值（Centering）计算准确性审核", clause_id="8")
    def rule_8(self, ctx: RuleContext):
        return self._calculation(ctx, metric="Centering")

    def _threshold(self, ctx, metric):
        warnings = []
        for _sheet, row, _columns in self._rows(ctx, metric):
            value = abs(_number(row.value(metric)))
            if value > Decimal("0.66"):
                return "NG", f"测点行{row.row_index}的{metric}绝对值大于0.66。"
            if value > Decimal("0.33"):
                warnings.append(str(row.row_index))
        return self._done(ctx, message=f"{metric}绝对值大于0.33且不大于0.66的行：" + "、".join(warnings) if warnings else "")

    @rule("中性值（Centering）阈值判定及预警审核", clause_id="9")
    def rule_9(self, ctx: RuleContext):
        return self._threshold(ctx, "Centering")

    @rule("极差值（Range）计算准确性审核", clause_id="10")
    def rule_10(self, ctx: RuleContext):
        return self._calculation(ctx, metric="Range")

    @rule("极差值（Range）阈值判定及预警审核", clause_id="11")
    def rule_11(self, ctx: RuleContext):
        return self._threshold(ctx, "Range")
