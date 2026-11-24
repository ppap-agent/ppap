"""UAES U05 candidate for v1.0 revision 3 (MSA:1–MSA:6): measurement system plan, reports and their coverage.

A leaf carries only checker.py, resources.py and rules.json and may not import the UAES implementation:
resources.py declares the UAES MSA reader as the carrier, and at run time `Audit` extends that reader class with
the revision 3 clause methods below (`MsaClauses`), reusing its sheet reading, cell citing and same-round access.
Verdicts are code over facts read from the originals (cells, the PDF text layer); no model is asked to judge.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table
from resources import RESOURCE


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def original_name(item: Any) -> str:
    """The name the user uploaded a same-round original under."""
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


_MSA, _PGQP1, _CP, _LISTS = "测量系统分析评价", "E12", "控制计划", ("过程特殊特性清单", "T12")
_PART = re.compile(r"(?<![0-9A-Za-z])F[0-9A-Z]{9}(?![0-9A-Za-z])")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# The placeholder list of MSA:1/MSA:2, compared after width folding, spaces removed and case folded.
_PLACEHOLDER_WORDS = (
    *("见附表", "见附件", "待定", "TBD", "NA", "N/A", "—", "-", "/", "同上", "见上", "略", "*", "待补", "后续提供", "XX", "XXXX", "123456"),
    *("#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "□", "■"),
)
_PLACEHOLDERS = {compact(word) for word in _PLACEHOLDER_WORDS}
# MSA:1: on core fields 1–4 these four are declared "not applicable" marks, not blanks.
_DECLARED_NA = {compact(word) for word in ("/", "N/A", "—", "无")}

# Plan headings: the clause's own words plus the spellings read from UAES plans.
_PLAN_COLUMNS = {
    "序号": ("序号", "no", "no.", "itemno", "item"),
    "零件号": ("零件号", "零件编号", "partno", "partnumber", "适用范围", "项目名", "机种名", "机种", "产品名称", "零件名称"),
    "特性名称": ("测量项目", "测量参数", "测量特性", "特性名称"),
    "量具名称": ("量具名称", "测量工具名称", "测量设备名称", "工具名称", "设备名称", "gaugename", "gagename"),
    "量具编号": ("量具编号", "工具编号", "设备编号", "设备代码", "量具代码", "gaugeno", "gageno"),
    "计划分析类型": ("计划分析类型", "分析类型", "分析方法", "研究类型", "msa类型", "分析项目"),
}
_CORE = ("零件号", "特性名称", "量具名称", "量具编号", "计划分析类型")
_CORE_LABEL = {"零件号": "零件号/适用范围", "特性名称": "特性名称", "量具名称": "量具名称", "量具编号": "量具编号", "计划分析类型": "计划分析类型"}
_ANALYSIS = ("gr&r", "grr", "r&r", "kappa", "cgk", "稳定性", "偏倚", "线性")
_PLAN_END = ("编制", "审核", "批准", "备注")

# Gauge words. Counting (attribute) gauges per MSA:2; variable gauges as spelled in UAES originals.
_COUNTING = ("人工", "目视", "目测", "通止规", "塞规", "塞尺", "综合检具", "检具", "visual", "p/g")
_VARIABLE = {
    "卡尺": ("卡尺", "caliper", "v/c"),
    "千分尺": ("千分尺", "micrometer"),
    "高度规": ("高度规", "高度尺", "heightgauge"),
    "CMM": ("cmm", "三坐标"),
    "气动量仪": ("气动量仪", "气动测量仪", "气动"),
    "粗糙度仪": ("粗糙度仪", "粗糙度"),
    "百分表": ("百分表",),
    "千分表": ("千分表",),
    "投影仪": ("投影",),
    "轮廓仪": ("轮廓仪",),
    "圆度仪": ("圆度仪",),
}
_ATTRIBUTE = {"塞规": ("塞规", "p/g", "pluggauge"), "通止规": ("通止规",), "塞尺": ("塞尺",), "环规": ("环规",), "螺纹规": ("螺纹规",), "检具": ("综合检具", "检具")}
_HUMAN = ("人工", "目视", "目测", "visual", "感官")

_ACCEPT = ("可接受", "合格", "通过", "ok", "acceptable", "gaugeisok", "gageisok", "measurementsystemisacceptable", "good", "pass", "满足要求", "符合要求", "能接受", "可以接受", "canaccept")
_MARGINAL = ("marginal", "临界", "near", "conditional", "有条件接受", "有条件")
_REJECT = ("不", "not", "poor", "unaccept", "reject", "improve", "改进", "改善", "fail", "bad", "需整改")

_CONCLUSION_LABEL = re.compile(r"^(结论|conclusion|conclude|判定|judgement|judgment)")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _c(value: Any) -> str:
    return compact(_text(value)).replace("＆", "&")


def _number(text: str) -> Decimal | None:
    match = _NUMBER.search(_text(text).replace(",", ""))
    if match is None:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _fmt(value: Decimal | None) -> str:
    return "无" if value is None else format(round(value, 4).normalize(), "f")


def verdict(text: str) -> str:
    """accept, marginal, reject or unknown for one written conclusion (MSA:1's list of equivalent wordings)."""
    raw = unicodedata.normalize("NFKC", text).casefold()
    words = compact(raw).replace("＆", "&")
    if not words:
        return "unknown"
    if any(term in words for term in _MARGINAL):
        return "marginal"
    if any(term in words for term in _REJECT) or re.search(r"\bng\b", raw):
        return "reject"
    if any(term in words for term in _ACCEPT):
        return "accept"
    return "unknown"


def gauge_family(name: str) -> str | None:
    """The gauge a written name stands for (卡尺, CMM, 塞规 …); None when the name names none of them."""
    words = _c(name)
    found = {canonical for table in (_VARIABLE, _ATTRIBUTE) for canonical, spellings in table.items() if any(spelling in words for spelling in spellings)}
    if "检具" in found and found - {"检具"}:
        found.discard("检具")
    return next(iter(found)) if len(found) == 1 else None


def gauge_kind(name: str) -> str:
    """counting, variable, mixed (written across both kinds: MSA:2 hands it to engineering) or unknown."""
    words = _c(name)
    counting = any(term in words for term in _COUNTING)
    variable = any(spelling in words for spellings in _VARIABLE.values() for spelling in spellings)
    # "通止塞规" reads as a go/no-go gauge or as a combined fixture: written across kinds.
    if ("通止" in words and "通止规" not in words) or "+" in words:
        return "mixed"
    if counting and variable:
        return "mixed"
    return "counting" if counting else "variable" if variable else "unknown"


def _dimension(text: str) -> tuple[bool, Decimal, str] | None:
    # A single engineering value only. Geometric tolerance frames, callout IDs, lists,
    # and different units need their own readings, not a first-number comparison.
    m = re.fullmatch(r"(?:粗糙度)?(rzmax|[φ⌀ø]?)(\d+(?:\.\d+)?)(mm|[μu]m)?(±\d+(?:\.\d+)?|h\d+)?", text)
    if not m:
        return None
    roughness = m[1] == "rzmax"
    if (roughness and m[3] == "mm") or (not roughness and m[3] in ("um", "μm")):
        return None
    return roughness, Decimal(m[2]), m[4] or ""


def _spec_identity(value: str):
    # The same nominal (e.g. Ø34.22 and Φ34.220) can occur on distinct features.
    # Don't grant coverage twice just because two source cells spell it differently.
    text = _c(value)
    parsed = _dimension(text)
    return parsed[:2] if parsed else text


def _same_gauge(left: str, right: str) -> bool:
    a, b = _c(left), _c(right)
    if not a or not b:
        return False
    if a == b:
        return True
    fa, fb = gauge_family(left), gauge_family(right)
    return fa is not None and fa == fb


@dataclass
class Report:
    """One analysis report (a GR&R report, a Kappa/attribute study or a Cg/Cgk sheet) of one MSA original."""

    kind: str  # GRR, Kappa, Attr (counting study without a Kappa value), Cgk
    item: Any
    where: str
    parts: set[str] = field(default_factory=set)
    names: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    characteristic: str = ""
    studied: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    subject_codes: frozenset[str] = frozenset()
    study_cells: tuple = ()
    text: str = ""
    title: str = ""
    conclusions: list[str] = field(default_factory=list)
    grr: Decimal | None = None
    ndc: Decimal | None = None
    kappas: list[Decimal] = field(default_factory=list)
    overall: list[Decimal] = field(default_factory=list)
    signature: tuple = ()
    place: Any = None

    @property
    def file(self) -> str:
        return original_name(self.item)

    @property
    def label(self) -> str:
        return f"《{self.file}》{self.where}"

    def named(self, gauge: str) -> bool:
        return any(_same_gauge(gauge, name) for name in self.names)

    def of_part(self, part: str) -> bool:
        """Whether the report is of exactly this part: its part number as written (or, for a plan that names its
        scope instead of a number, that scope written in the report)."""
        key = _c(part)
        if not key or key in _DECLARED_NA:
            return True
        return key.upper() in self.parts or (not _PART.fullmatch(key.upper()) and key in self.text)


@dataclass
class PlanRow:
    item: Any
    sheet: str
    row: int
    values: dict[str, str]
    place: Any = None

    @property
    def gauge(self) -> str:
        return self.values.get("量具名称", "")


class MsaClauses:
    """Revision 3 clause methods over the declared UAES MSA reader (`READER` is its module)."""

    READER: Any = None

    # ---- reading ---------------------------------------------------------------------------------

    def grid(self, ctx: RuleContext, ws: Any) -> dict[tuple[int, int], str]:
        """Every written cell of a sheet as text. A formula error elsewhere on the sheet (a scratch area) is plain text
        here, not an unreadable fact of the clause; the cells a basis relies on are cited on their own."""
        rows, columns = self.extent(Sheet(ctx, ws))
        found = {}
        for r, values in enumerate(ws.iter_rows(min_row=1, max_row=rows, max_col=columns, values_only=True), 1):
            for c, value in enumerate(values, 1):
                if text := _text(value):
                    found[(r, c)] = text
        return found

    @staticmethod
    def label_cells(grid: dict, aliases: tuple[str, ...]) -> list[tuple[int, int, str]]:
        """Values written right of each label cell, or after "label:" in the same cell (the reader's rule, on raw text)."""
        wanted = {compact(alias) for alias in aliases}
        values = []
        for (row, column), text in sorted(grid.items()):
            if compact(text) in wanted:
                found = next((c for c in range(column + 1, column + 9) if grid.get((row, c))), None)
                if found is not None:
                    values.append((row, found, grid[(row, found)]))
                continue
            match = re.match(r"\s*(.+?)\s*[:：]\s*(.+)", text, re.S)
            if match and compact(match.group(1)) in wanted:
                values.append((row, column, match.group(2).strip()))
        return values

    @classmethod
    def glabel(cls, grid: dict, aliases: tuple[str, ...]) -> list[str]:
        return [value for _, _, value in cls.label_cells(grid, aliases)]

    @staticmethod
    def right(grid: dict, row: int, column: int, gap: int = 3) -> list[tuple[int, str]]:
        """Values right of a label in its row, up to the first run of more than `gap` empty columns."""
        values, empty, col = [], 0, column + 1
        last = max((c for r, c in grid if r == row), default=column)
        while col <= last and empty <= gap:
            text = grid.get((row, col))
            if text:
                values.append((col, text))
                empty = 0
            else:
                empty += 1
            col += 1
        return values

    def mine(self, item: Any) -> bool:
        return item is self.audit_file or getattr(item, "audit_id", None) == self.audit_file.audit_id

    def suite(self) -> list[Any]:
        """This file and the same-round MSA originals, in file order (by the uploaded file name)."""
        return sorted([self.audit_file, *self.batch_files(_MSA)], key=lambda item: original_name(item))

    def workbook_of(self, ctx: RuleContext, item: Any):
        """The workbook of `item` read under the MSA resource; this file's own workbook for itself."""
        if self.mine(item):
            from contextlib import nullcontext

            return nullcontext(ctx)
        return self.reading(ctx, item, self.READER.resources)

    def scanned(self, item: Any) -> list[int]:
        if not str(item.file_url).lower().endswith(".pdf"):
            return []
        import fitz

        with fitz.open(str(item.file_url)) as document:
            return [page.number + 1 for page in document if not page.get_text().strip() or any(abs(fitz.Rect(image["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for image in page.get_image_info())]

    # The plan.

    def plans(self, ctx: RuleContext, item: Any) -> tuple[list[PlanRow], list[dict]]:
        """(rows, tables) of every MSA plan table in `item`: a list whose heading row names a gauge and a characteristic."""
        rows: list[PlanRow] = []
        tables: list[dict] = []
        with self.workbook_of(ctx, item):
            for ws in ctx.workbook.worksheets:
                grid = self.grid(ctx, ws)
                for header, columns in self.plan_headers(grid):
                    table = {"sheet": ws.title, "header": header, "columns": columns, "item": item, "extra": {}}
                    tables.append(table)
                    last_row = max((r for r, _c in grid), default=header)
                    headings = {c: grid[(header, c)] for r, c in grid if r == header}
                    table["extra"] = {c: text for c, text in headings.items() if c not in columns.values() and "备注" not in _c(text)}
                    for row in range(header + 1, last_row + 1):
                        first = next((grid[(row, c)] for c in sorted(c for r, c in grid if r == row)), "")
                        if any(_c(first).startswith(marker) for marker in _PLAN_END):
                            break
                        values = {name: grid.get((row, column), "") for name, column in columns.items()}
                        if not any(values.get(name) for name in ("序号", *_CORE)):
                            continue
                        values["_extra"] = "；".join(f"{_text(headings[c])}" for c in table["extra"] if not grid.get((row, c)))
                        place = self.READER.Place.of(f"“{ws.title}”第{row}行", ws, row, columns.get("量具名称") or columns.get("序号") or 1) if self.mine(item) else None
                        rows.append(PlanRow(item, ws.title, row, values, place))
        return rows, tables

    @staticmethod
    def plan_headers(grid: dict) -> list[tuple[int, dict[str, int]]]:
        found = []
        for row in sorted({r for r, _c in grid}):
            columns: dict[str, int] = {}
            for (r, column), text in sorted(grid.items()):
                if r != row:
                    continue
                words = _c(text)
                for name, aliases in _PLAN_COLUMNS.items():
                    if name in columns:
                        continue
                    if name == "序号":
                        if words in aliases:
                            columns[name] = column
                            break
                        continue
                    if name == "量具名称" and any(term in words for term in ("编号", "代码")):
                        continue
                    if any(alias in words for alias in aliases):
                        columns[name] = column
                        break
            if "量具名称" in columns and "特性名称" in columns:
                found.append((row, columns))
        return found

    # Reports.

    def reports_of(self, ctx: RuleContext, item: Any) -> list[Report]:
        cache = self.__dict__.setdefault("_r3_reports", {})
        key = getattr(item, "audit_id", id(item))
        if key not in cache:
            if str(item.file_url).lower().endswith(".pdf"):
                cache[key] = self.pdf_reports(item)
            else:
                found = []
                with self.workbook_of(ctx, item):
                    for ws in ctx.workbook.worksheets:
                        report = self.sheet_report(ctx, item, ws)
                        if report is not None:
                            found.append(report)
                cache[key] = found
        return cache[key]

    def all_reports(self, ctx: RuleContext) -> list[Report]:
        return [report for item in self.suite() for report in self.reports_of(ctx, item)]

    def sheet_report(self, ctx: RuleContext, item: Any, ws: Any) -> Report | None:
        grid = self.grid(ctx, ws)
        if not grid:
            return None
        words = [_c(text) for text in grid.values()]
        title = f"{ws.title} " + " ".join(text for (r, _c2), text in sorted(grid.items()) if r <= 4)
        has = lambda *terms: any(term in word for word in words for term in terms)  # noqa: E731
        if any(word.startswith("cgk=") or word == "cgk" for word in words):
            kind = "Cgk"
        elif any(word == "kappa" for word in words) or "kappa" in _c(ws.title):
            kind = "Kappa"
        elif has("计数型") and any(_CONCLUSION_LABEL.match(word) for word in words):
            kind = "Attr"
        elif has("重复性和再现性", "重复性与再现性", "重复性及再现性", "repeatability", "r&r", "grr") and any(word.startswith(("判定", "judgement")) for word in words):
            kind = "GRR"
        else:
            return None
        report = Report(kind, item, f"“{ws.title}”", title=title)
        text = " ".join(grid.values())
        report.text = _c(text)
        report.parts = set(_PART.findall(unicodedata.normalize("NFKC", text).upper()))
        report.names = [value for value in self.glabel(grid, (*self.READER.resources.GAUGE_NAME, "名称 Object", "测量工具名称")) if _c(value) not in _PLACEHOLDERS]
        report.numbers = [value for value in self.glabel(grid, (*self.READER.resources.GAUGE_NUMBER, "编号")) if _c(value) not in _PLACEHOLDERS]
        parameter_cells = self.label_cells(grid, ("测量参数", "Measure parameter", "Measuring parameter", "特性", "特 性", "Denote Requirement", "标注要求"))
        characteristic = [value for _, _, value in parameter_cells]
        report.study_cells = tuple((ws, r, c) for r, c, _ in parameter_cells)
        report.studied = tuple(v for v in characteristic if _c(v) not in _PLACEHOLDERS)
        limits = self.glabel(grid, ("参数规格", "Parameter specification"))
        report.limits = tuple(v for v in limits if _c(v) not in _PLACEHOLDERS)
        characteristic += limits
        subjects = self.glabel(grid, ("零件号", "零件名称", "零件编号", "Part No.", "Part Number", "Part Name", "Product number", "测量对象: Measurement object"))
        report.subject_codes = frozenset(v.strip() for v in subjects if re.fullmatch(r"F[0-9A-Z]{9,}", v.strip()))
        report.characteristic = " ".join(value for value in characteristic if _c(value) not in _PLACEHOLDERS)
        people = self.glabel(grid, ("测量人员", "评价人员", "Analysing staff", "Measure staff", "测量日期", "分析日期", "Analysing date", "Measuring date", "日期", "Date"))
        report.signature = (kind, tuple(sorted(_c(n) for n in report.numbers)), tuple(_c(p) for p in people), _c(report.characteristic), tuple(sorted(_c(n) for n in report.names)))
        report.place = self.READER.Place.of(report.where, ws, 1, 1) if self.mine(item) else None
        for (row, column), cell in sorted(grid.items()):
            word = _c(cell)
            if kind == "GRR":
                if word.startswith(("判定", "judgement")):
                    report.conclusions.extend(value for _col, value in self.right(grid, row, column))
                    if ":" in _text(cell) and (rest := _text(cell).split(":", 1)[1].strip()):
                        report.conclusions.append(rest)
                elif re.match(r"^%(grr|r&r)=", word) and report.grr is None:
                    report.grr = self.formula_value(grid, row, column, percent=True)
                elif re.match(r"^ndc=", word) and report.ndc is None:
                    report.ndc = self.formula_value(grid, row, column)
            elif kind in ("Kappa", "Attr"):
                if _CONCLUSION_LABEL.match(word):
                    values = [value for _col, value in self.right(grid, row, column)]
                    inline = re.split(r"[:：]", _text(cell), maxsplit=1)
                    if len(inline) == 2 and inline[1].strip() and not _CONCLUSION_LABEL.match(_c(inline[1])):
                        values.insert(0, inline[1].strip())
                    report.conclusions.extend(values)
                elif word == "kappa":
                    report.kappas.extend(n for _col, value in self.right(grid, row, column) if (n := _number(value)) is not None and re.fullmatch(r"-?\d+(\.\d+)?", _c(value)))
                elif "kappa" in word and any(term in word for term in ("总体", "整体", "overall")):
                    report.overall.extend(n for _col, value in self.right(grid, row, column) if (n := _number(value)) is not None)
        return report

    @staticmethod
    def formula_value(grid: dict, row: int, column: int, percent: bool = False) -> Decimal | None:
        """The computed value under a formula label: the first number written after an "=" cell in the next rows."""
        for r in range(row + 1, row + 4):
            for c in range(max(1, column - 3), column + 6):
                if _c(grid.get((r, c), "")) == "=" and (value := grid.get((r, c + 1))) is not None and re.fullmatch(r"-?\d+(\.\d+)?%?", _c(value)):
                    number = Decimal(_c(value).rstrip("%"))
                    # A spreadsheet percentage cell stores a fraction (0.0415 = 4.15%).
                    if percent and not _c(value).endswith("%") and number <= 1:
                        number *= 100
                    return number
        return None

    def pdf_reports(self, item: Any) -> list[Report]:
        import fitz

        found: list[Report] = []
        records: dict[str, Report] = {}
        scanned = set(self.scanned(item))
        with fitz.open(str(item.file_url)) as document:
            pages = [(page.number + 1, [line.strip() for line in page.get_text().splitlines() if line.strip()]) for page in document if page.number + 1 not in scanned]
        for number, lines in pages:
            if not lines:
                continue
            text = " ".join(lines)
            head = " ".join(lines[:3])

            def after(label: str, offset: int = 1) -> str:
                index = next((i for i, line in enumerate(lines) if _c(line).rstrip(":") == _c(label)), None)
                return lines[index + offset] if index is not None and index + offset < len(lines) else ""

            if "重复性及再现性分析报告" in head:
                report = Report("GRR", item, f"第{number}页", title=head, text=_c(text))
                report.parts = set(_PART.findall(text.upper()))
                report.names = [after("量具名称")] if after("量具名称") else []
                report.numbers = [after("量具编号")] if after("量具编号") else []
                report.characteristic = after("特 性") or after("特性")
                report.studied = (report.characteristic,) if report.characteristic else ()
                report.conclusions = [after("结论")] if after("结论") else []
                for index, line in enumerate(lines):
                    if _c(line) in ("%r&r=", "%grr=") and index + 1 < len(lines) and re.fullmatch(r"\d+(\.\d+)?%", _c(lines[index + 1])):
                        report.grr = Decimal(_c(lines[index + 1]).rstrip("%"))
                if values := re.findall(r"NDC\s*=\s*(\d+(?:\.\d+)?)\s*$", "\n".join(lines), re.M):
                    report.ndc = Decimal(values[-1])
                report.signature = ("GRR", tuple(report.numbers), _c(report.characteristic), number)
                found.append(report)
            elif "Attribute Report" in head:
                record = re.search(r"Record No:\s*(\S+)", text)
                key = record.group(1) if record else str(number)
                report = records.get(key)
                if report is None:
                    report = Report("Attr", item, f"第{number}页起（Record No {key}）", title=head)
                    report.parts = set(_PART.findall(text.upper()))
                    report.names = [after("Gage Name", 3)] if after("Gage Name", 3) else []
                    gauge_number = after("Gage Number", 3)
                    report.numbers = [gauge_number] if _c(gauge_number) not in _PLACEHOLDERS else []
                    report.characteristic = after("Denote Requirement", 5)
                    report.studied = (report.characteristic,) if report.characteristic else ()
                    report.signature = ("Attr", key)
                    records[key] = report
                    found.append(report)
                report.text += _c(text)
                for index, line in enumerate(lines):
                    word = _c(line)
                    if word == "kappa":
                        for value in lines[index + 1 : index + 4]:
                            if re.fullmatch(r"-?\d+(\.\d+)?", _c(value)):
                                report.kappas.append(Decimal(_c(value)))
                    elif word.startswith("conclude") or word in {"good", "acceptable", "marginal", "unacceptable", "poor", "bad", "ok", "ng", "pass", "fail"}:
                        rest = _text(line)[8:].strip() if word.startswith("conclude") else _text(line)
                        if rest:
                            report.conclusions.append(rest)
                if report.kappas:
                    report.kind = "Kappa"
        return found

    # ---- MSA:1 -----------------------------------------------------------------------------------

    @rule("MSA计划表五类核心字段与控制计划量具覆盖")
    def rule_1(self, ctx: RuleContext):
        rows, tables = self.plans(ctx, self.audit_file)
        if not tables:
            if pages := self.scanned(self.audit_file):
                raise unable(f"原件第{'、'.join(map(str, pages))}页没有文字层（扫描件），尚不能确认本文件是否含测量系统分析计划表，需人工复核。")
            return "NA", "不适用：本文件不含测量系统分析计划表（没有同时含量具名称类与测量项目/测量参数/测量特性类列头的清单表），本条判定对象不在本文件。"
        problems, notes = [], []
        for table in tables:
            missing = [label for name, label in _CORE_LABEL.items() if name not in table["columns"]]
            if missing:
                problems.append(f"计划表“{table['sheet']}”没有{'、'.join(missing)}列（该核心字段在每一行均为空）")
            if "序号" not in table["columns"]:
                problems.append(f"计划表“{table['sheet']}”没有序号/NO列")
        if not rows:
            return "NG", "测量系统分析计划表没有任何有效行。"
        for row in rows:
            where = self.named(row.place) if row.place is not None else f"“{row.sheet}”第{row.row}行"
            if "序号" in row.values and not row.values["序号"]:
                problems.append(f"{where}序号/NO为空")
            for name in _CORE:
                if name not in row.values:
                    continue
                value = _c(row.values[name])
                label = _CORE_LABEL[name]
                if not value:
                    problems.append(f"{where}{label}为空")
                elif name == "计划分析类型":
                    if value in _PLACEHOLDERS or value in _DECLARED_NA:
                        problems.append(f"{where}计划分析类型填的是占位词“{row.values[name]}”")
                    elif not any(term in value for term in _ANALYSIS):
                        problems.append(f"{where}计划分析类型“{row.values[name]}”未写明GR&R、Kappa、Cgk、稳定性、偏倚、线性中的任一种")
                elif value in _PLACEHOLDERS and value not in _DECLARED_NA:
                    problems.append(f"{where}{label}填的是占位词“{row.values[name]}”")
            if row.values.get("_extra"):
                notes.append(f"{where}{row.values['_extra']}为空")
        coverage, skipped = self.cp_coverage(ctx, rows)
        if coverage:
            problems.append(coverage)
        hint = ("；提示：" + "；".join(notes[:3]) + ("等" if len(notes) > 3 else "") + "（非核心字段，不影响结论）") if notes else ""
        if skipped:
            hint += f"；提示：控制计划中以下测量技术未纳入量具覆盖核对（不是本条所列可变数据量具或计数型专用检具，或属全自动设备/破坏性化学、金相试验）：{'、'.join(skipped[:8])}"
        if problems:
            return "NG", "；".join(problems[:6]) + ("等" if len(problems) > 6 else "") + "。" + hint
        return "PASS", f"测量系统分析计划表全部{len(rows)}个有效行的序号及五类核心字段均已填写，计划分析类型明确，控制计划中的量具均有对应行。" + hint

    def cp_coverage(self, ctx: RuleContext, rows: list[PlanRow]) -> tuple[str, list[str]]:
        """(the control plan gauges without a plan row, the techniques left out of the coverage check)."""
        plans = self.batch_files(_CP)
        if not plans:
            return "", ["（同批次没有控制计划，未核对覆盖）"]
        techniques: dict[str, str] = {}
        for plan in plans:
            with self.reading(ctx, plan, self.READER.control_plan):
                for sheet in Table(ctx).find_sheets("控制计划", on_missing=Decision.IGNORE):
                    try:
                        section = sheet.section("控制计划区")
                    except GuardStop:
                        continue
                    for row in section.rows("评价测量技术"):
                        text = _text(row.value("评价测量技术"))
                        if text:
                            techniques.setdefault(text, original_name(plan))
        if not techniques:
            raise unable("同批次控制计划未读到“评价测量技术”列内容，尚不能核对计划表的量具覆盖，需人工复核。")
        planned = {gauge_family(row.gauge) or _c(row.gauge) for row in rows}
        missing, skipped = [], []
        for technique in techniques:
            words = _c(technique)
            if any(term in words for term in ("金相", "化学", "光谱", "自动", "传感", "设备参数")):
                skipped.append(technique)
                continue
            families = {canonical for table in (_VARIABLE, _ATTRIBUTE) for canonical, spellings in table.items() if any(spelling in words for spelling in spellings)}
            if "检具" in families and families - {"检具"}:
                families.discard("检具")
            if not families:
                skipped.append(technique)
                continue
            for family in sorted(families):
                if family not in planned and family not in missing:
                    missing.append(family)
        if missing:
            return f"控制计划“测量技术/评价方法”列使用的{'、'.join(missing)}在测量系统分析计划表中没有对应行", skipped
        return "", skipped

    # ---- MSA:2 -----------------------------------------------------------------------------------

    def suite_plan(self, ctx: RuleContext) -> tuple[list[PlanRow], Any, list[str]]:
        """(the union of the suite's plan rows, the suite's main file, files whose plan pages are scanned)."""
        rows: list[PlanRow] = []
        main, scanned = None, []
        seen: dict[tuple, PlanRow] = {}
        for item in self.suite():
            found, tables = self.plans(ctx, item)
            if tables and main is None:
                main = item
            if not tables and self.scanned(item):
                scanned.append(original_name(item))
            for row in found:
                key = (_c(row.values.get("序号")), _c(row.gauge))
                if key not in seen:
                    seen[key] = row
                    rows.append(row)
        return rows, main or self.suite()[0], scanned

    def check_report(self, report: Report) -> tuple[str, str] | None:
        """A report's own failure (NG), or an UNABLE reason, or None when its conclusion and indices pass."""
        verdicts = [verdict(value) for value in report.conclusions]
        if report.kind == "Cgk":
            return None
        if not report.conclusions:
            return "UNABLE", f"{report.label}未读到结论"
        if "marginal" in verdicts:
            return "NG", f"{report.label}结论为有条件接受/临界类“{'；'.join(report.conclusions)}”，须整改后复评，按不通过处理"
        if "reject" in verdicts:
            return "NG", f"{report.label}结论“{'；'.join(report.conclusions)}”不是可接受"
        if "unknown" in verdicts:
            odd = [value for value, v in zip(report.conclusions, verdicts) if v == "unknown"]
            return "UNABLE", f"{report.label}结论“{'；'.join(odd)}”不能确定是否为可接受的写法"
        if report.kind == "GRR":
            if report.grr is None:
                return "UNABLE", f"{report.label}结论可接受，但未能读出%GRR，底层指标是否达标尚未核实"
            if report.grr > 30:
                return "NG", f"{report.label}%GRR为{report.grr.normalize()}%，大于30%，即使结论写可接受也按数据失真判不通过"
            if report.grr > 10 and not any(term in report.text for term in ("客户批准", "客户认可", "整改说明", "整改措施")):
                return "NG", f"{report.label}%GRR为{report.grr.normalize()}%（10%～30%），未附客户批准或整改说明"
            if report.ndc is None:
                return "UNABLE", f"{report.label}未能读出ndc，底层指标是否达标尚未核实"
            if report.ndc < 5 and "豁免" not in report.text:
                return "NG", f"{report.label}ndc为{report.ndc.normalize()}，小于5且无公差带豁免说明"
        if report.kind == "Kappa":
            values = report.overall or report.kappas
            if not values:
                return "UNABLE", f"{report.label}未能读出Kappa值，底层指标是否达标尚未核实"
            if low := [value for value in values if value < Decimal("0.75")]:
                return "NG", f"{report.label}Kappa为{'、'.join(str(v) for v in low)}，低于0.75，即使结论写可接受也按数据失真判不通过"
        return None

    @staticmethod
    def checked(report: Report) -> str:
        """What was actually read and checked on a passing report, in the report's own terms."""
        said = "；".join(report.conclusions)
        if report.kind == "GRR":
            return f"{report.label}结论“{said}”，%GRR {_fmt(report.grr)}%，ndc {_fmt(report.ndc)}"
        if report.kind == "Kappa":
            values = report.overall or report.kappas
            which = "总体Kappa" if report.overall else "Kappa值"
            return f"{report.label}结论“{said}”，{which}{'、'.join(_fmt(v) for v in values)}均不低于0.75"
        return f"{report.label}结论“{said}”（计数型分析，没有Kappa值）"

    def file_gauges(self, ctx: RuleContext, item: Any) -> list[str]:
        """The gauges a suite file is about: those its reports name, then the one its file name names."""
        found = [gauge_family(name) or _c(name) for report in self.reports_of(ctx, item) for name in report.names]
        if family := gauge_family(Path(original_name(item)).stem):
            found.append(family)
        return list(dict.fromkeys(found))

    def gauge_file(self, ctx: RuleContext, key: str, wanted: str, main: Any) -> Any:
        """The file a gauge's defect belongs to (MSA:2: the file of that gauge): a file with a report of the wanted
        kind naming it, else a file with any report naming it, else a file named after it, else the suite's main file."""
        suite = self.suite()
        named = lambda report: any((gauge_family(name) or _c(name)) == key for name in report.names)  # noqa: E731
        for test in (
            lambda item: any(report.kind == wanted and named(report) for report in self.reports_of(ctx, item)),
            lambda item: any(named(report) for report in self.reports_of(ctx, item)),
            lambda item: key in self.file_gauges(ctx, item),
        ):
            if hit := next((item for item in suite if test(item)), None):
                return hit
        return main

    @rule("计划量具与GRR/Kappa报告匹配及报告结论与指标")
    def rule_2(self, ctx: RuleContext):
        plan, main, scanned = self.suite_plan(ctx)
        own = [report for report in self.reports_of(ctx, self.audit_file) if report.kind != "Cgk"]
        failures, opens, notes, passed = [], [], [], []
        for report in own:
            result = self.check_report(report)
            if result is None:
                passed.append(self.checked(report))
            elif result[0] == "NG":
                failures.append(result[1])
            else:
                opens.append(result[1])
        if without := [report for report in own if report.kind == "Kappa" and not report.overall and len(report.kappas) > 1]:
            notes.append(f"{without[0].label}{'等' + str(len(without)) + '份Kappa研究' if len(without) > 1 else ''}没有总体Kappa，已逐项核对读到的各个Kappa值（含单个评价者的分项值）")
        suite_level = self.mine(main)
        reports = self.all_reports(ctx)
        elsewhere: list[str] = []  # Defects of other files' gauges: not in this file's scope.
        if suite_level:
            if not plan:
                if scanned:
                    opens.append(f"同批次《{'》《'.join(scanned)}》有没有文字层的页面，尚不能确认是否有测量系统分析计划表")
                else:
                    failures.append("文件内及同批次文件中没有测量系统分析计划表")
            if not any(report.kind in ("GRR", "Kappa") for report in reports):
                failures.append("文件内及同批次文件中没有重复性及再现性（GR&R）报告或Kappa报告")
        groups: dict[str, list[PlanRow]] = {}
        for row in plan:
            gauge = row.gauge
            where = f"《{original_name(row.item)}》“{row.sheet}”第{row.row}行"
            key = gauge_family(gauge) or _c(gauge)
            if not _c(gauge) or _c(gauge) in _PLACEHOLDERS:
                if suite_level:
                    notes.append(f"{where}量具名称{'为空' if not _c(gauge) else '为占位词“' + gauge + '”'}，该项未审")
                continue
            if gauge_kind(gauge) == "mixed":
                if self.mine(self.gauge_file(ctx, key, "", main)):
                    opens.append(f"{where}量具名称“{gauge}”跨计数型与计量型，无法判定属哪一类，移交工程裁定")
                continue
            groups.setdefault(key, []).append(row)
        matched_here: list[str] = []
        for key, rows in groups.items():
            gauge = rows[0].gauge
            counting = gauge_kind(gauge) == "counting"
            wanted = "Kappa" if counting else "GRR"
            part = rows[0].values.get("零件号", "")
            # A counting gauge is matched only by a Kappa study; an attribute sheet without Kappa is not one.
            matched = [r for r in reports if r.kind == wanted and r.named(gauge) and r.of_part(part) and _c(r.characteristic)]
            distinct = {r.signature for r in matched}
            where = "、".join(f"第{row.row}行" for row in rows)
            label = "以评价者为对象的Kappa研究" if counting else "GR&R报告"
            owner = self.gauge_file(ctx, key, wanted, main)
            defect = ""
            if not matched:
                near = [r for r in reports if r.named(gauge)]
                why = ""
                if near:
                    kinds = {"GRR": "GR&R报告", "Kappa": "Kappa研究", "Attr": "计数型分析（无Kappa值）", "Cgk": "Cg/Cgk报告"}
                    described = []
                    for r in near[:3]:
                        said = "“" + r.characteristic + "”" if _c(r.characteristic) else "未写"
                        described.append(f"{r.label}为{kinds[r.kind]}，零件号{'、'.join(sorted(r.parts)) or '未写'}，所判特性{said}")
                    why = "（同名报告：" + "；".join(described) + "）"
                defect = f"计划表{where}“{gauge}”（零件号{rows[0].values.get('零件号') or '未写'}）没有零件号与量具名称匹配、且写明所判特性的{label}{why}"
            elif len(distinct) < len(rows):
                defect = f"同类量具报告份数不足：计划表列出{len(rows)}台“{gauge}”（{where}），匹配的{label}只有{len(distinct)}份"
            if self.mine(owner):
                if defect:
                    failures.append(defect)
                else:
                    matched_here.append(f"计划表{where}“{gauge}”有{len(distinct)}份零件号、量具名称匹配且写明所判特性的{label}（{'、'.join(dict.fromkeys(r.label for r in matched))}）")
            elif defect:
                elsewhere.append(f"“{gauge}”的缺陷归《{original_name(owner)}》")
        if elsewhere:
            notes.append("计划表中" + "；".join(elsewhere) + "，不在本文件归属范围内")
        tail = ("；提示：" + "；".join(notes[:4])) if notes else ""
        if failures:
            return "NG", "；".join(failures[:5]) + ("等" if len(failures) > 5 else "") + "。" + tail
        if opens:
            raise unable("；".join(opens[:4]) + "，需人工复核。" + tail)
        done = []
        if passed:
            done.append("本文件报告" + "；".join(passed))
        done.extend(matched_here)
        if not done:
            done.append("本文件没有GR&R或Kappa报告，计划表中也没有归本文件的量具缺陷")
        return "PASS", "；".join(done) + "。" + tail

    # ---- MSA:3 -----------------------------------------------------------------------------------

    def pgqp1_rows(self, ctx: RuleContext, item: Any, *, exact_part: bool = False) -> tuple[list[tuple[int, str, str, str]], set[str], bool]:
        """((row, item, SC value, equipment) of the important-characteristic list, part numbers, whether a PGQP1 sheet exists)."""
        rows, parts, sheets = [], set(), False
        with self.reading(ctx, item, self.READER.pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                sheets = True
                parts |= {(value.strip() if exact_part else _c(value).upper()) for value in self.glabel(self.grid(ctx, sheet.ws), self.READER.capability.PGQP1_PART) if _c(value) not in _PLACEHOLDERS}
                for row in self.section(sheet, "重要特性区").rows("特性项目", "特殊特性", "测量设备"):
                    name, special, gauge = _text(row.value("特性项目")), _text(row.value("特殊特性")), _text(row.value("测量设备"))
                    # A heading repeated on a later page is not a characteristic.
                    if any(term in _c(value) for value in (name, special, gauge) for term in ("characteristics", "items/项目", "sc/特殊特性", "measuringequipment", "对供应商的措施")):
                        continue
                    if name or special or gauge:
                        rows.append((row.row_index, name, special, gauge))
                        self.__dict__.setdefault("_feature_cells", {})[f"PGQP1第{row.row_index}行"] = [(sheet.ws, row.row_index, row.columns[col].header_col) for col in ("特殊特性", "测量设备")]
        return rows, parts, sheets

    @rule("PGQP1重要特性计数型量具的Kappa独立文件")
    def rule_3(self, ctx: RuleContext):
        plans = self.batch_files(_PGQP1)
        if not plans:
            self.need("采购件质量计划（PGQP1）")
            return "NG", "同批次没有包含PGQP1表的《采购件质量计划》文件。"
        if len(plans) > 1:
            raise unable("同批次有多份采购件质量计划，尚不能确定按哪一份核对，需人工复核。")
        rows, parts, sheets = self.pgqp1_rows(ctx, plans[0])
        if not sheets:
            return "NG", "同批次《采购件质量计划》中没有PGQP1表。"
        counting, mixed = [], []
        for row, name, special, gauge in rows:
            kind = gauge_kind(gauge)
            if kind == "mixed":
                mixed.append(f"第{row}行“{name}”测量设备“{gauge}”")
            elif kind == "counting" and any(term in _c(gauge) for term in ("通止规", "塞规", "塞尺", "检具")):
                counting.append((row, name, special, gauge))
        if not counting:
            if mixed:
                raise unable("PGQP1" + "；".join(mixed[:3]) + "写法无法判定是否属计数型，移交工程裁定。")
            return "PASS", "PGQP1重要特性中没有通止规、塞规、塞尺等计数型量具项目，本条无判定对象。"
        if len(parts) != 1:
            raise unable(f"采购件质量计划PGQP1的零件号未能唯一读取（读到：{'、'.join(sorted(parts)) or '无'}），尚不能确认同零件号的Kappa文件，需人工复核。")
        part = next(iter(parts))
        kappas = [report for report in self.all_reports(ctx) if report.kind == "Kappa"]
        missing = []
        for row, name, special, gauge in counting:
            same = [report for report in kappas if part in {p.upper() for p in report.parts}]
            covered = [report for report in same if any(term and term in report.text for term in (_c(name), _c(special)))]
            if not covered:
                if not kappas:
                    why = "同批次没有Kappa研究"
                elif not same:
                    why = f"同批次没有零件号为{part}的Kappa研究"
                else:
                    why = f"零件号为{part}的Kappa研究（{'、'.join(r.label for r in same[:3])}）没有载明该特性"
                missing.append(f"PGQP1第{row}行“{name}”（{special}）使用{gauge}，{why}")
        tail = ("；另有" + "；".join(mixed[:2]) + "写法无法判定是否属计数型，移交工程裁定") if mixed else ""
        if missing:
            self.need("Kappa报告", part=part)
            return "NG", "；".join(missing[:4]) + ("等" if len(missing) > 4 else "") + "。" + tail
        if mixed:
            raise unable("PGQP1" + "；".join(mixed[:3]) + "写法无法判定是否属计数型，移交工程裁定，需人工复核。")
        return "PASS", f"PGQP1重要特性中{len(counting)}个计数型量具项目均有同零件号{part}、载明所判特性的Kappa研究。"

    # ---- MSA:4 -----------------------------------------------------------------------------------

    @rule("控制计划出货检验/GP12人为检测的Kappa独立文件")
    def rule_4(self, ctx: RuleContext):
        plans = self.batch_files(_CP)
        if not plans:
            self.need("控制计划")
            return "NG", "同批次没有《控制计划》。"
        results = []
        for plan in plans:
            results.append(self.human_checks(ctx, plan))
        status = "NG" if any(s == "NG" for s, _r in results) else "UNABLE" if any(s == "UNABLE" for s, _r in results) else "PASS"
        reason = "；".join(f"《{original_name(plan)}》：{r}" for plan, (s, r) in zip(plans, results) if s == status or status == "PASS")
        if status == "UNABLE":
            raise unable(reason)
        return status, reason

    def human_checks(self, ctx: RuleContext, plan: Any) -> tuple[str, str]:
        manual, read, named = [], 0, False
        with self.reading(ctx, plan, self.READER.control_plan):
            for sheet in Table(ctx).find_sheets("控制计划", on_missing=Decision.IGNORE):
                try:
                    section = sheet.section("控制计划区")
                    columns = section.columns(optional=("工序名称",))
                except GuardStop:
                    continue
                rows = list(section.rows("评价测量技术"))
                if not rows:
                    continue
                read += len(rows)
                if "工序名称" not in columns:
                    continue
                named = True
                owners = self.READER.operation_owners(sheet, columns["工序名称"].header_col, range(rows[0].row_index, rows[-1].row_index + 1), set())
                for row in rows:
                    names, method = owners.get(row.row_index, ()), _text(row.value("评价测量技术"))
                    outgoing = [name for name in names if any(term in _c(name) for term in ("出货检验", "gp12"))]
                    if outgoing and any(term in _c(method) for term in _HUMAN):
                        manual.append((outgoing[0], method, row.row_index, len(names) > 1))
        if not read:
            return "UNABLE", "控制计划中未读到评价测量技术行，尚不能确认出货检验/GP12工序是否有人为检测"
        if not named:
            return "UNABLE", "控制计划缺工序名称列，属文件结构问题，不能当作没有出货检验工序"
        if not manual:
            return "PASS", "提示：本批次控制计划未发现出货检验/GP12工序，或该类工序没有人工、目视、目测等人为检测方法（客观无检测对象，按条文判通过）"
        try:
            part = self.plan_part(ctx, plan)
        except GuardStop:
            part = None
        if part is None:
            return "UNABLE", "控制计划未能读出唯一的零件号，尚不能确认同零件号的Kappa文件"
        kappas = [report for report in self.all_reports(ctx) if report.kind == "Kappa" and part in {p.upper() for p in report.parts}]
        traced, open_ = [], []
        for operation, method, row, between in manual:
            exact = [r for r in kappas if any(_c(name) == "综合检具" or _c(name) == _c(method) for name in r.names)]
            if exact:
                continue
            accepted = [r for r in kappas if all(verdict(c) == "accept" for c in r.conclusions) and r.conclusions]
            by_operation = [r for r in accepted if any(term in _c(r.title) for term in ("出货", "gp12", "终检"))]
            if by_operation:
                traced.append((row, method, by_operation[0].label))
                continue
            if between:
                open_.append(f"第{row}行“{method}”位于两个工序名称之间且原件没有分隔线，尚不能确定是否属于“{operation}”")
                continue
            self.need("Kappa报告", part=part)
            return "NG", f"控制计划第{row}行“{operation}”采用“{method}”，同批次没有零件号为{part}、量具名称相同或为综合检具、或能对应到该工序检验人员的Kappa独立文件"
        if open_:
            return "UNABLE", "；".join(open_[:3])
        trace = ""
        if traced:
            rows_ = "、".join(f"第{row}行“{method}”" for row, method, _label in traced[:4]) + (f"等{len(traced)}行" if len(traced) > 4 else "")
            trace = f"；留痕提示：{rows_}由{traced[0][2]}覆盖（报告标题指向出货检验/GP12检验人员，与工序名未逐字一致）"
        return "PASS", "控制计划出货检验/GP12的人为检测均有同零件号的Kappa文件覆盖" + trace

    # ---- MSA:5 -----------------------------------------------------------------------------------

    @rule("Cg/Cgk评定区两个指数均不低于1.33")
    def rule_5(self, ctx: RuleContext):
        name = original_name(self.audit_file)
        groups, requirement, judged, places = self.cgk_blocks(ctx)
        header = any(term in _c(name) for term in ("cgk", "能力指数", "capabilityindex")) or bool(groups)
        if not groups and self.pdf_mentions("Cgk"):
            raise unable("PDF原件文字层提到Cgk，检查程序尚不能从PDF读出Cg/Cgk评定区，需人工复核。")
        if not header:
            if pages := self.scanned(self.audit_file):
                raise unable(f"原件第{'、'.join(map(str, pages))}页没有文字层，尚不能确认是否含Cg/Cgk评定区，需人工复核。")
            return "NA", "不适用：本文件不是含Cg/Cgk评定区的测量能力报告（文件名与表头均无Cgk、能力指数、Capability Index）。"
        if not groups:
            return "NG", f"《{name}》属Cg/Cgk测量能力报告，但未找到同一评定区内的Cg与Cgk实测值。"
        threshold = Decimal("1.33")
        notes = []
        if requirement:
            other = [value for value in requirement if value != threshold]
            if other:
                raise unable(f"文件自带要求行的阈值为{'、'.join(map(str, other))}，与1.33不同，登记冲突交客户裁决。")
        failures = []
        for index, (cg, cgk, where) in enumerate(groups, 1):
            if cg is None or cgk is None:
                failures.append(f"{where}评定区只有{'Cgk' if cg is None else 'Cg'}一个指数有实测值（另一个为空、只有标签或公式未算出）")
                continue
            low = [f"{label}={_fmt(value)}" for label, value in (("Cg", cg), ("Cgk", cgk)) if value < threshold]
            if low:
                failures.append(f"{where}{'、'.join(low)}，低于1.33")
        ok = not failures
        if judged is not None and judged != ok:
            notes.append(f"登记冲突：文件“测量系统是否合适”栏标“{'是' if judged else '否'}”，与实测值{'不' if judged else ''}达标矛盾，以实测值为准")
        tail = ("；" + "；".join(notes)) if notes else ""
        if failures:
            return "NG", "；".join(failures) + "。" + tail
        values = "；".join(f"{where}Cg={_fmt(cg)}、Cgk={_fmt(cgk)}" for cg, cgk, where in groups)
        return "PASS", f"{values}，均不低于1.33。" + tail

    def cgk_blocks(self, ctx: RuleContext):
        """([(Cg, Cgk, where)], thresholds on the file's own requirement line, the 是否合适 mark or None, places)."""
        groups, requirement, judged = [], [], None
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            return groups, requirement, judged, []
        for ws in ctx.workbook.worksheets:
            grid = self.grid(ctx, ws)
            cgs, cgks = [], []
            for (row, column), text in sorted(grid.items()):
                word = _c(text)
                if re.fullmatch(r"cg=?", word) or re.fullmatch(r"cgk=?", word):
                    value = next((Decimal(_c(v)) for _col, v in self.right(grid, row, column, gap=6) if re.fullmatch(r"-?\d+(\.\d+)?", _c(v))), None)
                    (cgks if word.startswith("cgk") else cgs).append((row, column, value))
                elif "要求" in word and "cg" in word:
                    requirement.extend(Decimal(n) for n in re.findall(r"\d+\.\d+", _text(text)))
                elif "是否合适" in word:
                    marks = self.right(grid, row, column, gap=8)
                    for index, (col, value) in enumerate(marks):
                        if _c(value).startswith(("是", "yes")) and index and _c(marks[index - 1][1]) in ("x", "√", "✓", "✔", "×", "■", "☑") and marks[index - 1][0] == col - 1:
                            judged = True
                        if _c(value).startswith(("否", "no")) and index and _c(marks[index - 1][1]) in ("x", "√", "✓", "✔", "×", "■", "☑") and marks[index - 1][0] == col - 1:
                            judged = False
            used = set()
            for row, column, cg in cgs:
                pair = next(((r, c, v) for r, c, v in cgks if (r, c) not in used and 0 <= r - row <= 5), None)
                if pair is not None:
                    used.add(pair[:2])
                where = self.named(self.READER.Place.of(f"“{ws.title}”第{row}行", ws, row, column))
                groups.append((cg, pair[2] if pair else None, where))
            for r, c, v in cgks:
                if (r, c) not in used:
                    groups.append((None, v, self.named(self.READER.Place.of(f"“{ws.title}”第{r}行", ws, r, c))))
        return groups, requirement, judged, []

    # ---- MSA:6 -----------------------------------------------------------------------------------

    def list_rows(self, ctx: RuleContext, item: Any) -> list[tuple[str, str, str, str, str]] | None:
        """(where, part numbers written, characteristic, measuring equipment) of each row of a spreadsheet special
        characteristic list; None when the list has no measuring equipment column (or is not a spreadsheet).

        The equipment heading may be English and Chinese in one cell ("Measuring Equipment/测量设备") over a
        two-row heading; the rows start below the heading's merged area."""
        if str(item.file_url).lower().endswith(".pdf"):
            return None
        found: list[tuple[str, str, str, str, str]] = []
        located = False
        with self.reading(ctx, item, self.READER.resources):
            for ws in ctx.workbook.worksheets:
                grid = self.grid(ctx, ws)
                for (row, column), text in sorted(grid.items()):
                    if row > 12 or not any(term in _c(text) for term in ("测量设备", "measuringequipment", "检测设备", "测量工具", "量具")):
                        continue
                    if any(term in _c(text) for term in ("能力", "capability")):
                        continue
                    heads = {c: _c(t) for (r, c), t in grid.items() if r == row}
                    name_col = next((c for c, t in sorted(heads.items()) if any(term in t for term in ("特殊特性名称", "sppcname", "特性名称", "特性描述"))), None)
                    value_col = next((c for c, t in sorted(heads.items()) if any(term in t for term in ("特性值", "规格", "公差"))), None)
                    part_col = next((c for c, t in sorted(heads.items()) if any(term in t for term in ("零件号", "件号", "partno", "/code"))), None)
                    if name_col is None:
                        continue
                    located = True
                    bottom = row
                    for range_ in ws.merged_cells.ranges:
                        if range_.min_row == row and range_.min_col in heads:
                            bottom = max(bottom, range_.max_row)
                    last = max(r for r, _c2 in grid)
                    for r in range(bottom + 1, last + 1):
                        # The table ends at its sign-off or remark line ("备注：内部过程特性符号" is a legend, not a row).
                        if any(_c(grid[(rr, c)]).startswith(marker) for rr, c in grid if rr == r for marker in _PLAN_END):
                            break
                        name = grid.get((r, name_col), "")
                        value = grid.get((r, value_col), "") if value_col else ""
                        # A legend line under the table (a symbol explained, no value, part or equipment) is not a row.
                        if not (name or value) or not (value or grid.get((r, column)) or (part_col and grid.get((r, part_col)))):
                            continue
                        where = f"清单“{ws.title}”第{r}行"
                        self.__dict__.setdefault("_feature_cells", {})[where] = [(ws, r, c) for c in (name_col, value_col, column) if c]
                        found.append((where, grid.get((r, part_col), "") if part_col else "", name, value, grid.get((r, column), "")))
                    break
        return found if located else None

    @staticmethod
    def characteristic_match(name: str, requirement: str, report: Report, *, unique_name: bool, unique_requirement: bool) -> bool | None:
        """Compare an explicitly labelled study parameter, not numbers anywhere on the sheet.
        Different nominal dimensions or different Rz limits exclude coverage. Equal numbers
        alone cannot identify a feature; unresolved specs and duplicate identities stay open."""
        written = {_c(v) for v in report.studied if _c(v) and _c(v) not in _PLACEHOLDERS}
        if not written:
            return None

        target = _dimension(_c(requirement))
        readings = [_dimension(w) for w in written]
        if target and all(v and v[0] == target[0] and v[1] != target[1] for v in readings):
            return False
        if unique_name and len(written) == 1 and _c(name) in written and not readings[0] and re.search(r"[a-z\u4e00-\u9fff]", _c(name)):
            return True
        if target and unique_requirement and len(written) == 1 and (study := readings[0]) and study[:2] == target[:2]:
            # Require the whole value (Rz limit or numeric dimension with tolerance), not a shared nominal.
            limits = {_c(v).strip("()") for v in report.limits}
            if target[0]:
                return True if not limits or all(_dimension(v) == target for v in limits) else None
            if target[2] and ((study[2] == target[2] and not limits) or (not study[2] and limits == {target[2]})):
                return True
        return None

    def coverage(self, ctx: RuleContext, items: list[tuple[str, str, str, str]], parts: list[str], main: Any) -> tuple[list[tuple[str, Any]], dict[str, list[str]], list[str]]:
        """Align each source row to the same part, gauge and characteristic, then allocate its defect once."""
        everything = self.all_reports(ctx)
        scanned = any(self.scanned(i) for i in self.suite())
        defects, unresolved = [], []
        gauges: dict[str, list[str]] = {}
        keys = [(part, gauge_family(gauge) or _c(gauge)) for (_where, _name, _requirement, gauge), part in zip(items, parts)]
        names = Counter((key, _c(item[1])) for key, item in zip(keys, items))
        specs = Counter((key, _spec_identity(item[2])) for key, item in zip(keys, items))
        for (where, name, requirement, gauge), (part, family) in zip(items, keys):
            desc = f"{where}“{name}（{requirement}）”"
            if not _c(gauge) or _c(gauge) in _PLACEHOLDERS:
                if self.mine(main):
                    for cell in self.__dict__.get("_feature_cells", {}).get(where, ()):
                        self.cite(*cell)
                defects.append((f"{desc}没有对应的测量设备/量具", main))
                continue
            gauges.setdefault(family, []).append(desc)
            kind = gauge_kind(gauge)
            if kind == "mixed":
                unresolved.append(f"{desc}测量设备“{gauge}”跨计数型与计量型")
                continue
            named = [r for r in everything if r.named(gauge)]
            # A report citing several part codes is not unambiguously a study of this part.
            # Unknown identity is not a mismatch. A number found only in body text cannot identify a study.
            same = [r for r in named if r.subject_codes == {part}]
            unbound = [r for r in named if not r.subject_codes or (len(r.subject_codes) > 1 and part in r.subject_codes)]
            if kind == "unknown":
                unresolved.append(f"{desc}“{gauge}”的系统级评价记录尚不能识别，不能以未读到GR&R报告代替")
                continue
            kinds = ("Kappa", "Attr", "GRR") if kind == "counting" else ("GRR",)
            key = (part, family)
            matches = [
                (r, self.characteristic_match(name, requirement, r, unique_name=names[key, _c(name)] == 1, unique_requirement=specs[key, _spec_identity(requirement)] == 1))
                for r in same
                if r.kind in kinds
            ]
            confirmed = [r for r, match in matches if match is True]
            if not confirmed:
                if not part or any(r.kind in kinds for r in unbound) or scanned:
                    unresolved.append(f"{desc}有未能定位件号的同量具报告或套件扫描页，不能断言缺件")
                    continue
                if any(match is None for _r, match in matches):
                    unresolved.append(f"{desc}未能与“{gauge}”报告所写特性唯一对齐")
                    continue
                landing = next((r.item for r in same), None)
                if landing is None:
                    landing = self.gauge_file(ctx, family, kinds[0], main)
                observed = "、".join(dict.fromkeys(v for report, _ in matches for v in report.studied))
                detail = f"（报告实写：{observed}）" if observed else ""
                if self.mine(landing):
                    for cell in self.__dict__.get("_feature_cells", {}).get(where, ()):
                        self.cite(*cell)
                    for r, _ in matches:
                        for cell in r.study_cells:
                            self.cite(*cell)
                defects.append((f"{desc}没有零件号{part}、量具“{gauge}”及该特性均匹配的{'或'.join(kinds)}报告{detail}", landing))
                continue
            for r in confirmed:
                if self.mine(r.item):
                    for cell in self.__dict__.get("_feature_cells", {}).get(where, ()):
                        self.cite(*cell)
                    for cell in r.study_cells:
                        self.cite(*cell)
                outcomes = [verdict(v) for v in r.conclusions]
                if "reject" in outcomes:
                    defects.append((f"{desc}对应的{r.label}结论不合格", r.item))
                elif (
                    not outcomes
                    or any(v != "accept" for v in outcomes)
                    or self.check_report(r) is not None
                    or (r.kind == "GRR" and ((r.grr is not None and r.grr > 10) or (r.ndc is not None and r.ndc < 5)))
                ):
                    # MSA:6 borrows MSA:2's matching key; an index conflicting with an acceptable conclusion
                    # is not an unconditional NG under this clause.
                    unresolved.append(f"{desc}对应的{r.label}未能确定合格结论")
        return defects, gauges, unresolved

    def landed(self, source: str, items: list, defects: list[tuple[str, Any]], gauges: dict, mixed: list[str]) -> tuple[str, str]:
        """This file's MSA:6 outcome: (status, reason)."""
        own = [text for text, landing in defects if self.mine(landing)]
        elsewhere = sorted({original_name(landing) for _text_, landing in defects if not self.mine(landing)})
        tail = f"；另有套件级缺陷落在《{'》《'.join(elsewhere)}》，不在本文件重复计入" if elsewhere else ""
        if own:
            return "NG", "；".join(own[:5]) + ("等" if len(own) > 5 else "") + tail + "。"
        if mixed:
            return "UNABLE", "；".join(mixed[:4]) + "，需人工复核。"
        if defects:
            return "UNABLE", f"套件缺陷落在《{'》《'.join(elsewhere)}》，不在本文件重复计入；条文未指定其他文件的结论，不能据此记通过。"
        return "PASS", f"{source}全部{len(items)}项特殊特性对应的{len(gauges)}种量具均有零件匹配、写明所判特性且结论合格的分析报告。"

    @rule("特殊特性量具的分析报告覆盖与结论")
    def rule_6(self, ctx: RuleContext):
        plans = self.batch_files(_PGQP1)
        plan, main, _scanned = self.suite_plan(ctx)
        if plans:
            if len(plans) > 1:
                raise unable("同批次有多份采购件质量计划，尚不能确定按哪一份核对，需人工复核。")
            if self.scanned(plans[0]):
                raise unable("采购件质量计划含扫描页，未能确认所需特殊特性范围。")
            rows, parts, sheets = self.pgqp1_rows(ctx, plans[0], exact_part=True)
            if not sheets:
                return "NG", "同批次《采购件质量计划》中没有PGQP1表，无法取得特殊特性。"
            if len(parts) != 1:
                raise unable(f"采购件质量计划PGQP1的零件号未能唯一读取（读到：{'、'.join(sorted(parts)) or '无'}），尚不能按零件号匹配报告，需人工复核。")
            part = next(iter(parts))
            items = [(f"PGQP1第{row}行", name, special, gauge) for row, name, special, gauge in rows if _c(special)]
            if not items:
                raise unable("PGQP1特殊特性列没有内容，不能按空范围通过，需人工复核。")
            status, reason = self.landed("PGQP1", items, *self.coverage(ctx, items, [part] * len(items), main))
            if status == "UNABLE":
                raise unable(reason)
            return status, reason
        lists = [item for kind in _LISTS for item in self.batch_files(kind)]
        if not lists:
            raise unable("同批次既没有PGQP1，也没有特殊特性清单，没有可取特殊特性的来源，需人工复核。")
        if len(lists) > 1:
            raise unable(f"同批次未提交PGQP1，回退的特殊特性清单有{len(lists)}份，尚不能确定按哪一份核对，需人工复核。")
        found = self.list_rows(ctx, lists[0])
        name = original_name(lists[0])
        if found is None:
            raise unable(f"同批次未提交PGQP1，回退特殊特性清单《{name}》；未能在清单中定位测量设备栏，检查程序不能按“特性→量具”对齐，需人工复核。")
        if not found:
            raise unable(f"同批次未提交PGQP1，回退特殊特性清单《{name}》没有读到特性行，不能按空范围通过，需人工复核。")
        source = f"特殊特性清单《{name}》"
        planned = {_c(row.values.get("零件号", "")).upper() for row in plan} - {""}
        own_part = next(iter(planned)) if len(planned) == 1 else ""
        row_parts = [set(_PART.findall(unicodedata.normalize("NFKC", part_text).upper())) for _where, part_text, _ch, _val, _g in found]
        written = set().union(*row_parts)
        everything = [(where, name, value, gauge) for where, _p, name, value, gauge in found]
        if len(written) <= 1 and (not written or not own_part or written == {own_part}):
            # One part (or none written): the whole list is this part's.
            part = own_part or (next(iter(written)) if written else "")
            status, reason = self.landed(source, everything, *self.coverage(ctx, everything, [part] * len(everything), main))
        else:
            # The list mixes part numbers. The clause does not say whether only this part's rows count or all rows:
            # conclude only when both readings give this file the same outcome.
            if not own_part:
                raise unable(f"{source}混装多个零件号（{'、'.join(sorted(written))}），测量系统分析计划表没有唯一零件号，尚不能确定本零件号，需人工复核。")
            mine = [(item, own_part) for item, parts in zip(everything, row_parts) if own_part in parts]
            narrow = self.landed(source + f"中零件号{own_part}的行", [i for i, _p in mine], *self.coverage(ctx, [i for i, _p in mine], [p for _i, p in mine], main)) if mine else ("UNABLE", "")
            wide_parts = [next(iter(parts)) if len(parts) == 1 else own_part for parts in row_parts]
            wide = self.landed(source + "全部行", everything, *self.coverage(ctx, everything, wide_parts, main))
            if narrow[0] != wide[0]:
                raise unable(
                    f"{source}混装多个零件号（{'、'.join(sorted(written))}）。条文写“无PGQP1时取特殊特性清单”，未写只取本零件号{own_part}的行还是全部行："
                    f"只取本零件号的行时，{narrow[1] or '没有本零件号的行。'}按全部行时，{wide[1].rstrip('。')}。两种读法结论不同，需人工复核。"
                )
            status, reason = wide[0], f"{source}混装多个零件号，只取本零件号{own_part}的行与取全部行结论相同。" + wide[1]
        if status == "UNABLE":
            raise unable(reason)
        return status, reason


class Audit(AgentAudit):
    """Runs the declared UAES MSA reader extended with this leaf's revision 3 clause methods."""

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (MsaClauses, base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
