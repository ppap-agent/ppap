"""UAES U01 candidate: initial sample inspection report (ISIR), UAES v1.0 revision 3 (IR:1-IR:4).

The four reports of F-PU-207 are read as tables: requirement, supplier readings and the OK / NOT OK columns.
Pictures placed in a report's 供应商负责人 area are shown to the image model only to say whether each is a
handwritten signature or a seal; every verdict is decided in code.
"""

# ---- shared reading (a leaf may not import the UAES implementation, so each candidate leaf carries its own) ----

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from contextlib import contextmanager
from datetime import date  # noqa: F401 - not every leaf compares dates
from pathlib import Path
from typing import Any, Iterator

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import ExcelAgent, rule
from audit.contracts import AuditEvidence, EvidenceSource, SupplyNeed
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table  # noqa: F401 - not every leaf finds sheets
from audit.engine.rule_engine.contracts import RuleResourceModel, index_rule_resource
from documents.pdf import GroupingMode, GroupingSpec, OutputSpec, PdfSpec, StitchMode, StitchSpec
from resources import RESOURCE

WHOLE_SHEET = 10_000
_PICTURES: dict[str, Any] = {"preserve_pdf_embedded_images": True, "embedded_image_max_per_sheet": 10_000, "embedded_image_min_bytes": 0, "rasterize_vector_graphics": True}
STITCHED = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES), output=OutputSpec(**_PICTURES))
PER_PAGE = PdfSpec(grouping=GroupingSpec(mode=GroupingMode.ALL_PAGES), stitch=StitchSpec(mode=StitchMode.NO_STITCH), output=OutputSpec(**_PICTURES))

# The clause's placeholder words (PSW:1, SSH:2); a hollow box is an unfilled cell.
PLACEHOLDER_WORDS = (
    "见附表",
    "见附件",
    "待定",
    "TBD",
    "NA",
    "N/A",
    "—",
    "-",
    "/",
    "同上",
    "见上",
    "略",
    "*",
    "待补",
    "后续提供",
    "XX",
    "XXXX",
    "123456",
    "#N/A",
    "#REF!",
    "#DIV/0!",
    "#VALUE!",
    "#NAME?",
    "#NUM!",
    "#NULL!",
)


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


PLACEHOLDERS = {compact(word) for word in PLACEHOLDER_WORDS}
HOLLOW = {"□", "☐"}


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def resource(*documents) -> Any:
    parts = []
    for index in range(3):
        merged: dict[str, Any] = {}
        for document in documents:
            for item in document[index]:
                merged.setdefault(item.canonical_name, item)
        parts.append(tuple(merged.values()))
    return index_rule_resource(RuleResourceModel(sheets=parts[0], sections=parts[1], columns=parts[2]))


def original_name(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


def version_of(item: Any) -> str:
    return str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)


class Place(str):
    """A place written into a basis that remembers the cell it names."""

    cell: tuple[Any, int, int] | None = None

    @classmethod
    def of(cls, text: str, sheet: Any, row: int, column: int) -> "Place":
        place = cls(text)
        place.cell = (sheet, row, column)
        return place


class Reader(ExcelAgent):
    """A candidate carrier: its own resource only; every place a basis names becomes evidence."""

    DOCUMENT: Any = ((), (), ())
    PDF_SPECS: tuple = (STITCHED, PER_PAGE)
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})
    TABLES = True

    def _resolve_resource_doc_type(self):
        return None

    # ---- loading ------------------------------------------------------------------------------

    def _load_workbook(self) -> Any:
        suffix = Path(str(self.audit_file.file_url)).suffix.lower()
        if suffix not in type(self).SUPPORTED_EXTENSIONS:
            raise unable(f"检查程序不能按本条款读取{suffix[1:].upper()}格式的原件，需人工复核。")
        if type(self).TABLES:
            return super()._load_workbook()
        if self._workbook_cache is None:
            from openpyxl import Workbook

            self._workbook_cache = Workbook()
        return self._workbook_cache

    def _load_pdf_workbook(self, file_url: str) -> Any:
        self._conversion_artifact, self._workbook_cache, self._pdf_spec = self.convert(file_url, type(self).DOCUMENT, type(self).PDF_SPECS)
        return self._workbook_cache

    def convert(self, path: str, document: Any, specs: tuple) -> tuple[Any, Any, Any]:
        """(artifact, workbook, spec): the first layout that yields one of the document's sheets; else the last that converted."""
        from audit.engine.checkers.pdf_evidence import PdfSourceError

        names = [sheet.canonical_name for sheet in document[0]]
        chosen = None
        for spec in specs:
            try:
                chosen = (*self._convert_pdf(path, spec), spec)
            except PdfSourceError:
                continue
            assert self._rule_resource is not None
            probe = RuleContext(None, chosen[1], self._rule_resource)
            if any(probe.find_sheets(name, on_missing=Decision.IGNORE) for name in names):
                break
        if chosen is None:
            from openpyxl import Workbook

            self.__dict__.setdefault("_untabled", set()).add(path)
            return None, Workbook(), None
        return chosen

    def untabled(self, path: str | None = None) -> bool:
        return str(path or self.audit_file.file_url) in self.__dict__.get("_untabled", set())

    def unread_pages(self, path: str | None = None) -> list[int]:
        """Scanned pages of a PDF: no text layer, or one picture covering the page."""
        path = str(path or self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            return []
        import fitz

        with fitz.open(path) as document:
            return [page.number + 1 for page in document if not page.get_text().strip() or any(abs(fitz.Rect(image["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for image in page.get_image_info())]

    def batch_files(self, *kinds: str) -> list[Any]:
        """Same-round originals typed as one of `kinds`, other than this file."""
        if self.group_context is None:
            return []
        found: dict[str, Any] = {}
        for kind in kinds:
            for item in self.group_context.get_files_by_type(kind):
                if item.audit_id != self.audit_file.audit_id:
                    found[item.audit_id] = item
        return list(found.values())

    def named_files(self, terms: tuple[str, ...]) -> list[Any]:
        """Same-round originals, typed or not, whose type or uploaded name writes one of `terms`."""
        if self.group_context is None:
            return []
        wanted = [compact(term) for term in terms]
        found: dict[str, Any] = {}
        for kind, items in self.group_context.by_doc_type.items():
            for item in items:
                if item.audit_id != self.audit_file.audit_id and any(term in compact(kind) or term in compact(original_name(item)) for term in wanted):
                    found[item.audit_id] = item
        for item in self.group_context.by_audit_id.values():
            if item.audit_id != self.audit_file.audit_id and any(term in compact(original_name(item)) for term in wanted):
                found[item.audit_id] = item
        return list(found.values())

    @contextmanager
    def reading(self, ctx: RuleContext, item: Any, document: Any, specs: tuple = (STITCHED, PER_PAGE)) -> Iterator[RuleContext]:
        """Read one frozen same-round original with this carrier's resource; its cells are evidence of its version."""
        from audit.checker_lib.excel import load_workbook_auto

        version = version_of(item)
        cache = self.__dict__.setdefault("_related", {})
        if version not in cache:
            source = Path(str(item.file_url))
            metadata = getattr(item, "file_metadata", None) or {}
            expected = metadata.get("source_sha256") or metadata.get("sha256")
            if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise OSError("本轮冻结关联资料已变化，不能完成本项核对。")
            cache[version] = self.convert(str(source), document, specs)[1] if source.suffix.lower() == ".pdf" else load_workbook_auto(str(source))
        with ctx.reading(cache[version], version):
            yield ctx

    # ---- reading helpers ----------------------------------------------------------------------

    @staticmethod
    def extent(sheet: Sheet) -> tuple[int, int]:
        return min(sheet.ws.max_row, WHOLE_SHEET), min(sheet.ws.max_column, WHOLE_SHEET)

    @classmethod
    def cells(cls, sheet: Sheet, *, row_min: int = 1, row_max: int | None = None) -> list[Any]:
        rows, columns = cls.extent(sheet)
        return sheet.scan_non_empty(row_min=row_min, row_max=min(row_max or rows, rows), col_min=1, col_max=columns)

    @staticmethod
    def raw(cell: Any) -> str:
        return unicodedata.normalize("NFKC", "" if cell.ws.cell(cell.row_index, cell.col_index).value is None else str(cell.ws.cell(cell.row_index, cell.col_index).value)).strip()

    @staticmethod
    def section(sheet: Sheet, name: str) -> Any:
        try:
            return sheet.section(name)
        except GuardStop as stop:
            if stop.decision != Decision.NG:
                raise
            raise unable(f"工作表“{sheet.title}”中未能定位{name}的表头，检查程序未能读取，需人工复核。") from None

    @staticmethod
    def located(sheet: Sheet, name: str) -> Any:
        try:
            return sheet.section(name)
        except GuardStop:
            return None

    # ---- the image model reads, code decides ---------------------------------------------------

    def transcribe(self, image: bytes, prompt: str, schema: dict[str, Any], subject: str) -> dict[str, Any]:
        """One image of an original transcribed into `schema` by the image model; the model transcribes, code decides."""
        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from audit.engine.runtime import get_runtime
        from documents.model_access import ModelUnavailable

        runtime = get_runtime()
        hook = getattr(runtime, "read_ocr_visual_facts", None)
        if runtime is not None and not callable(hook):
            raise unable(f"当前运行环境不提供原件图像转写，{subject}尚未核实，需人工复核。")
        request = hook if callable(hook) else request_ocr_visual_facts
        try:
            return request(images=[image], prompt=prompt, response_schema={"name": "transcription", "schema": schema})
        except ModelUnavailable as exc:
            if exc.code in ModelUnavailable.UNGRANTED:
                raise unable(f"当前没有可用的模型，{subject}尚未核实，需人工复核。") from None
            raise GuardStop(Decision.ERR, "原件模型请求失败或结果尚未确认，尚未取得业务结论。") from None
        except GuardStop:
            raise
        except Exception:
            raise GuardStop(Decision.ERR, "原件读取失败，尚未取得业务结论。") from None

    # ---- evidence -----------------------------------------------------------------------------

    def need(self, document: str, *, part: str | None = None, found: str | None = None) -> None:
        if "_need" not in self.__dict__:
            self.__dict__["_need"] = SupplyNeed(kind="source_mismatch" if found else "missing", document=document, part_number=part or None, found_part_number=found or None)

    def witness(self, locator: dict[str, Any], value: Any, version: str | None = None, *, visual: bool = False) -> None:
        source = EvidenceSource(
            kind="visual" if visual else "structure",
            locator=json.dumps({**locator, "file_version_id": version or version_of(self.audit_file)}, ensure_ascii=False, separators=(",", ":")),
            excerpt=" ".join(str(value or "").split())[:500],
        )
        cited = self.__dict__.setdefault("_cited", [])
        if source not in cited and len(cited) < 40:
            cited.append(source)

    def cite(self, sheet: Any, row: int, column: int) -> None:
        """Name a spreadsheet cell a basis points at as evidence; a table converted from a PDF has no cell of its own."""
        from openpyxl.utils.cell import get_column_letter

        ws, ctx = getattr(sheet, "ws", sheet), self._shared_rule_context
        if ctx is None or type(row) is not int or type(column) is not int or row < 1 or column < 1:
            return
        version = ctx.version_for_worksheet(ws) or (version_of(self.audit_file) if getattr(ws, "parent", None) is ctx.workbook else "")
        files = [self.audit_file, *(self.group_context.by_audit_id.values() if self.group_context is not None else ())]
        original = next((item for item in files if version_of(item) == version), None)
        if not version or original is None or Path(str(original.file_url)).suffix.lower() not in {".xlsx", ".xls", ".xlsm"}:
            return
        merged = ctx.ops.get_detailed_merge_info(ws, row, column) if ctx.ops is not None else None
        anchor = ws.cell(row=merged.min_row, column=merged.min_col) if merged else ws.cell(row=row, column=column)
        self.witness({"kind": "xlsx_cell", "sheet": str(ws.title), "cell": f"{get_column_letter(anchor.column)}{anchor.row}"}, anchor.value, version)

    def named(self, place: str) -> str:
        if isinstance(place, Place) and place.cell is not None:
            self.cite(*place.cell)
        return str(place)

    def _execute_rule_check(self, rule_no: int) -> Any:
        self.__dict__.pop("_need", None)
        self.__dict__.pop("_cited", None)
        return super()._execute_rule_check(rule_no)

    def _finalize_audit_result(self, result: Any, *, rule_no: int, phase: Any, ctx: RuleContext | None = None) -> Any:
        result = super()._finalize_audit_result(result, rule_no=rule_no, phase=phase, ctx=ctx)
        need = self.__dict__.pop("_need", None)
        if need is not None and result.status == "NG" and result.supply is None:
            result = result.model_copy(update={"supply": need})
        cited = tuple(self.__dict__.pop("_cited", ()))
        if not cited:
            return result
        suffix: Any = Path(str(self.audit_file.file_url)).suffix.lower().lstrip(".")
        current = result.evidence or AuditEvidence(required="structure_or_visual", used="none", source_format=suffix, prepared_format=suffix, sources=())
        sources = (*cited, *(source for source in current.sources if source not in cited))
        used = "both" if len({source.kind for source in sources}) > 1 else sources[0].kind
        performed = (result.checks_performed or 0) + len(cited)
        return result.model_copy(update={"evidence": current.model_copy(update={"sources": sources, "used": used}), "checks_performed": performed})


def create_audit(audit_file, rule, group_context=None):
    return AgentAudit(audit_file, rule, group_context, resource=RESOURCE)


# ---- IR -------------------------------------------------------------------------------------------

import math
import struct
from dataclasses import dataclass
from io import BytesIO

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

REPORTS = {"尺寸报告": "dimension", "材料报告": "material", "功能报告": "function", "可靠性报告": "reliability"}
SIGNATURE_MARKERS = ("Supplier responsible signature", "供应商负责人")
CUSTOMER_MARKERS = ("Customer", "客户负责人")
# IR:2: the whole-table marks that exempt the function and reliability reports.
EXEMPT_REPORTS = ("功能报告", "可靠性报告")
EXEMPT_MARKS = ("无此项要求", "不适用", "N/A", "无", "见总成级试验")
APPROVAL_KINDS = ("E11", "顾客工程批准")
APPROVAL_NAMES = ("顾客工程批准", "工程批准", "偏差", "OTS", "认可")
BUBBLE = ("气泡图", "气泡", "bubble")


def _report(title: str, english: str):
    sheet = SheetModel(canonical_name=title, include_keywords=(title, english))
    section = SectionModel(
        canonical_name=f"{title}区",
        sheet_name=title,
        header_anchors=(f"{title}序号", f"{title}要求", f"{title}评价"),
        header_hit=2,
        header_search_max_row=WHOLE_SHEET,
        header_search_max_col=WHOLE_SHEET,
        row_anchors=(f"{title}序号",),
        row_stop_on_first_blank=False,
        row_max_blank_streak=WHOLE_SHEET,
        row_stop_markers=SIGNATURE_MARKERS,
    )
    columns = (
        ColumnModel(canonical_name=f"{title}序号", section_name=section.canonical_name, aliases=("No.",)),
        ColumnModel(canonical_name=f"{title}要求", section_name=section.canonical_name, aliases=("Requirements/要求",)),
        ColumnModel(canonical_name=f"{title}供应商实测值", section_name=section.canonical_name, aliases=("Actual-Value Supplier/供应商实测值",)),
        ColumnModel(canonical_name=f"{title}评价", section_name=section.canonical_name, aliases=("Evaluation",)),
    )
    return sheet, section, columns


_PARTS = [_report(title, english) for title, english in REPORTS.items()]
INSPECTION = (tuple(part[0] for part in _PARTS), tuple(part[1] for part in _PARTS), tuple(column for part in _PARTS for column in part[2]))

_NUMBER = r"[-+]?\d+(?:\.\d+)?"
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
        inside = (low > self.lower or (low == self.lower and not self.lower_open)) and (high < self.upper or (high == self.upper and not self.upper_open))
        outside = high < self.lower or (high == self.lower and self.lower_open) or low > self.upper or (low == self.upper and self.upper_open)
        return True if inside else False if outside else None


def _text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return text.replace("〈", "<").replace("〉", ">").replace("≦", "≤").replace("≧", "≥")


def limits(requirement: Any) -> Limits | None:
    """The range a requirement as written on the report allows; None when it states no readable range."""
    if isinstance(requirement, (int, float)):
        return None
    text = _text(requirement).strip()
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
    match = re.match(rf"^R[az]\s*max\s*({_NUMBER})", text, re.I)
    if match:
        return Limits(0.0, float(match.group(1)))
    # A tolerance value followed by datum letters ("Φ0.1A-B G", "0.1MA-BC", "0.15 丨K") is a geometric tolerance zone.
    match = re.fullmatch(rf"[ΦφØø]?\s*({_NUMBER})\s*[丨|]?\s*([A-Z](?:[\s\-丨|]*[A-Z])*)", text)
    if match:
        return Limits(0.0, float(match.group(1)))
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


def bare(requirement: Any) -> bool:
    """A nominal written without any tolerance ("49.2", "40°", "6"): the report states no interval for it."""
    if isinstance(requirement, (int, float)):
        return True
    return bool(re.fullmatch(rf"[ΦφØøR]?\s*{_NUMBER}\s*°?", _text(requirement).strip()))


def reading(value: Any, number_format: str, percent: bool) -> tuple[float, float] | None:
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
    header: int


def xls_pictures(path: str, sheet: str) -> list[tuple[tuple[int, int, int, int], bytes | None]]:
    """(one-based anchor (top, left, bottom, right), picture bytes) of each picture on one sheet of a BIFF8 workbook."""
    import olefile

    with olefile.OleFileIO(path) as document:
        stream = document.openstream("Workbook" if document.exists("Workbook") else "Book").read()
    names, group, drawing, current, previous, position = {}, bytearray(), bytearray(), None, None, 0
    while position + 4 <= len(stream):
        kind, size = struct.unpack_from("<HH", stream, position)
        data = stream[position + 4 : position + 4 + size]
        if kind == 0x0085:
            length, wide = data[6], data[7]
            names[struct.unpack_from("<I", data)[0]] = data[8 : 8 + length * (2 if wide else 1)].decode("utf-16-le" if wide else "latin-1")
        elif kind == 0x00EB or kind == 0x003C and previous == 0x00EB:
            group.extend(data)
        elif kind == 0x0809:
            current = names.get(position)
        elif kind == 0x000A:
            current = None
        elif current == sheet and (kind == 0x00EC or kind == 0x003C and previous == 0x00EC):
            drawing.extend(data)
        if kind != 0x003C:
            previous = kind
        position += 4 + size

    def art(blob: bytes, start: int, end: int):
        while start + 8 <= end:
            header, kind, size = struct.unpack_from("<HHI", blob, start)
            yield header >> 4, kind, start + 8, min(start + 8 + size, end), header & 0xF == 0xF
            start += 8 + size

    store: list[bytes | None] = []

    def blips(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in art(bytes(group), start, end):
            if container:
                blips(begin, finish)
            elif kind == 0xF007 and finish - begin >= 36:
                name = group[begin + 33]
                embedded = next(iter(art(bytes(group), begin + 36 + name, finish)), None)
                if embedded is None or embedded[1] not in (0xF01D, 0xF01E):
                    store.append(None)
                else:
                    instance, _kind, data, stop, _ = embedded
                    store.append(bytes(group[data + 16 * (1 + (instance & 1)) + 1 : stop]))

    blips(0, len(group))
    blob = bytes(drawing)
    found: list[tuple[tuple[int, int, int, int], bytes | None]] = []

    def walk(start: int, end: int) -> None:
        for _instance, kind, begin, finish, container in art(blob, start, end):
            if kind == 0xF004:
                anchor, picture = None, None
                for count, part, data, stop, inner in art(blob, begin, finish):
                    if inner:
                        walk(data, stop)
                    elif part == 0xF010 and stop - data >= 18:
                        _flag, left, _dx, top, _dy, right, _dx2, bottom, _dy2 = struct.unpack_from("<9H", blob, data)
                        anchor = (top + 1, left + 1, bottom + 1, right + 1)
                    elif part == 0xF00B:
                        for index in range(count):
                            code, value = struct.unpack_from("<HI", blob, data + 6 * index)
                            if code & 0x3FFF == 0x0104:
                                picture = value
                if anchor is not None and picture:
                    found.append((anchor, store[picture - 1] if picture <= len(store) else None))
            elif container:
                walk(begin, finish)

    walk(0, len(blob))
    return found


def xlsx_pictures(path: str, sheet: str) -> list[tuple[tuple[int, int, int, int], bytes | None]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path)
    found = []
    for image in getattr(workbook[sheet], "_images", []):
        start = image.anchor._from
        end = getattr(image.anchor, "to", None) or start
        try:
            data = image._data()
        except Exception:
            data = None
        found.append(((start.row + 1, start.col + 1, end.row + 1, end.col + 1), data))
    return found


PICTURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["handwritten_signature", "ink_seal"],
    "properties": {"handwritten_signature": {"type": ["boolean", "null"]}, "ink_seal": {"type": ["boolean", "null"]}},
}
PICTURE_PROMPT = (
    "这是样品检验报告工作表“供应商负责人”签署区中嵌入的一张图片。请只描述图片本身，不判断是否合格。\n"
    "handwritten_signature：图片中有手写笔迹的签名为 true；只有印刷体文字、印章、标志或空白为 false；看不清为 null。\n"
    "ink_seal：图片中有公司印章（公章、专用章等的印迹或其图像）为 true；品牌标志、表头 logo、签名、文字为 false；看不清为 null。"
)


class InspectionR3(Reader):
    DOCUMENT = INSPECTION
    EXTRA_RESOURCE = resource(INSPECTION)

    # ---- the four reports ----------------------------------------------------------------------

    def readable(self) -> None:
        if unread := self.unread_pages():
            raise unable(f"原件第{'、'.join(map(str, unread))}页为扫描页（没有文字层），检查程序不读取扫描件，需人工复核。")

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
            found.append(Report(title, sheet, rows, band, ok_column, nok_column, header))
        return found

    def all_reports(self, ctx: RuleContext) -> tuple[list[Report], list[str]]:
        found, missing = [], []
        for title in REPORTS:
            reports = self.reports(ctx, title)
            if not reports:
                missing.append(title)
            found.extend(reports)
        if missing and (self.untabled() or self.unread_pages()):
            raise unable(f"PDF原件未能读出表格，尚不能确认是否包含{'、'.join(missing)}，需人工复核。")
        return found, missing

    @staticmethod
    def requirement_rows(report: Report) -> list:
        return [row for row in report.rows if compact(row.value(f"{report.title}要求"))]

    def exemption(self, report: Report) -> str | None:
        """IR:2's whole-table mark of a function or reliability report: its text, written in the header or first row."""
        if report.title not in EXEMPT_REPORTS:
            return None
        rows = [report.header, report.header + 1, *(row.row_index for row in report.rows[:1])]
        marks = [compact(mark) for mark in EXEMPT_MARKS]
        for row in dict.fromkeys(rows):
            for cell in self.cells(report.sheet, row_min=row, row_max=row):
                written = self.raw(cell)
                if compact(written) in marks or any(len(mark) > 2 and mark in compact(written) for mark in marks):
                    self.cite(report.sheet, row, cell.col_index)
                    return written
        return None

    def verdict(self, report: Report, row: int) -> bool | None:
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

    def ranges(self, report: Report, row: Any) -> tuple[list[tuple[str, int]], list[Place], list[float]]:
        """(readings outside the requirement's range, readings or requirement that could not be read, readings inside)."""
        requirement = row.value(f"{report.title}要求")
        where = f"“{report.sheet.title}”第{row.row_index}行"
        asked = row.columns[f"{report.title}要求"].header_col
        range_ = limits(requirement)
        if range_ is None:
            return [], [] if bare(requirement) or not re.search(r"\d", _text(requirement)) else [Place.of(f"{where}要求“{requirement}”的范围未能读取", report.sheet, row.row_index, asked)], []
        percent = "%" in _text(requirement)
        outside, unknown, values = [], [], []
        for column in report.band:
            cell = report.sheet.cell(row.row_index, column)
            if not cell.has_data():
                continue
            interval = reading(cell.value(), cell.number_format(), percent)
            if interval is None:
                unknown.append(Place.of(f"{where}实测值“{cell.value()}”未能读取为数值", report.sheet, row.row_index, column))
                continue
            held = range_.holds(*interval)
            if held is False:
                outside.append((str(cell.value()), column))
            elif held is None:
                unknown.append(Place.of(f"{where}实测值“{cell.value()}”尚不能确定是否在要求“{requirement}”内", report.sheet, row.row_index, column))
            values.append(interval[1])
        return outside, unknown, values

    def approvals(self) -> list[Any]:
        return list({item.audit_id: item for item in [*self.batch_files(*APPROVAL_KINDS), *self.named_files(APPROVAL_NAMES)]}.values())

    # ---- IR:1 ----------------------------------------------------------------------------------

    @rule("尺寸报告实测值列数与超差")
    def rule_1(self, ctx: RuleContext):
        self.readable()
        reports = self.reports(ctx, "尺寸报告")
        if not reports:
            if self.untabled():
                raise unable("PDF原件未能读出表格，尚不能确认是否包含尺寸报告，需人工复核。")
            return "NG", "缺少尺寸报告。"
        notes, wrong, outside, unknown, skipped = [], [], [], [], 0
        for report in reports:
            filled = [column for column in report.band if any(report.sheet.cell(row.row_index, column).has_data() for row in report.rows)]
            where = f"尺寸报告“{report.sheet.title}”"
            if not filled or len(filled) % 3:
                self.cite(report.sheet, report.header, report.band[0])
                wrong.append(f"{where}实测值有读数的列数为{len(filled)}，不是3或3的倍数")
                continue
            notes.append(f"{where}实测值有读数的列数为{len(filled)}")
            for row in self.requirement_rows(report):
                over, open_, _values = self.ranges(report, row)
                requirement = row.value(f"{report.title}要求")
                if limits(requirement) is None and bare(requirement):
                    skipped += 1
                if over:
                    outside.append(
                        Place.of(f"“{report.sheet.title}”第{row.row_index}行要求“{requirement}”实测值{'、'.join(value for value, _column in over)}超差", report.sheet, row.row_index, over[0][1])
                    )
                unknown += open_
        if wrong:
            return "NG", "；".join(wrong) + "。"
        if outside:
            listed = "；".join(map(self.named, outside[:8])) + ("等" if len(outside) > 8 else "")
            approvals = self.approvals()
            if not approvals:
                self.need("顾客工程批准或偏差许可")
                return "NG", f"{listed}；同批次没有顾客工程批准或偏差许可，按超差判不通过。"
            raise unable(f"{listed}；同批次有{'、'.join(original_name(item) for item in approvals)}，检查程序未能核对其是否覆盖这些超差项，需人工复核。")
        if unknown:
            raise unable("；".join(notes) + "；检查程序未能确认：" + "；".join(map(self.named, unknown[:8])) + ("等" if len(unknown) > 8 else "") + "，需人工复核是否超差。")
        note = f"（{skipped}项要求只写名义值、报告上没有公差，未按区间核对）" if skipped else ""
        return "PASS", "；".join(notes) + f"；有公差的定量尺寸实测值均在名义值与上下公差构成的区间内{note}。"

    # ---- IR:2 ----------------------------------------------------------------------------------

    @rule("四报告要求列与实测值及OK")
    def rule_2(self, ctx: RuleContext):
        self.readable()
        reports, missing = self.all_reports(ctx)
        if missing:
            return "NG", "文件缺少" + "、".join(missing) + "。"
        violations, unknown, reminders, checked = [], [], [], 0
        for report in reports:
            rows = self.requirement_rows(report)
            if not rows:
                mark = self.exemption(report)
                if mark is not None:
                    reminders.append(f"{report.sheet.title}整表标注“{mark}”，按豁免视为要求列有内容；提示：建议供应商书面确认图纸与规范确未规定对应试验")
                    continue
                self.cite(report.sheet, report.header, report.rows[0].columns[f"{report.title}要求"].header_col if report.rows else 1)
                violations.append(f"“{report.sheet.title}”要求列没有内容")
                continue
            for row in rows:
                checked += 1
                where = f"“{report.sheet.title}”第{row.row_index}行"
                verdict = self.verdict(report, row.row_index)
                over, open_, values = self.ranges(report, row)
                requirement = row.value(f"{report.title}要求")
                if over:
                    violations.append(Place.of(f"{where}要求“{requirement}”实测值{'、'.join(value for value, _column in over)}超出要求范围", report.sheet, row.row_index, over[0][1]))
                if verdict is False:
                    violations.append(Place.of(f"{where}结果不是OK", report.sheet, row.row_index, report.nok_column or report.ok_column or 1))
                elif verdict is None:
                    unknown.append(Place.of(f"{where}结果栏没有可确认的OK", report.sheet, row.row_index, report.ok_column or 1))
                unknown += open_
                if limits(requirement) is not None and not values and not over and not open_:
                    unknown.append(Place.of(f"{where}要求“{requirement}”没有供应商实测值", report.sheet, row.row_index, report.band[0]))
        reminder = ("；" + "；".join(reminders)) if reminders else ""
        if violations:
            return "NG", "；".join(map(self.named, violations[:10])) + ("等" if len(violations) > 10 else "") + reminder + "。"
        if unknown:
            raise unable("检查程序未能确认：" + "；".join(map(self.named, unknown[:8])) + ("等" if len(unknown) > 8 else "") + reminder + "，需人工复核。")
        return "PASS", f"四份报告共{checked}项要求的实测值均在要求范围内且结果为OK{reminder}。"

    # ---- IR:3 ----------------------------------------------------------------------------------

    def signature_area(self, report: Report) -> tuple[int, int, int, int] | None:
        """(first row, first column, last row, last column) of a report's 供应商负责人 area: its label rows, from the
        label to the customer's sign-off on those rows."""
        rows, columns = self.extent(report.sheet)
        markers = [compact(marker) for marker in SIGNATURE_MARKERS]
        labels = [cell for cell in self.cells(report.sheet, row_min=report.header) if any(marker in compact(self.raw(cell)) for marker in markers)]
        if not labels:
            return None
        first, last = min(cell.row_index for cell in labels), max(cell.row_index for cell in labels)
        left = min(cell.col_index for cell in labels)
        customers = [compact(marker) for marker in CUSTOMER_MARKERS]
        right = min(
            (
                cell.col_index
                for row in range(first, last + 1)
                for cell in self.cells(report.sheet, row_min=row, row_max=row)
                if cell.col_index > left and any(marker in compact(self.raw(cell)) for marker in customers)
            ),
            default=columns + 1,
        )
        return first, left, last, right - 1

    def pictures(self, report: Report) -> list[tuple[tuple[int, int, int, int], bytes | None]] | None:
        path = str(self.audit_file.file_url)
        if path.lower().endswith(".pdf"):
            return None
        cache = self.__dict__.setdefault("_sheet_pictures", {})
        if report.sheet.title not in cache:
            try:
                cache[report.sheet.title] = xls_pictures(path, report.sheet.title) if path.lower().endswith(".xls") else xlsx_pictures(path, report.sheet.title)
            except Exception:
                cache[report.sheet.title] = None
        return cache[report.sheet.title]

    def picture(self, data: bytes | None) -> dict[str, Any]:
        cache = self.__dict__.setdefault("_picture_facts", {})
        key = hashlib.sha256(data or b"").hexdigest()
        if key not in cache:
            if data is None:
                cache[key] = {"handwritten_signature": None, "ink_seal": None}
            else:
                from PIL import Image

                output = BytesIO()
                with Image.open(BytesIO(data)) as image:
                    image.convert("RGB").save(output, format="PNG")
                cache[key] = self.transcribe(output.getvalue(), PICTURE_PROMPT, PICTURE_SCHEMA, "样品检验报告签署区的图片")
        return cache[key]

    @rule("有要求的报告供应商负责人签字与印章")
    def rule_3(self, ctx: RuleContext):
        self.readable()
        reports, missing = self.all_reports(ctx)
        if missing:
            return "NG", "文件缺少" + "、".join(missing) + "。"
        applicable = [report for report in reports if self.requirement_rows(report) or self.exemption(report) is not None]
        if not applicable:
            return "NG", "四份报告的要求列都没有内容，没有需供应商负责人签字的报告。"
        unsigned, unknown, signed, seals, open_seal = [], [], [], [], False
        from openpyxl.utils.cell import get_column_letter

        for report in applicable:
            area = self.signature_area(report)
            if area is None:
                unsigned.append(f"“{report.sheet.title}”没有供应商负责人签署区")
                continue
            first, left, last, right = area
            self.cite(report.sheet, first, left)
            pictures = self.pictures(report)
            if pictures is None:
                unknown.append(f"“{report.sheet.title}”签署区的图片")
                continue
            inside = [(anchor, data) for anchor, data in pictures if first - 1 <= anchor[0] <= last + 1 and left <= anchor[1] <= right]
            if not inside:
                unsigned.append(f"“{report.sheet.title}”供应商负责人处没有签字")
                continue
            facts = [(anchor, self.picture(data)) for anchor, data in inside]
            for anchor, fact in facts:
                locator = {"kind": "xlsx_range", "sheet": report.sheet.title, "start": f"{get_column_letter(anchor[1])}{anchor[0]}", "end": f"{get_column_letter(anchor[3])}{anchor[2]}"}
                if fact.get("handwritten_signature") or fact.get("ink_seal"):
                    self.witness(locator, "签署区的签名" if fact.get("handwritten_signature") else "签署区的印章", visual=True)
            marks = [fact.get("handwritten_signature") for _anchor, fact in facts]
            if True in marks:
                signed.append(report.sheet.title)
            elif None in marks:
                unknown.append(f"“{report.sheet.title}”供应商负责人处图片是否为手写签名")
            else:
                unsigned.append(f"“{report.sheet.title}”供应商负责人处没有手写签字")
            stamps = [fact.get("ink_seal") for _anchor, fact in facts]
            if True in stamps:
                seals.append(report.sheet.title)
            elif None in stamps:
                open_seal = True
        if unsigned:
            return "NG", "；".join(unsigned) + "。"
        if unknown:
            raise unable("检查程序未能核实" + "、".join(unknown) + "，需人工复核。")
        if not seals:
            if open_seal:
                raise unable("供应商负责人均已签字；签署区图片是否为供应商印章未能核实，需人工复核。")
            return "NG", "要求的供应商负责人均已签字（" + "、".join(signed) + "），但签署区没有任何有效印章，签署不完整。"
        return "PASS", "有要求的报告（" + "、".join(signed) + "）供应商负责人均已签字，签署区有供应商印章（" + "、".join(seals) + "）。"

    # ---- IR:4 ----------------------------------------------------------------------------------

    def identity(self, ctx: RuleContext) -> tuple[str, str]:
        parts: set[str] = set()
        names: set[str] = set()
        for title in REPORTS:
            for sheet in Table(ctx).find_sheets(title, on_missing=Decision.IGNORE):
                for cell in self.cells(sheet, row_max=6):
                    match = re.match(r"\s*(.+?)\s*[:：]\s*(.+)", self.raw(cell), re.S)
                    if match and compact(match.group(1)) == compact("Part Number/零件号"):
                        parts.add(match.group(2).strip())
                    if match and compact(match.group(1)) == compact("Part Name/零件名称"):
                        names.add(match.group(2).strip())
        return "、".join(sorted(parts)) or "未写", "、".join(sorted(names)) or "未写"

    @rule("同物料号与名称的气泡图及双向对齐")
    def rule_4(self, ctx: RuleContext):
        part, name = self.identity(ctx)
        embedded = [ws.title for ws in ctx.workbook.worksheets if any(compact(word) in compact(ws.title) for word in BUBBLE)]
        found = self.named_files(BUBBLE)
        if not found and not embedded:
            self.need("气泡图", part=None if part == "未写" else part)
            return "NG", f"同批次没有物料编号“{part}”、物料名称“{name}”的气泡图，ISIR内也没有气泡图页，缺少气泡图。"
        places = [f"ISIR内工作表“{title}”" for title in embedded] + [f"同批次《{original_name(item)}》" for item in found]
        raise unable(f"有可能的气泡图（{'、'.join(places)}），检查程序尚不能读取气泡图的件号与气泡编号，未能核对物料编号“{part}”、物料名称“{name}”及与ISIR尺寸项的双向对齐和逐项尺寸公差，需人工复核。")

# ruff: noqa: E402, F821  # a candidate leaf binds the declared reader's names at run time; it may not import it
