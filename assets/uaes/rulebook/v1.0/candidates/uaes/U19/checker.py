"""UAES U19 candidate: customer engineering approval (C sample release report), UAES v1.0 revision 3 (CEA:1).

The cover (F-NE-185) and the deviation release list are read as tables, and the cover's PDF text layer and
drawings for the sign-off and the reject column. Only when the reject column or the sign-off cannot be told from
those is an image of the cover transcribed by the image model; every verdict is decided in code.
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


# ---- CEA ------------------------------------------------------------------------------------------

from audit.engine.rule_engine.contracts import ColumnModel, SectionModel, SheetModel

_SHEETS = ("封面", "cover", "认可报告", "偏差", "deviation", "table_", "page-", "sheet")
COVER = (
    (SheetModel(canonical_name="认可封面", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="认可区",
            sheet_name="认可封面",
            header_anchors=("报告内容", "带条件批准", "否决"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET,
            header_search_max_col=WHOLE_SHEET,
            row_anchors=("报告内容",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET,
            row_stop_markers=("审核：", "Checked by"),
        ),
    ),
    (
        ColumnModel(canonical_name="报告内容", section_name="认可区", aliases=("报告内容",)),
        ColumnModel(canonical_name="带条件批准", section_name="认可区", aliases=("带条件批准",)),
        ColumnModel(canonical_name="否决", section_name="认可区", aliases=("否决",)),
    ),
)
DEVIATIONS = (
    (SheetModel(canonical_name="偏差认可汇总", include_keywords=_SHEETS),),
    (
        SectionModel(
            canonical_name="偏差区",
            sheet_name="偏差认可汇总",
            header_anchors=("偏差项", "UAES评估确认", "是否需要偏差"),
            header_hit=3,
            header_search_max_row=WHOLE_SHEET,
            header_search_max_col=WHOLE_SHEET,
            row_anchors=("是否需要偏差",),
            row_stop_on_first_blank=False,
            row_max_blank_streak=WHOLE_SHEET,
            row_stop_markers=("供应商申请人", "Supplier Proposer"),
        ),
    ),
    (
        ColumnModel(canonical_name="偏差项", section_name="偏差区", aliases=("偏差项",)),
        ColumnModel(canonical_name="UAES评估确认", section_name="偏差区", aliases=("UAES 评估确认", "UAES评估确认")),
        # The clause's 是否需要偏差 is headed "是否同意\n偏差\nYes or\nNo" on the list.
        ColumnModel(canonical_name="是否需要偏差", section_name="偏差区", aliases=("是否同意 偏差", "是否同意偏差", "是否需要偏差")),
        ColumnModel(canonical_name="偏差备注", section_name="偏差区", aliases=("备注",)),
    ),
)
PART_NUMBER = ("零件号\nPart No.",)
PART_NAME = ("零件名称\nPart name", "零件名称\nPart Name")
REMARK = "UAES批准的前提"
CATEGORIES = ("尺寸", "材料", "性能", "试装")
ROLES = ("组长", "审核", "批准")
ROLE_NAMES = {"组长": "组长（Team Leader）", "审核": "审核（Checked by）", "批准": "批准（Approved by）"}
LABEL_WORDS = {"日期", "date", "team", "leader", "checked", "approved", "by"}
_DATE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
_MARK = re.compile(r"[×xX☑✓√✔■●]")
FULL = "完全认可"
# CEA:1: a remark that carries a deviation, an out-of-tolerance or a rectification promise takes the deviation path.
DEVIATION_TERMS = ("偏差", "超差", "整改", "条件认可")
# Two statements of the remark that contradict each other: an approval and its refusal.
REFUSALS = ("不认可", "不予认可", "不同意", "不批准")
_APPROVAL_KINDS = ("E11", "顾客工程批准")

_MARK_SCHEMA = {"type": ["boolean", "null"]}
REJECT_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(CATEGORIES), "properties": {name: _MARK_SCHEMA for name in CATEGORIES}}
REJECT_PROMPT = (
    "这是C样品认可报告封面的原件图像。请只转写封面“报告内容 / 批准 / 带条件批准 / 否决(Reject)”表格中“否决 Reject”一列，不判断是否合格。\n"
    "尺寸、材料、性能、试装四行各一个字段：该行“否决”格里有实际的勾、叉、符号或涂色填充为 true；空格为 false；看不清为 null。"
)
_ROLE = {"type": "object", "additionalProperties": False, "required": ["signed", "date"], "properties": {"signed": _MARK_SCHEMA, "date": {"type": ["string", "null"]}}}
SIGN_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(ROLES), "properties": {role: _ROLE for role in ROLES}}
SIGN_PROMPT = (
    "这是C样品认可报告封面的原件图像。请转写封面底部组长(Team Leader)、审核(Checked by)、批准(Approved by)三处，不判断是否合格。\n"
    "signed：该处有实际签名（手写或姓名）为 true，空白为 false，看不清为 null。date：照原样转写该处日期，空白写空字符串，看不清写 null。"
)


def _day(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def deadlines(text: str) -> list[date | None]:
    """Promised latest dates: "截止<date>", "临时偏差至<date>", "<date>前完成"; never an expected rectification time."""
    found = re.findall(r"(?:截止|临时偏差至)\s*(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    found += re.findall(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*前完成", text)
    return [_day(*parts) for parts in found]


def temporary(text: str) -> list[date | None]:
    """The "临时偏差至<date>" validities a list remark writes."""
    return [_day(*parts) for parts in re.findall(r"临时偏差至\s*(\d{2,4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", "".join(text.split()))]


def _parse(text: Any) -> date | None:
    from audit.engine.checkers.date_utils import normalize_excel_date

    match = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", unicodedata.normalize("NFKC", str(text or "")))
    if match:
        return _day(*match.groups())
    found = normalize_excel_date(text) if text else None
    return found if isinstance(found, date) else None


class _Here:
    def __init__(self, ctx: RuleContext) -> None:
        self.ctx = ctx

    def __enter__(self) -> RuleContext:
        return self.ctx

    def __exit__(self, *exc: object) -> None:
        return None


class ApprovalR3(Reader):
    DOCUMENT = COVER
    PDF_SPECS = (PER_PAGE, STITCHED)
    EXTRA_RESOURCE = resource(COVER, DEVIATIONS)

    def pdf(self) -> bool:
        return str(self.audit_file.file_url).lower().endswith(".pdf")

    def pdf_witness(self, page: int, bounds: Any, text: str) -> None:
        self.witness({"kind": "pdf_region", "page": page, "bbox": [round(float(value), 2) for value in bounds], "coordinate_system": "pdf_points_unrotated"}, text)

    def page_image(self) -> bytes:
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            return document[0].get_pixmap(dpi=200).tobytes("png")

    # ---- cover ---------------------------------------------------------------------------------

    def cover(self, ctx: RuleContext) -> tuple[Sheet, Any]:
        found = [(sheet, section) for sheet in Table(ctx).find_sheets("认可封面", on_missing=Decision.IGNORE) for section in [self.located(sheet, "认可区")] if section]
        if len(found) != 1:
            if not found and (self.untabled() or self.unread_pages()):
                raise unable("PDF原件未能读出表格，检查程序未能读取认可报告封面，需人工复核。")
            raise unable(f"检查程序{'未能定位' if not found else f'定位到{len(found)}处'}封面“报告内容/批准/带条件批准/否决”表格，需人工复核。")
        return found[0]

    def rejected(self, sheet: Sheet, section: Any) -> str | None:
        """The category whose 否决 cell carries a mark; "" when none does; None when a drawn mark cannot be excluded."""
        rows = {}
        for row in section.rows("报告内容"):
            name = str(row.value("报告内容") or "")
            category = next((item for item in CATEGORIES if item in name), None)
            if category:
                rows[category] = row
        if set(rows) != set(CATEGORIES):
            missing = "、".join(item for item in CATEGORIES if item not in rows)
            raise unable(f"封面“报告内容”下未能读取{missing}行，检查程序未能确认否决列，需人工复核。")
        column = section.column("否决").header_col
        for category, row in rows.items():
            mark = str(sheet.cell(row.row_index, column).value() or "").strip()
            if _MARK.search(mark):
                self.cite(sheet, row.row_index, column)
                return category
            if mark:
                return None
        if self.pdf():
            return self.drawn_reject()
        path = str(self.audit_file.file_url)
        if path.lower().endswith((".xlsx", ".xlsm")):
            import zipfile

            with zipfile.ZipFile(path) as archive:
                if not any(name.startswith("xl/drawings/") for name in archive.namelist()):
                    return ""
        return None

    def drawn_reject(self) -> str | None:
        """Strokes, pictures or words inside the 否决 column cells of the PDF cover; "" when it is clean."""
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            drawings = page.get_drawings()
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        heading = [rect for rect, text in words if text == "否决"]
        tryout = [rect for rect, text in words if text in {"试装", "Tryout"}]
        if len(heading) != 1 or not tryout:
            return None
        head = heading[0]
        under = max(rect.y1 for rect, text in words if text in {"否决", "Reject"} and abs(rect.x0 - head.x0) < 40)
        vertical = [item["rect"] for item in drawings if item["rect"].width <= 3 and item["rect"].height > 40 and item["rect"].y0 <= head.y0]
        horizontal = [item["rect"] for item in drawings if item["rect"].height <= 3 and item["rect"].width > 40]
        left = max((rect.x1 for rect in vertical if rect.x1 <= head.x0), default=None)
        right = min((rect.x0 for rect in vertical if rect.x0 >= head.x1), default=None)
        top = min((rect.y1 for rect in horizontal if rect.y0 >= under and rect.x0 < head.x0 and rect.x1 > head.x1), default=None)
        bottom = min((rect.y0 for rect in horizontal if rect.y0 >= max(rect.y1 for rect in tryout) and rect.x0 < head.x0 and rect.x1 > head.x1), default=None)
        if left is None or right is None or top is None or bottom is None:
            return None
        cells = fitz.Rect(left + 1.5, top + 1.5, right - 1.5, bottom - 1.5)

        def stroke(item: Any) -> bool:
            return any(part[0] == "c" or part[0] == "l" and abs(part[1].x - part[2].x) > 1 and abs(part[1].y - part[2].y) > 1 for part in item["items"])

        marks = [item["rect"] for item in drawings if stroke(item) and item["rect"].intersects(cells) and (item["rect"] & cells).get_area() > 0]
        filled = [
            item["rect"] for item in drawings if item.get("fill") is not None and item["rect"].intersects(cells) and (item["rect"] & cells).get_area() > 4 and item["rect"].width < cells.width - 1
        ]
        self.pdf_witness(1, cells, f"封面否决列：{len(marks) + len(filled)}处图形标记")
        if marks or filled:
            return "否决列"
        if any(rect.intersects(cells) for rect in images) or any(rect.intersects(cells) for rect, _text in words):
            return None
        return ""

    def remark(self, sheet: Sheet) -> tuple[str, Any]:
        rows, columns = self.extent(sheet)
        cells = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(REMARK) in compact(cell.value())]
        if len(cells) != 1:
            raise unable(f"封面{'未能定位' if not cells else '有多处'}“UAES 批准的前提/备注”栏，检查程序未能读取，需人工复核。")
        text = str(cells[0].value())
        text = re.split(r"remark", text, maxsplit=1, flags=re.I)[-1]
        return re.split(r"组长\s*[:：]", text, maxsplit=1)[0].strip(), cells[0]

    def cover_signatures(self) -> dict[str, tuple[str, date | None]] | None:
        """Role -> (name, date) read beside each cover role label on the text layer; None when only an image could tell."""
        import fitz
        from audit.engine.checkers.date_utils import normalize_excel_date

        if not self.pdf():
            return None
        with fitz.open(str(self.audit_file.file_url)) as document:
            page = document[0]
            words = [(fitz.Rect(word[:4]), word[4]) for word in page.get_text("words")]
            images = [fitz.Rect(image["bbox"]) for image in page.get_image_info()]
        labels = {}
        for role in ROLES:
            found = [rect for rect, text in words if text.rstrip(":：") == role and text[-1:] in ":："]
            if len(found) != 1:
                return None
            labels[role] = found[0]
        signed = {}
        for role, label in labels.items():
            bottom = label.y1 + label.height
            right = min((other.x0 for other in labels.values() if other.x0 > label.x0 and other.y0 < bottom and other.y1 > label.y0), default=float("inf"))
            entry = [(rect, text) for rect, text in words if rect.x0 > label.x1 - 1 and rect.x1 <= right + 1 and rect.y0 < bottom and rect.y1 > label.y0]
            day = next((normalize_excel_date(text) for _rect, text in entry if _DATE.fullmatch(text)), None)
            name = " ".join(text for _rect, text in entry if not _DATE.fullmatch(text) and text.rstrip(":：").casefold() not in LABEL_WORDS)
            if (not name or day is None) and any(image.intersects(fitz.Rect(label.x0, label.y0, min(right, 1e6), bottom)) for image in images):
                return None
            if entry:
                area = fitz.Rect(label)
                for rect, _text in entry:
                    area |= rect
                self.pdf_witness(1, area, f"{role}：{name} {day or ''}".strip())
            signed[role] = (name, day)
        return signed

    def identity(self, ctx: RuleContext, sheet: Sheet) -> tuple[str, str]:
        values = []
        for name, aliases in (("零件号", PART_NUMBER), ("零件名称", PART_NAME)):
            wanted = {compact(alias) for alias in aliases}
            rows, columns = self.extent(sheet)
            labels = [cell for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns) if compact(cell.value()) in wanted]
            if len(labels) != 1:
                raise unable(f"“{sheet.title}”中{'未能定位' if not labels else '有多处'}{name}字段，检查程序未能读取，需人工复核。")
            merged = ctx.ops.get_detailed_merge_info(sheet.ws, labels[0].row_index, labels[0].col_index)
            values.append(str(sheet.cell(labels[0].row_index, (merged.max_col if merged else labels[0].col_index) + 1).value() or "").strip())
        return values[0], values[1]

    # ---- CEA:1 ---------------------------------------------------------------------------------

    @rule("否决前置与完全认可或偏差认可两路判定")
    def rule_1(self, ctx: RuleContext):
        if unread := self.unread_pages():
            raise unable(f"原件第{'、'.join(map(str, unread))}页为扫描页（没有文字层），检查程序不读取扫描件，需人工复核。")
        sheet, section = self.cover(ctx)
        rejected = self.rejected(sheet, section)
        if rejected is None:
            if not self.pdf():
                raise unable("封面否决列有图形或文字，检查程序尚不能对表格原件做视觉层取证，需人工复核。")
            marks = self.transcribe(self.page_image(), REJECT_PROMPT, REJECT_SCHEMA, "封面否决列")
            self.witness({"kind": "pdf_page", "page": 1, "engine": "ocr_llm", "fact": "reject"}, "、".join(f"{name}:{marks.get(name)}" for name in CATEGORIES), visual=True)
            marked = [name for name in CATEGORIES if marks.get(name) is True]
            if marked:
                return "NG", f"封面否决列{'、'.join(marked)}有否决标记，为否决件，直接不通过。"
            if any(marks.get(name) is None for name in CATEGORIES):
                return "NG", "封面否决列的勾选经视觉层取证后仍无法读出，判不通过，请人工复核。"
        elif rejected:
            return "NG", f"封面否决列{rejected}有否决标记，为否决件，直接不通过。"
        remarks, cell = self.remark(sheet)
        self.cite(sheet, cell.row_index, cell.col_index)
        full = FULL in remarks
        deviation = [term for term in DEVIATION_TERMS if term in remarks]
        refused = [term for term in REFUSALS if term in remarks]
        if refused and (full or deviation):
            return "NG", f"封面备注“{remarks}”既写认可又写“{'、'.join(refused)}”，两处表述相互冲突，认可结论无法辨识，判不通过，请人工复核。"
        if not full and not deviation:
            return "NG", f"封面备注“{remarks}”不能归入完全认可或偏差认可／条件认可，认可结论无法辨识，判不通过，请人工复核。"
        if not deviation:
            return self.full_approval()
        route = f"备注写有“{FULL}”，但同时载有“{'、'.join(deviation)}”，按偏差／条件认可路径判定；" if full else f"备注载有“{'、'.join(deviation)}”，按偏差／条件认可路径判定；"
        return self.deviation_approval(ctx, sheet, remarks, route)

    def full_approval(self) -> tuple[str, str]:
        na = "偏差认可汇总清单、整改期限、临时偏差有效期、两处期限一致性四项在完全认可路径下无判定对象，记不适用（仅作提醒）。"
        signed = self.cover_signatures()
        if signed is None:
            if not self.pdf():
                raise unable("封面完全认可；检查程序尚未能从表格原件读取组长、审核、批准的签名与日期，需人工复核。")
            read = self.transcribe(self.page_image(), SIGN_PROMPT, SIGN_SCHEMA, "封面三方签名与日期")
            self.witness({"kind": "pdf_page", "page": 1, "engine": "ocr_llm", "fact": "sign_off"}, json.dumps(read, ensure_ascii=False), visual=True)
            if any((read.get(role) or {}).get("signed") is None or (read.get(role) or {}).get("date") is None for role in ROLES):
                raise unable("封面完全认可；组长、审核、批准的签名或日期未能从原件图像辨识，需人工复核。")
            signed = {role: ("签名" if read[role]["signed"] else "", _parse(read[role]["date"])) for role in ROLES}
        missing = [ROLE_NAMES[role] for role, (name, day) in signed.items() if not name or day is None]
        if missing:
            return "NG", "封面完全认可，但" + "、".join(missing) + "缺少签名或日期。" + na
        listed = "，".join(f"{role}{name} {day}" for role, (name, day) in signed.items())
        days = [day for _name, day in signed.values()]
        if days != sorted(days):
            return "NG", "封面完全认可，但签字日期不满足组长≤审核≤批准：" + listed + "。" + na
        return "PASS", "封面完全认可，" + listed + "，签名及日期齐全且组长≤审核≤批准。" + na

    def deviation_approval(self, ctx: RuleContext, sheet: Sheet, remarks: str, route: str) -> tuple[str, str]:
        today = date.today()
        failures: list[str] = []
        promised = deadlines(remarks)
        deadline: date | None = None
        if any(item is None for item in promised):
            raise unable(f"{route}封面备注“{remarks}”中的承诺期限未能读为日期，需人工复核。")
        if promised:
            deadline = max(item for item in promised if item is not None)
            if deadline <= today:
                failures.append(f"封面整改承诺的最晚期限{deadline}不晚于当前日期{today}")
        elif re.search(r"\d{1,4}\s*[-/.年月]\s*\d{1,2}", remarks):
            raise unable(f"{route}封面备注“{remarks}”中写有日期，但未能读出整改承诺的最晚期限（…前完成／截止…；预计整改时间不算），需人工复核。")
        else:
            failures.append("封面没有整改承诺的最晚期限")
        part, name = self.identity(ctx, sheet)
        lists = self.deviation_lists(ctx, part, name)
        if not lists:
            self.need("偏差认可汇总清单", part=part)
            failures.append(f"文件内及同批次没有零件号“{part}”、零件名称“{name}”的偏差认可汇总清单")
            return "NG", route + "；".join(failures) + "。"
        if len(lists) > 1:
            raise unable(f"{route}文件内及同批次有{len(lists)}份同零件的偏差认可汇总清单，尚不能确定按哪一份核对，需人工复核。")
        item = lists[0]
        with self.reading(ctx, item, DEVIATIONS, (PER_PAGE, STITCHED)) if item is not None else _Here(ctx):
            listed = self.list_deadlines(ctx)
        valid = [day for day, _item in listed]
        expired: dict[date, list[str]] = {}
        for day, entry in listed:
            if day <= today and entry not in expired.setdefault(day, []):
                expired[day].append(entry)
        for day, entries in sorted(expired.items()):
            if entries:
                failures.append(f"清单{'、'.join(entries)}临时偏差至{day}，有效期不晚于当前日期{today}")
        if deadline is not None:
            if not valid:
                if not failures:
                    raise unable(f"{route}清单备注中没有“临时偏差至<日期>”，封面期限{deadline}没有可比的清单期限，需人工复核。")
            elif deadline != max(valid):
                failures.append(f"封面期限{deadline}与清单期限（{'、'.join(str(day) for day in sorted(set(valid)))}，最晚{max(valid)}）不一致")
        if failures:
            return "NG", route + "；".join(failures) + "。"
        return "PASS", route + f"同零件的偏差认可汇总清单四个字段齐全；封面整改承诺最晚期限{deadline}晚于当前日期，与清单最晚有效期一致；清单临时偏差有效期均晚于当前日期。"

    def deviation_lists(self, ctx: RuleContext, part: str, name: str) -> list:
        """The lists (None for this file) whose header names this part number and part name among its own."""
        found: list[Any] = []
        unknown: list[str] = []
        if self.list_matches(ctx, part, name):
            found.append(None)
        candidates = {item.audit_id: item for item in [*self.batch_files(*_APPROVAL_KINDS), *self.named_files(("偏差认可汇总", "偏差"))]}
        for item in candidates.values():
            with self.reading(ctx, item, DEVIATIONS, (PER_PAGE, STITCHED)):
                matched = self.list_matches(ctx, part, name)
            if matched:
                found.append(item)
            elif matched is None or self.untabled(str(item.file_url)):
                unknown.append(original_name(item))
        if not found and unknown:
            raise unable(f"同批次原件（{'、'.join(unknown)}）未能读取，尚不能确认是否为同零件的偏差认可汇总清单，需人工复核。")
        return found

    def list_matches(self, ctx: RuleContext, part: str, name: str) -> bool | None:
        for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE):
            if self.located(sheet, "偏差区") is None:
                continue
            try:
                theirs = self.identity(ctx, sheet)
            except GuardStop:
                return None
            # A list may cover several parts: "F01RD0YC50/F01RC0Y796", "机加工壳体 / 毛坯件壳体".
            parts = {compact(value) for value in re.split(r"[/、,，;；]", theirs[0])}
            names = {compact(value) for value in re.split(r"[/、,，;；]", theirs[1])}
            return compact(part) in parts and compact(name) in names
        return False

    def list_deadlines(self, ctx: RuleContext) -> list[tuple[date, str]]:
        """Each "临时偏差至<date>" of the list's remark column, with the deviation item it belongs to."""
        sheet = next(sheet for sheet in Table(ctx).find_sheets("偏差认可汇总", on_missing=Decision.IGNORE) if self.located(sheet, "偏差区") is not None)
        section = self.section(sheet, "偏差区")
        found = []
        current = ""
        for row in section.rows("偏差项", "UAES评估确认", "是否需要偏差", "偏差备注"):
            current = str(row.value("偏差项") or "").strip() or current
            for day in temporary(str(row.value("偏差备注") or "")):
                if day is None:
                    raise unable(f"偏差认可汇总清单第{row.row_index}行“临时偏差至”后的日期未能读取，需人工复核。")
                self.cite(sheet, row.row_index, row.columns["偏差备注"].header_col)
                found.append((day, current or f"第{row.row_index}行"))
        return found

# ruff: noqa: E402, F821  # a candidate leaf binds the declared reader's names at run time; it may not import it
