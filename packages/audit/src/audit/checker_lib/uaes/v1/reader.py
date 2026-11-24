"""Table and picture reading shared by the UAES v1.0 readers built on the generic Excel reader (IR, SSH, PSW, CEA)."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from documents.pdf import GroupingMode, GroupingSpec, OutputSpec, PdfSpec, StitchMode, StitchSpec

from audit.checker_lib.excel_agent import ExcelAgent
from audit.contracts import AuditEvidence, EvidenceSource, SupplyNeed
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet
from audit.engine.rule_engine.contracts import RuleResourceModel, index_rule_resource

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
        from documents.ai import AIError
        from documents.model_access import ModelUnavailable

        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from audit.engine.runtime import get_runtime

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
        except AIError:
            # A timed-out, refused or malformed model answer is a failed model request, not an unreadable original.
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
