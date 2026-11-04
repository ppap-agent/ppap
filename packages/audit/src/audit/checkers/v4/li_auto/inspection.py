"""L01 shares inspection parsing but never treats an unparsed range as passed."""

import math
from dataclasses import replace

from audit.checkers.v2.dimension_measurement_excel_agent import (
    DimensionMeasurementExcelAgent,
    _is_doc01_sequence_number,
    _iter_doc01_main_sections,
    _observe_doc01_row,
)
from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.dimension_measurement_policy import check_duplicates, check_tolerance, inspect_measurements, is_strict_ok_text
from audit.engine.rule_engine import Decision, GuardStop, Row, Sheet
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, compact


class LiAutoInspectionAgent(NativeFacts):
    EXPECTED_RULE_CATEGORIES = (
        "标题识别检查",
        "重要度标记检查",
        "尺寸区数值公差带检查",
        "检查方法/工具列检查",
        "尺寸区同行重复值检查",
        "非尺寸数据检查",
        "尺寸区OK数据方法匹配检查",
        "关键字段识别检查",
    )
    DOC_TYPE = PpapDocumentType.DIMENSION_MEASUREMENT_REPORT
    rule_1 = DimensionMeasurementExcelAgent.rule_1
    rule_2 = DimensionMeasurementExcelAgent.rule_2
    rule_8 = DimensionMeasurementExcelAgent.rule_8

    @staticmethod
    def _inspection_rows(ctx, required):
        rows = []
        for section in _iter_doc01_main_sections(ctx):
            optional = tuple(name for name in ("序号", "检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", "判定结果") if name not in required)
            columns = section.columns(*required, optional=optional)
            business = [name for name in ("检查项目", "判定标准", "供方检查方法/检查工具", "检查数据", "判定结果") if name in columns]
            group_markers = dict(ctx.section_group_markers(section.resolved, "区域"))
            section_count = 0
            for index in range(max(column.data_start_row for column in columns.values()), section.resolved.ws.max_row + 1):
                row = Row(ctx, section.resolved.ws, columns, index, section.resolved)
                if row.is_header():
                    continue
                if index in group_markers:
                    physical = [compact(cell.value) for cells in section.resolved.ws.iter_rows(min_row=index, max_row=index) for cell in cells if compact(cell.value)]
                    if physical and all(value == group_markers[index] for value in physical):
                        continue
                project = compact(row.value("检查项目")) if "检查项目" in columns else ""
                serial = compact(row.value("序号")) if "序号" in columns else ""
                if project == "检查项目" and serial == "序号":
                    continue
                other = [name for name in business if name != "检查项目"]
                has_other = any(row.is_present(name) for name in other)
                if "检查数据" in columns and "判定结果" in columns:
                    has_other = has_other or any(compact(section.resolved.ws.cell(index, column).value) for column in range(columns["检查数据"].header_col, columns["判定结果"].header_col))
                if not has_other and (serial in {"尺寸", "非尺寸"} or project in {"尺寸", "非尺寸"}):
                    continue
                if not has_other and (
                    serial.rstrip(":：") in {"车型", "零件名称", "零件号", "供应商", "责任人", "日期"} or project.rstrip(":：") in {"车型", "零件名称", "零件号", "供应商", "责任人", "日期"}
                ):
                    break
                # Source clauses do not require a serial number or project
                # label before examining an otherwise populated business row.
                if has_other or project or _is_doc01_sequence_number(serial):
                    rows.append((row, columns))
                    section_count += 1
            ctx.note_scan(yielded=section_count, finished=True)
        if not rows:
            raise GuardStop(Decision.UNABLE, "未取得可核实的检查数据行。")
        return rows

    @staticmethod
    def _measurements(row, columns):
        if "检查数据" not in columns or "判定结果" not in columns:
            return ()
        first, last = columns["检查数据"].header_col, columns["判定结果"].header_col
        if last <= first:
            raise GuardStop(Decision.UNABLE, "无法确定检查数据样本列的真实边界。")
        values = []
        for column in range(first, last):
            merged = row.ctx.ops.get_detailed_merge_info(row.ws, row.row_index, column)
            if merged and merged.min_col != merged.max_col:
                raise GuardStop(Decision.UNABLE, "检查数据横向合并跨越多个样本位，尚不能确定真实样本归属。")
            values.append(Sheet(row.ctx, row.ws).cell(row.row_index, column).value())
        return tuple(values)

    @staticmethod
    def _observation(row, columns):
        observed = _observe_doc01_row(row, columns)
        return replace(observed, raw_measurements=LiAutoInspectionAgent._measurements(row, columns))

    @rule("尺寸区数值公差带检查")
    def rule_3(self, ctx):
        checked = 0
        for row, columns in self._inspection_rows(ctx, ("检查数据", "判定标准", "判定结果")):
            observation = self._observation(row, columns)
            if observation.section_group != "尺寸":
                continue
            if not observation.raw_measurements:
                return "NG", "尺寸行缺少检查数据。"
            kind, groups = inspect_measurements(observation.raw_measurements)
            if (
                kind == "mixed_or_invalid"
                or any(isinstance(value, bool) or value is None or not str(value).strip() for value in observation.raw_measurements)
                or any(not math.isfinite(value) for group in groups for value in group or ())
            ):
                return "UNABLE", "检查数据存在不可可靠解析或非有限数值，本条尚未核实。"
            decision = check_tolerance(observation.standard_text, observation.raw_measurements, observation.result_text, observation.method_text)
            if decision.status == "NG":
                if "无法解析" in decision.reason:
                    return "UNABLE", decision.reason
                return "NG", decision.reason
            if not decision.numeric_checked:
                return "UNABLE", "本行尚未完成数值公差核对，不能按通用通过处理。"
            checked += 1
        return ("PASS", "") if checked else ("NA", "没有适用的尺寸数值行。")

    @rule("检查方法/工具列检查")
    def rule_4(self, ctx):
        for row, _columns in self._inspection_rows(ctx, ("供方检查方法/检查工具",)):
            value = compact(row.value("供方检查方法/检查工具"))
            if not value:
                return "UNABLE", "检查方法/工具未填写，尚不能核实该项。"
            if "/" in value or "或" in value:
                return "NG", "检查方法/工具不得包含斜线或“或”。"
        return "PASS", ""

    @rule("尺寸区同行重复值检查")
    def rule_5(self, ctx):
        checked = 0
        for row, columns in self._inspection_rows(ctx, ("检查数据", "判定结果")):
            observation = self._observation(row, columns)
            if observation.section_group != "尺寸":
                continue
            if not observation.raw_measurements:
                return "UNABLE", "尺寸行没有可比较的数据。"
            kind, groups = inspect_measurements(observation.raw_measurements)
            if (
                kind == "mixed_or_invalid"
                or any(isinstance(value, bool) or value is None or not str(value).strip() for value in observation.raw_measurements)
                or any(not math.isfinite(value) for group in groups for value in group or ())
            ):
                return "UNABLE", "检查数据存在不可可靠解析或非有限数值，尚不能核对重复值。"
            if kind != "numeric_values":
                continue
            decision = check_duplicates(observation.raw_measurements)
            if decision.status == "NG":
                return "NG", decision.reason
            checked += 1
        return ("PASS", "") if checked else ("NA", "没有适用尺寸行。")

    @rule("非尺寸数据检查")
    def rule_6(self, ctx):
        checked = 0
        for row, columns in self._inspection_rows(ctx, ("检查数据", "判定结果")):
            if row.group("区域") != "非尺寸":
                continue
            values = self._measurements(row, columns)
            if not values:
                return "NG", "非尺寸行没有检查数据。"
            checked += 1
            if any(not is_strict_ok_text(value) for value in values):
                return "NG", "非尺寸数据只接受OK或合格。"
        return ("PASS", "") if checked else ("UNABLE", "未取得非尺寸数据。")

    @rule("尺寸区OK数据方法匹配检查")
    def rule_7(self, ctx):
        applied = False
        for row, columns in self._inspection_rows(ctx, ("检查数据", "判定结果", "供方检查方法/检查工具")):
            observation = self._observation(row, columns)
            if observation.section_group != "尺寸":
                continue
            if any(compact(value).casefold() == "ok" for value in observation.raw_measurements):
                applied = True
                if compact(observation.method_text) not in {"通止规", "菲林尺", "卡板"}:
                    return "NG", "尺寸检查数据出现OK时，方法须为通止规、菲林尺或卡板。"
        return ("PASS", "") if applied else ("NA", "没有尺寸OK定性数据。")

    def precheck(self):
        from .precheck_l1 import precheck_inspection

        return precheck_inspection(self)
