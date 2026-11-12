"""UAES sample inspection report: measurement columns, requirement ranges, sign-off and the bubble chart."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .materials import related_by_content
from .resources import combine
from .resources import inspection as resources

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
# Geometric tolerances (position, concentricity, perpendicularity, roundness, parallelism, profile, run-out).
_GEOMETRIC = ("◎", "⊥", "○", "⌖", "∥", "//", "⌒", "⌓", "↗", "⏥", "⌯", "▱")
_MARKS = {"√", "✓", "✔", "☑", "×", "x", "■", "●"}
_PASSED = {"ok"}
_REJECTED = {"notok", "nok", "ng"}


@dataclass(frozen=True)
class Limits:
    lower: float
    upper: float
    lower_open: bool = False
    upper_open: bool = False

    def holds(self, low: float, high: float) -> bool | None:
        """Whether a reading known to lie in [low, high] is inside; None when only part of it is."""
        inside = (low > self.lower or (low == self.lower and not self.lower_open)) and (high < self.upper or (high == self.upper and not self.upper_open))
        outside = high < self.lower or (high == self.lower and self.lower_open) or low > self.upper or (low == self.upper and self.upper_open)
        return True if inside else False if outside else None


def _text(value) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return text.replace("〈", "<").replace("〉", ">").replace("≦", "≤").replace("≧", "≥")


def limits(requirement) -> Limits | None:
    """The range a requirement as written on the report allows; None when it states no readable range."""
    if isinstance(requirement, (int, float)):
        return None  # A bare figure carries no tolerance.
    text = _text(requirement).strip()
    # Material and function requirements: "Si(10.5-13.5)%", "Cu≤0.1%", "屈服强度(Rp0.2)>130Mpa".
    match = re.search(rf"[(]\s*({_NUMBER})\s*[-~～]\s*({_NUMBER})\s*[)]", text)
    if match and not re.search(r"[±]", text):
        return Limits(float(match.group(1)), float(match.group(2)))
    match = re.search(rf"(≤|<=|<|≥|>=|>)\s*({_NUMBER})", text)
    if match:
        sign, bound = match.group(1), float(match.group(2))
        if sign in {"≤", "<="}:
            return Limits(-math.inf, bound)
        if sign == "<":
            return Limits(-math.inf, bound, upper_open=True)
        return Limits(bound, math.inf, lower_open=sign == ">")
    if any(text.startswith(symbol) for symbol in _GEOMETRIC):
        match = re.match(rf"^\D*?({_NUMBER})", text)
        return Limits(0.0, float(match.group(1))) if match else None
    # Roughness "Ramax12.5", "Rzmax3.2", "RZmax25丨F".
    match = re.match(rf"^R[az]\s*max\s*({_NUMBER})", text, re.I)
    if match:
        return Limits(0.0, float(match.group(1)))
    # Dimensions: an optional multiplicity "2*", a diameter/radius sign, the nominal and its tolerances;
    # a chamfer angle "*45°" follows the length.
    body = re.sub(r"^\*?\s*(?:\d+\s*[*xX×]\s*)?[ΦφØøR]?\s*", "", text)
    body = re.sub(r"\s*\*\s*\d+(?:\.\d+)?\s*°\s*$", "", body).strip()
    match = re.fullmatch(rf"({_NUMBER})\s*±\s*({_NUMBER})", body)
    if match:
        nominal, tolerance = float(match.group(1)), abs(float(match.group(2)))
        return Limits(nominal - tolerance, nominal + tolerance)
    match = re.fullmatch(rf"(\d+(?:\.\d+)?)\s*({_NUMBER})\s*/\s*({_NUMBER})", body)
    if match:
        nominal, first, second = (float(item) for item in match.groups())
        return Limits(nominal + min(first, second), nominal + max(first, second))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", body)
    if match:
        nominal, upper = float(match.group(1)), float(match.group(2))
        return Limits(nominal, nominal + upper)
    return None


def reading(value, number_format: str, percent: bool) -> tuple[float, float] | None:
    """The interval a measured value states: a number, or a bound such as "<0.01%"."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value) * (100 if percent and "%" in number_format else 1)
        return number, number
    text = _text(value).strip().rstrip("%").strip()
    if re.fullmatch(_NUMBER, text):
        return float(text), float(text)
    match = re.fullmatch(rf"(≤|<=|<)\s*({_NUMBER})", text)
    if match:
        return -math.inf, float(match.group(2))
    match = re.fullmatch(rf"(≥|>=|>)\s*({_NUMBER})", text)
    if match:
        return float(match.group(2)), math.inf
    return None


@dataclass
class Report:
    title: str
    sheet: Sheet
    rows: list
    band: list[int]
    ok_column: int | None
    nok_column: int | None


class UaesInspectionAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)

    def reports(self, ctx: RuleContext, title: str) -> list[Report]:
        found = []
        for sheet in Table(ctx).find_sheets(title, on_missing=Decision.IGNORE):
            section = self.section(sheet, f"{title}区")
            header = section.resolved.header_row
            supplier = section.column(f"{title}供应商实测值")
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, header, supplier.header_col)
            band = list(range(supplier.header_col, (merged.max_col if merged else supplier.header_col) + 1))
            evaluation = section.column(f"{title}评价")
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, header, evaluation.header_col)
            ok_column = nok_column = None
            if merged and merged.max_col > merged.min_col:
                # "Evaluation" spans an OK column and a NOT OK column named on the next header row.
                for column in range(merged.min_col, merged.max_col + 1):
                    label = compact(sheet.cell(merged.max_row + 1, column).value()).rstrip(".")
                    if label in _PASSED:
                        ok_column = column
                    elif label in _REJECTED:
                        nok_column = column
                if ok_column is None or nok_column is None:
                    raise unable(f"“{sheet.title}”Evaluation下未能定位OK与NOT OK两栏，检查程序未能读取，需人工复核。")
            else:
                ok_column = evaluation.header_col
            rows = list(section.rows(f"{title}序号", f"{title}要求"))
            found.append(Report(title, sheet, rows, band, ok_column, nok_column))
        return found

    def all_reports(self, ctx: RuleContext) -> list[Report]:
        found, missing = [], []
        for title in resources.REPORTS:
            reports = self.reports(ctx, title)
            if not reports:
                missing.append(title)
            found.extend(reports)
        if missing:
            if self.untabled() or self.unread_pages():
                raise unable(f"PDF原件未能读出表格，尚不能确认是否包含{'、'.join(missing)}，需人工复核。")
            raise _Missing("文件缺少" + "、".join(missing) + "。")
        return found

    @staticmethod
    def requirement_rows(report: Report) -> list:
        return [row for row in report.rows if compact(row.value(f"{report.title}要求"))]

    @rule("尺寸报告供应商实测列为3的正整数倍")
    def rule_1(self, ctx: RuleContext):
        reports = self.reports(ctx, "尺寸报告")
        if not reports:
            if self.untabled() or self.unread_pages():
                raise unable("PDF原件未能读出表格，尚不能确认是否包含尺寸报告，需人工复核。")
            return "NG", "缺少尺寸报告。"
        notes = []
        for report in reports:
            # A measured column is a column of the supplier band that holds a reading on a requirement row.
            filled = [column for column in report.band if any(report.sheet.cell(row.row_index, column).has_data() for row in report.rows)]
            count = len(filled)
            where = f"尺寸报告“{report.sheet.title}”"
            if not count:
                return "NG", f"{where}供应商实测值栏没有任何实测值。"
            # The rule document's remark hands more than five columns to a reviewer.
            if count > 5:
                raise unable(f"{where}供应商实测值有{count}列，超过5列，需人工判断是否覆盖多产线/多模穴。")
            if count % 3:
                return "NG", f"{where}供应商实测值列数为{count}，不是3或3的倍数。"
            notes.append(f"{where}供应商实测值列数为{count}")
        return "PASS", "；".join(notes) + "。"

    @rule("四报告全部要求实测范围与同行OK")
    def rule_2(self, ctx: RuleContext):
        try:
            reports = self.all_reports(ctx)
        except _Missing as missing:
            return "NG", str(missing)
        violations, unknown, near, checked = [], [], [], 0
        for report in reports:
            for row in self.requirement_rows(report):
                checked += 1
                where = f"“{report.sheet.title}”第{row.row_index}行"
                requirement = row.value(f"{report.title}要求")
                verdict = self.verdict(report, row.row_index)
                if verdict is False:
                    violations.append(f"{where}结果不是OK")
                    continue
                if verdict is None:
                    unknown.append(f"{where}结果栏没有可确认的OK")
                range_ = limits(requirement)
                if range_ is None:
                    if re.search(r"\d", _text(requirement)):
                        unknown.append(f"{where}要求“{requirement}”的范围未能读取")
                    continue
                percent = "%" in _text(requirement)
                values, outside = [], []
                for column in report.band:
                    cell = report.sheet.cell(row.row_index, column)
                    if not cell.has_data():
                        continue
                    interval = reading(cell.value(), cell.number_format(), percent)
                    if interval is None:
                        unknown.append(f"{where}实测值“{cell.value()}”未能读取为数值")
                        continue
                    held = range_.holds(*interval)
                    if held is False:
                        outside.append(str(cell.value()))
                    elif held is None:
                        unknown.append(f"{where}实测值“{cell.value()}”尚不能确定是否在要求“{requirement}”内")
                    values.append(interval[1])
                if outside:
                    violations.append(f"{where}要求“{requirement}”，实测值{'、'.join(outside)}超出要求范围")
                if not values:
                    unknown.append(f"{where}要求“{requirement}”没有供应商实测值")
                elif math.isfinite(range_.lower) and math.isfinite(range_.upper) and range_.upper > range_.lower:
                    if all(value >= range_.upper - (range_.upper - range_.lower) * 0.1 for value in values):
                        near.append(f"{where}“{requirement}”")
        if violations:
            return "NG", "；".join(violations[:8]) + ("等" if len(violations) > 8 else "") + "。"
        if unknown:
            raise unable("检查程序未能确认：" + "；".join(unknown[:8]) + ("等" if len(unknown) > 8 else "") + "，需人工复核。")
        if not checked:
            raise unable("四份报告的要求列下都没有内容，没有可核对的要求项，需人工复核。")
        note = f"实测值均接近要求上限（{'、'.join(near[:5])}），请关注过程能力分析报告。" if near else ""
        return "PASS", f"四份报告共{checked}项要求的实测值均在要求范围内且结果为OK。{note}"

    def verdict(self, report: Report, row: int) -> bool | None:
        """True when the row's result is OK, False when it is NOT OK, None when nothing confirms either."""
        assert report.ok_column is not None
        chosen = compact(report.sheet.cell(row, report.ok_column).value()).rstrip(".")
        if report.nok_column is None:
            return True if chosen in _PASSED else False if chosen in _REJECTED else None
        rejected = compact(report.sheet.cell(row, report.nok_column).value()).rstrip(".")
        if rejected in _REJECTED or rejected in _MARKS:
            return False
        if rejected:
            return None
        return True if chosen in _PASSED or chosen in _MARKS else None

    @rule("四报告适用要求与供应商负责人实际签字")
    def rule_3(self, ctx: RuleContext):
        try:
            reports = self.all_reports(ctx)
        except _Missing as missing:
            return "NG", str(missing)
        applicable = [report for report in reports if self.requirement_rows(report)]
        if not applicable:
            raise unable("四份报告的要求列下都没有内容，尚不能确定需签字的报告，需人工复核。")
        # A signature is a picture or handwriting on the original: each report's sign-off area is looked at.
        observed = {f"signed_{index}": self.signature_area(report) for index, report in enumerate(applicable)}
        fields = {
            f"signed_{index}": f"工作表“{report.sheet.title}”表格下方“供应商负责人/Supplier responsible signature”处有实际签名（手写或签名图片）；只有标签或空白时为false。"
            for index, report in enumerate(applicable)
            if observed[f"signed_{index}"] is None
        }
        if fields:
            observed |= self._from_legacy(lambda agent: agent.visual_pdf(fields, existential_fields=tuple(fields)))
        unsigned = [report.sheet.title for index, report in enumerate(applicable) if observed.get(f"signed_{index}") is False]
        if unsigned:
            return "NG", "“" + "”、“".join(unsigned) + "”有要求项，但供应商负责人处没有实际签字。"
        unknown = [report.sheet.title for index, report in enumerate(applicable) if observed.get(f"signed_{index}") is not True]
        if unknown:
            raise unable("“" + "”、“".join(unknown) + "”供应商负责人签字尚未从原件图像核实，需人工复核。")
        return "PASS", "有要求项的报告（" + "、".join(report.sheet.title for report in applicable) + "）供应商负责人处均有实际签字。"

    def signature_area(self, report: Report) -> bool | None:
        """Whether the image of a worksheet's sign-off rows shows a signature beside 供应商负责人; None when not seen."""
        path = str(self.audit_file.file_url)
        if path.lower().endswith(".pdf") or report.sheet.ws.parent is not self._workbook_cache:
            return None
        from documents.excel.visual import ExcelRegionRenderError
        from openpyxl.utils.cell import get_column_letter

        from audit.checker_lib.excel import load_workbook
        from audit.checker_lib.visual import observe_excel_region

        rows, columns = self.extent(report.sheet)
        labels = [
            cell for cell in report.sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if any(compact(marker) in compact(cell.value()) for marker in resources.SIGNATURE_MARKERS)
        ]
        if not labels:
            return None
        first, last = min(cell.row_index for cell in labels), max(cell.row_index for cell in labels)
        cell_range = f"A{max(1, first - 2)}:{get_column_letter(columns)}{min(rows, last + 2)}"
        cache = self.__dict__.setdefault("_signature_regions", {})
        if "workbook" not in cache:
            cache["workbook"] = load_workbook(path)
        keywords = tuple(dict.fromkeys(str(cell.value()).strip()[:80] for cell in labels))[:8]
        field = {"signed": "图中“供应商负责人 / Supplier responsible signature”处（标签右侧或下方）有实际签名（手写笔迹或签名图片）；只有标签文字或空白时为false。"}
        try:
            observed = observe_excel_region(
                self.audit_file, cache["workbook"][report.sheet.title], cell_range, field, keywords, cache=cache.setdefault("regions", {}), witness=self.legacy().witness, whole_field=True
            )
        except (ExcelRegionRenderError, ValueError, KeyError):
            return None
        value = observed.get("signed")
        return value if isinstance(value, bool) else None

    @rule("同物料号及名称的独立气泡图")
    def rule_4(self, ctx: RuleContext):
        part, name = self.identity(ctx)
        found = related_by_content(self, ("气泡图", "bubble"))
        if found.missing:
            return "NG", "同批次没有气泡图独立文件，请复审或提交气泡图文件一起审核。"
        raise unable(f"同批次有{len(found.items)}份可能的气泡图（{'、'.join(found.names)}），检查程序尚未声明气泡图的读取方式，未能核对物料编号“{part}”与物料名称“{name}”，需人工复核。")

    def identity(self, ctx: RuleContext) -> tuple[str, str]:
        """The part number and name written in the report headers ("Part Number/零件号： F01R10YA40")."""
        parts: set[str] = set()
        names: set[str] = set()
        for title in resources.REPORTS:
            for sheet in Table(ctx).find_sheets(title, on_missing=Decision.IGNORE):
                parts.update(value for value in self.labelled(sheet, ("Part Number/零件号",)) if value)
                names.update(value for value in self.labelled(sheet, ("Part Name/零件名称",)) if value)
        return "、".join(sorted(parts)) or "未写", "、".join(sorted(names)) or "未写"


class _Missing(Exception):
    """One of the four reports is absent from a readable original."""
