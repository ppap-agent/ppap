"""UAES engineering requirements: process capability studies (U06) and process flow charts (U16)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .resources import capability, combine, pgqp1
from .resources import flowchart as flow_resources

_INSPECTION = re.compile(flow_resources.INSPECTION_ACTIVITY, re.I)
_PGQP1 = "E12"
_PLACEHOLDERS = {"", "/", "-", "—", "na", "n/a", "无"}
# A target written as one of these states that the characteristic has none (A40 目录 "/").
_NO_TARGET = {"/", "-", "—"}
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class UaesProcessFlowAgent(UaesExcelAgent):
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})
    DOCUMENT_MODULE = flow_resources
    EXTRA_RESOURCE = combine(flow_resources.DOCUMENT)

    # Definitions come from the remarks column of the UAES rule document.
    KEY_MARKS = {
        "key_operation": "全图至少有一道工序带实际关键工序标记：K、K1…、★、◆、加粗、底色、虚框等任一形式。",
        "characteristic_marks": "产品/过程特性中的关键、重要或特殊特性有实际符号标注（CC、SC、YC、YS、★等）；不能只有未标注的文字描述。",
        "legend": "原件图例或注释定义了上述每种关键标记的含义。",
    }
    SYMBOL_FIELDS = {
        "standard_shapes": "全部工序活动（加工/操作、移动/搬运、存储/存取/贮存、检验、返工/返修、报废）用标准化流程图形表达，不能只是纯文字框或列表；各图形含义以本图图例为准。",
        "complete_legend": "图例覆盖了全图实际使用的全部符号。",
        "consistent": "同一活动类型在全图使用相同符号。",
        "inspection_shapes": "所有进货、过程、最终、巡检、自检活动使用检验符号，且可与图例核对。",
    }

    def chart(self, ctx: RuleContext):
        """The flow chart read from cells; None for a PDF whose pages carry it only as an image."""
        from .flowchart import FlowChart

        if self.untabled() or (not Table(ctx).find_sheets("过程流程图", on_missing=Decision.IGNORE) and self.unread_pages()):
            return None
        return FlowChart(ctx, str(self.audit_file.file_url))

    @rule("关键工序及特性标识与图例")
    def rule_1(self, ctx: RuleContext):
        chart = self.chart(ctx)
        marked = None if chart is None else chart.key_operation()
        if marked is False:
            return "NG", f"过程流程图“{chart.sheet.title}”全部工序没有关键工序标记（K、★、◆、加粗、底色或虚框均未使用），条款第一项不满足。"
        # Drawn marks and the characteristic symbols beside them exist only as images of the original.
        fields = {name: text for name, text in self.KEY_MARKS.items() if not (marked and name == "key_operation")}
        return self.visual(fields, "关键工序、特性标记和标记定义")

    @rule("全图活动符号一致性与检验图例")
    def rule_2(self, ctx: RuleContext):
        chart = self.chart(ctx)
        if chart is None:
            return self.visual(self.SYMBOL_FIELDS, "全图标准符号、完整图例、一致性及检验符号")
        where = f"过程流程图“{chart.sheet.title}”"
        operations = chart.operations
        for operation in operations:
            if not operation.symbols:
                if operation.unread_lane:
                    raise unable(f"{where}工序“{operation.description}”（第{operation.rows[0]}行）的符号列有未读图形，检查程序尚不能确认其符号，需人工复核。")
                return "NG", f"{where}工序“{operation.description}”（第{operation.rows[0]}行）没有标准图形符号，只有文字。"
        legend = chart.legend()
        if not legend:
            if chart.has_pictures():
                raise unable(f"{where}的单元格中没有符号图例，原件的图片或图形中可能有图例，需人工复核。")
            return "NG", f"{where}没有符号图例。"
        undefined = sorted({symbol for operation in operations for symbol in operation.symbols} - set(legend))
        if undefined:
            return "NG", f"{where}的图例没有定义所用符号：" + "、".join(undefined)
        for column, name in chart.typed_lanes().items():
            used = {symbol for operation in operations for lane, symbol in operation.marks if lane == column}
            if len(used) > 1:
                return "NG", f"{where}“{name}”列使用了多种符号：{'、'.join(sorted(used))}，同类活动符号混用。"
        # Inspection activities, as decided for this clause: inspection and test operations.
        inspected = [operation for operation in operations if _INSPECTION.search(operation.description)]
        symbols = [symbol for symbol, name in legend.items() if re.search(flow_resources.INSPECTION_NAME, name, re.I)]
        if inspected and len(symbols) != 1:
            if not symbols:
                return "NG", f"{where}的图例没有定义检验符号，检验类活动无法以检验符号表达。"
            raise unable(f"{where}的图例中有多个检验符号（{'、'.join(symbols)}），尚不能确定检验类活动应使用哪一个，需人工复核。")
        for operation in inspected:
            if symbols[0] not in operation.symbols:
                if operation.unread_lane:
                    raise unable(f"{where}工序“{operation.description}”的符号列有未读图形，检查程序尚不能确认其符号，需人工复核。")
                return "NG", f"{where}检验类工序“{operation.description}”（第{operation.rows[0]}行）未使用检验符号{symbols[0]}，实际符号为{''.join(operation.symbols)}。"
        return "PASS", "全部工序以图例定义的符号表达，各活动列符号一致，检验类工序使用检验符号。"

    @rule("过程流程图实际受控章")
    def rule_3(self, ctx: RuleContext):
        from .flowchart import carries_pictures

        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            try:
                pictures = carries_pictures(path)
            except Exception:
                pictures = True
            # A stamp is a picture or the words "受控"; a spreadsheet with neither has none.
            if not pictures and not self.mentions_controlled(ctx):
                return "NG", "原件没有任何图片（媒体、图片库或页眉页脚中均无），单元格中也没有“受控”字样，过程流程图没有受控章。"
        return self.stamp()

    def mentions_controlled(self, ctx: RuleContext) -> bool:
        for worksheet in ctx.workbook.worksheets:
            sheet = Sheet(ctx, worksheet)
            rows, columns = self.extent(sheet)
            if any("受控" in compact(cell.value()) for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns)):
                return True
        return False


@dataclass
class Report:
    """One capability certification report: each index read as a number, or why it could not be."""

    where: str
    values: dict[str, Decimal] = field(default_factory=dict)
    gaps: dict[str, str] = field(default_factory=dict)
    # The readings count is an upper bound: every number in the measured-results area.
    bounded: bool = False


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = "".join(str(value).split()).lstrip(">＞≥=")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


class UaesCapabilityAgent(UaesExcelAgent):
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})
    DOCUMENT_MODULE = capability
    EXTRA_RESOURCE = combine(capability.DOCUMENT, pgqp1.DOCUMENT, capability.PGQP1_PROCESS)

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .native import UaesSourceAgent
        from .precheck_l1 import precheck_capacity

        return precheck_capacity(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    @property
    def pdf(self) -> bool:
        return str(self.audit_file.file_url).lower().endswith(".pdf")

    def unreadable_pdf(self) -> bool:
        return self.pdf and (self.untabled() or bool(self.unread_pages()))

    # Certification reports.

    def reports(self, ctx: RuleContext) -> list[Report]:
        found = []
        for sheet in Table(ctx).find_sheets("能力报告", on_missing=Decision.IGNORE):
            if (statistics := self._optional_section(sheet, "能力统计区")) is not None:
                found.append(self.statistics_report(sheet, statistics))
            elif self._optional_section(sheet, "实测数据区") is not None:
                found.extend(self.grid_reports(sheet))
        if self.pdf and found:
            self.pdf_indices(found)
        return found

    @staticmethod
    def _optional_section(sheet: Sheet, name: str):
        try:
            return sheet.section(name)
        except GuardStop:
            return None

    def statistics_report(self, sheet: Sheet, section) -> Report:
        report = Report(f"“{sheet.title}”")
        labels = {name: {compact(alias) for alias in aliases} for name, aliases in (("读数次数", capability.READINGS), ("Cpk", capability.CPK), ("Ppk", capability.PPK))}
        value_column = section.column("统计值").header_col
        for row in section.rows("统计项", "统计值"):
            label = compact(row.value("统计项"))
            for name, wanted in labels.items():
                if label not in wanted or name in report.values:
                    continue
                raw = row.value("统计值")
                number = _number(raw)
                if number is not None:
                    report.values[name] = number
                    report.gaps.pop(name, None)
                elif raw in (None, "") and self.uncomputed(sheet, row.row_index, value_column):
                    report.gaps[name] = f"{name}（第{row.row_index}行）是没有计算结果的公式"
                else:
                    report.gaps[name] = f"{name}（第{row.row_index}行）为“{raw if raw is not None else ''}”，不是数值"
        for name in labels:
            if name not in report.values:
                report.gaps.setdefault(name, f"未找到{name}的统计行")
        return report

    def grid_reports(self, sheet: Sheet) -> list[Report]:
        """574 report pages stacked in one sheet: each "team | No.1 …" grid starts a report."""
        rows, columns = self.extent(sheet)
        team = {compact(alias) for alias in sheet.ctx.resource.columns["组号"].aliases}
        starts = [cell.row_index for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(cell.value()) in team]
        reports = []
        for index, start in enumerate(starts):
            end = starts[index + 1] - 1 if index + 1 < len(starts) else rows
            subgroups = [cell.col_index for cell in sheet.scan_non_empty(row_min=start, row_max=start, col_min=1, col_max=columns) if re.fullmatch(capability.SUBGROUP, str(cell.value()).strip())]
            label_column = next(cell.col_index for cell in sheet.scan_non_empty(row_min=start, row_max=start, col_min=1, col_max=columns) if compact(cell.value()) in team)
            count = 0
            for row in range(start + 1, end + 1):
                merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, label_column)
                label = "" if merged and merged.min_row != row else str(sheet.cell(row, label_column).value() or "").strip()
                # The control-limit and analysis rows after the samples are not readings.
                if label and not compact(label).startswith(capability.SAMPLE_ROW):
                    break
                for column in subgroups:
                    merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, column)
                    if merged and (merged.min_row, merged.min_col) != (row, column):
                        continue
                    count += len(_NUMBER.findall(str(sheet.cell(row, column).value() or "")))
            report = Report(f"“{sheet.title}”第{start}行起的实测数据", bounded=True)
            if count:
                report.values["读数次数"] = Decimal(count)
            else:
                # No number read is a reading gap, never a count of zero.
                report.gaps["读数次数"] = f"“{sheet.title}”第{start}行起的实测数据区未读出任何数值"
            reports.append(report)
        return reports

    def pdf_indices(self, reports: list[Report]) -> None:
        """Cpk and Ppk of PDF report pages, which state them outside the ruled table (the text layer)."""
        import fitz

        pages = []
        with fitz.open(str(self.audit_file.file_url)) as document:
            for page in document:
                text = page.get_text()
                if capability.PDF_CPK in text and capability.PDF_PPK in text:
                    pages.append((page.number + 1, text))
        for report in reports:
            if not report.bounded:
                continue
            for name in ("Cpk", "Ppk"):
                report.gaps[name] = f"PDF文字层的报告页数（{len(pages)}）与读出的实测数据表数（{len(reports)}）不一致，{name}不能对应到报告"
        if len(pages) != len(reports):
            return
        for report, (number, text) in zip(reports, pages):
            report.where = f"第{number}页"
            for name, label in (("Cpk", capability.PDF_CPK), ("Ppk", capability.PDF_PPK)):
                values = re.findall(re.escape(label) + r"\s*(-?\d+(?:\.\d+)?)", text)
                if len(values) == 1:
                    report.values[name] = Decimal(values[0])
                    report.gaps.pop(name, None)
                else:
                    report.gaps[name] = f"第{number}页文字层中“{label}”后有{len(values)}个数值"

    def require_reports(self, ctx: RuleContext) -> list[Report]:
        reports = self.reports(ctx)
        if not reports:
            if self.unreadable_pdf():
                raise unable("PDF原件有未读出表格或没有文字层的页面，检查程序未能定位工艺能力认证报告，需人工复核。")
            raise unable("检查程序未能在原件中定位工艺能力认证报告（未找到 DESCRIPTIVE STATISTICS/VALUES 统计表或 team/No.1 实测数据表），需人工复核。")
        return reports

    @rule("所有能力认证报告的样本数与严格Cpk阈值")
    def rule_1(self, ctx: RuleContext):
        gaps = []
        for report in self.require_reports(ctx):
            readings = report.values.get("读数次数")
            if readings is not None and readings < 125:
                if report.bounded:
                    return "NG", f"工艺能力认证报告{report.where}的实测结果区域（No.1–No.25列）最多只有{readings}个数值，读数次数与实测结果区域数量均少于125。"
                return "NG", f"工艺能力认证报告{report.where}读数次数（Number of readings）为{readings}，少于125。"
            cpk = report.values.get("Cpk")
            if cpk is not None and cpk <= Decimal("1.33"):
                return "NG", f"工艺能力认证报告{report.where}Process Capability (Cpk)为{cpk}，必须大于1.33。"
            if readings is None or report.bounded:
                gaps.append(f"{report.where}：" + report.gaps.get("读数次数", "实测结果区域只能确定数值上限，读数次数不能确认"))
            if cpk is None:
                gaps.append(f"{report.where}：" + report.gaps.get("Cpk", "未读到Cpk"))
        if gaps:
            raise unable("检查程序未能读取全部工艺能力认证报告的读数次数与Cpk：" + "；".join(gaps[:3]) + "，需人工复核。")
        return "PASS", "全部工艺能力认证报告读数次数不少于125，Cpk均大于1.33。"

    @rule("所有能力认证报告的严格Ppk阈值")
    def rule_2(self, ctx: RuleContext):
        gaps = []
        for report in self.require_reports(ctx):
            ppk = report.values.get("Ppk")
            if ppk is None:
                gaps.append(f"{report.where}：" + report.gaps.get("Ppk", "未读到Ppk"))
            elif ppk <= Decimal("1.67"):
                return "NG", f"工艺能力认证报告{report.where}Performance index (Ppk)为{ppk}，必须大于1.67。"
        if gaps:
            raise unable("检查程序未能读取全部工艺能力认证报告的Ppk：" + "；".join(gaps[:3]) + "，需人工复核。")
        return "PASS", "全部工艺能力认证报告Ppk均大于1.67。"

    # The capability summary list (目录 of 776/A40, the 574 "Ppk能力值汇总" page).

    @rule("能力汇总逐行结果目标及不足项措施")
    def rule_3(self, ctx: RuleContext):
        sheets = Table(ctx).find_sheets("能力汇总表", on_missing=Decision.IGNORE)
        if not sheets:
            if self.unreadable_pdf():
                raise unable("PDF原件有未读出表格或没有文字层的页面，尚不能确认是否有能力值汇总清单，需人工复核。")
            return "NG", "文件中不存在能力值汇总清单（列出CPK/PPK结果值与目标值的表格）。"
        checked = 0
        for sheet in sheets:
            section = self.section(sheet, "能力汇总区")
            columns = section.columns(optional=("能力特性", "目标CPK", "结果CPK", "目标PPK", "结果PPK", "反应计划", "汇总备注"))
            pairs = [(result, target) for result, target in (("结果CPK", "目标CPK"), ("结果PPK", "目标PPK")) if result in columns and target in columns]
            if not pairs:
                raise unable(f"能力汇总“{sheet.title}”中未能定位成对的结果值与目标值列，检查程序未能读取，需人工复核。")
            anchor = next(name for name in ("能力特性", "结果PPK", "结果CPK") if name in columns)
            for row in section.rows_from(columns):
                if not row.is_present(anchor):
                    continue
                checked += 1
                low = []
                for result, target in pairs:
                    wanted_raw = row.value(target)
                    # A characteristic without a stated target ("/") has nothing to compare against.
                    if compact(wanted_raw) in _NO_TARGET:
                        continue
                    wanted, actual_raw = _number(wanted_raw), row.value(result)
                    actual = _number(actual_raw)
                    if wanted is None or actual is None:
                        column = columns[result if actual is None else target].header_col
                        if self.uncomputed(sheet, row.row_index, column):
                            raise unable(f"能力汇总“{sheet.title}”第{row.row_index}行{result if actual is None else target}是没有计算结果的公式，需人工复核。")
                        raise unable(f"能力汇总“{sheet.title}”第{row.row_index}行{target}“{wanted_raw}”/{result}“{actual_raw}”不能读为数值，需人工复核。")
                    if actual < wanted:
                        low.append(f"{result}{actual}低于{target}{wanted}")
                if low:
                    notes = [row.value(name) for name in ("反应计划", "汇总备注") if name in columns]
                    if not any(compact(note) for note in notes):
                        return "NG", f"能力汇总“{sheet.title}”第{row.row_index}行{'，'.join(low)}，同行没有反应计划或备注。"
        if not checked:
            raise unable("能力汇总没有可确认的特性行，不能按空范围通过，需人工复核。")
        return "PASS", "能力汇总全部特性行达到目标，或低于目标的行已有反应计划/备注。"

    # The directory against the same-part PGQP1.

    @rule("能力目录覆盖PGQP1特殊特性")
    def rule_4(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=False)

    @rule("能力目录覆盖PGQP1全部过程特性")
    def rule_5(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=True)

    def pgqp_directory(self, ctx: RuleContext, *, process_only: bool):
        directories = Table(ctx).find_sheets("能力目录", on_missing=Decision.IGNORE)
        plans = self.batch_files(_PGQP1)
        missing, unknown = [], []
        if not directories:
            if self.pdf and self.pdf_mentions("目录"):
                unknown.append("原件文字提到“目录”，但检查程序未能读出目录表格")
            elif self.unreadable_pdf():
                unknown.append("PDF原件有未读出表格或没有文字层的页面，尚不能确认是否有目录页")
            else:
                missing.append("过程能力分析报告没有目录页，请补充过程能力分析报告的目录页内容")
        if not plans:
            missing.append("同批次没有采购件质量计划（PGQP1）独立文件，缺项文件")
        if missing:
            return "NG", "；".join(missing) + "。"
        if unknown:
            raise unable("；".join(unknown) + "，需人工复核。")
        if len(directories) != 1:
            raise unable(f"过程能力分析报告有多个目录页（{'、'.join(sheet.title for sheet in directories)}），尚不能确定按哪一个核对，需人工复核。")
        directory = directories[0]
        parts = {compact(value) for value in self.labelled(directory, capability.DIRECTORY_PART)} - _PLACEHOLDERS
        if len(parts) != 1:
            raise unable(f"目录“{directory.title}”的零件号码{'未填写' if not parts else '有多个'}，检查程序不能确认同零件号的采购件质量计划，需人工复核。")
        part = next(iter(parts))
        names, process_rows = self.directory_rows(directory)
        others = []
        for item in plans:
            with self.reading(ctx, item, pgqp1):
                sheets = Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE)
                theirs = {compact(value) for sheet in sheets for value in self.labelled(sheet, capability.PGQP1_PART)} - _PLACEHOLDERS
                if not sheets:
                    others.append("（无PGQP1表）")
                    continue
                if len(theirs) != 1:
                    raise unable("同批次采购件质量计划PGQP1的零件号未能唯一读取，检查程序不能确认是否同零件号，需人工复核。")
                if theirs != {part}:
                    others.append(next(iter(theirs)))
                    continue
                if len(sheets) != 1:
                    raise unable("同零件号的采购件质量计划有多个PGQP1表，尚不能确定按哪一个核对，需人工复核。")
                return self.covered(sheets[0], directory, names, process_rows, process_only=process_only)
        return "NG", f"同批次没有零件号为{part}且包含PGQP1表的采购件质量计划（同批次采购件质量计划：{'、'.join(others)}），缺项文件。"

    def directory_rows(self, directory: Sheet) -> tuple[set[str], list[int] | None]:
        """Characteristic names of the directory, and the rows it marks as process characteristics."""
        section = self.section(directory, "能力汇总区")
        columns = section.columns(optional=("能力特性", "过程特性标记"))
        if "能力特性" not in columns:
            raise unable(f"目录“{directory.title}”中未能定位特性名称列（工程名），检查程序未能读取目录条目，需人工复核。")
        names: set[str] = set()
        process_rows: list[int] = []
        for row in section.rows_from(columns):
            if not row.is_present("能力特性"):
                continue
            names.add(compact(row.value("能力特性")))
            if "过程特性标记" in columns and compact(row.value("过程特性标记")):
                process_rows.append(row.row_index)
        return names, process_rows if "过程特性标记" in columns else None

    def covered(self, plan: Sheet, directory: Sheet, names: set[str], process_rows, *, process_only: bool):
        if process_only:
            if process_rows is None:
                raise unable(f"目录“{directory.title}”中未能定位特别特性的“过程”列，检查程序不能确认哪些条目是过程特性，需人工复核。")
            section = self.section(plan, "关键工序区")
            processes = [str(row.value("关键工艺")).strip() for row in section.rows("关键工艺", "工艺描述") if row.is_present("关键工艺")]
            if not processes:
                raise unable("同零件号PGQP1的关键工序表中没有可确认的过程，不能按空范围通过，需人工复核。")
            if process_rows:
                raise unable(f"目录“{directory.title}”有{len(process_rows)}个标为过程特性的条目，但PGQP1关键工序只描述工艺要求，检查程序不能逐项对应，需人工复核。")
            listed = f"同零件号PGQP1“2.Special Process关键工序”列出{len(processes)}道关键工序（{'、'.join(processes)}）的过程特性"
            return "NG", f"目录“{directory.title}”没有任何标为过程特性的条目（特别特性“过程”列全空），而{listed}，均未进行过程能力研究。"
        section = self.section(plan, "重要特性区")
        special = []
        for row in section.rows("特性项目", "特殊特性"):
            mark = str(row.value("特殊特性") or "").strip()
            if not row.is_present("特性项目") or not mark:
                continue
            if re.search(r"\d", mark):
                raise unable(f"PGQP1“SC/特殊特性”列第{row.row_index}行写的是“{mark}”（特性值），不是特殊特性标记，检查程序不能确定哪些特性被标记为特殊特性，需人工复核。")
            special.append((row.row_index, str(row.value("特性项目")).strip()))
        if not special:
            raise unable("同零件号PGQP1没有被标记为特殊特性的特性，不能按空范围通过，需人工复核。")
        for row_index, name in special:
            if compact(name) not in names:
                return "NG", f"PGQP1第{row_index}行特殊特性“{name}”不在过程能力分析报告目录“{directory.title}”中。"
        return "PASS", f"同零件号PGQP1的{len(special)}项特殊特性均在目录“{directory.title}”中。"
