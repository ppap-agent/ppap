"""UAES clause checks on the shared rule engine, reading only UAES's own resource."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from audit.checker_lib.nio.excel_agent import ExcelAgent
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


class UaesExcelAgent(ExcelAgent):
    """A UAES carrier: its sheets, sections and columns come from its EXTRA_RESOURCE only."""

    # The resource module of the document this carrier audits: its DOCUMENT and PDF_SPECS.
    DOCUMENT_MODULE: Any = None
    # Formats the engine reads as tables; a DOCX carrier stays on its own reader.
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})

    def _resolve_resource_doc_type(self):
        # The resources registered for li/nio document types are not UAES's templates.
        return None

    def batch_files(self, kind: str) -> list[Any]:
        """Same-round originals of one UAES type, other than this file."""
        if self.group_context is None:
            return []
        return [item for item in self.group_context.get_files_by_type(kind) if item.audit_id != self.audit_file.audit_id]

    @contextmanager
    def reading(self, ctx: RuleContext, item: Any, document: Any) -> Iterator[RuleContext]:
        """Read one frozen same-round original with this resource; its cells are evidence of its version."""
        from audit.checker_lib.nio.cross_file_extractors import _load_workbook_auto

        metadata = getattr(item, "file_metadata", None) or {}
        version = str(metadata.get("file_version_id") or item.audit_id)
        cache = self.__dict__.setdefault("_uaes_related", {})
        if version not in cache:
            source = Path(str(item.file_url))
            expected = metadata.get("source_sha256") or metadata.get("sha256")
            if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise OSError("本轮冻结关联资料已变化，不能完成本项核对。")
            cache[version] = self.convert(str(source), document)[1] if source.suffix.lower() == ".pdf" else _load_workbook_auto(str(source))
        with ctx.reading(cache[version], version):
            yield ctx

    def convert(self, path: str, document: Any) -> tuple[Any, Any, Any]:
        """(artifact, workbook, spec): the first of the document's layouts that yields one of its sheets; else the last that converted."""
        from audit.engine.checkers.pdf_evidence import PdfSourceError

        sheets = [sheet.canonical_name for sheet in document.DOCUMENT[0]]
        chosen = None
        for spec in document.PDF_SPECS:
            try:
                chosen = (*self._convert_pdf(path, spec), spec)
            except PdfSourceError:
                continue
            assert self._rule_resource is not None
            probe = RuleContext(None, chosen[1], self._rule_resource)
            if any(probe.find_sheets(name, on_missing=Decision.IGNORE) for name in sheets):
                break
        if chosen is None:
            # No layout found a table: nothing is read as a table; clauses decide on the text layer.
            from openpyxl import Workbook

            self.__dict__.setdefault("_untabled", set()).add(path)
            return None, Workbook(), None
        return chosen

    def untabled(self) -> bool:
        """Whether this PDF original yielded no table under any of its document's layouts."""
        return str(self.audit_file.file_url) in self.__dict__.get("_untabled", set())

    def pdf_mentions(self, *words: str) -> bool:
        """Whether the text layer of this PDF original names any of `words`."""
        if not str(self.audit_file.file_url).lower().endswith(".pdf"):
            return False
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            text = compact("".join(page.get_text() for page in document))
        return any(compact(word) in text for word in words)

    def _load_pdf_workbook(self, file_url: str) -> Any:
        self._conversion_artifact, self._workbook_cache, self._pdf_spec = self.convert(file_url, type(self).DOCUMENT_MODULE)
        return self._workbook_cache

    def legacy(self) -> Any:
        """The snapshot reader, kept only for facts an image of the original shows (signatures, seals, drawn marks)."""
        if "_legacy" not in self.__dict__:
            from .native import UaesSourceAgent

            self._legacy = UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context)
        return self._legacy

    def visual(self, fields: dict[str, str], description: str) -> tuple[str, str]:
        """Image facts of the original, each asked as a yes/no question."""
        return self._from_legacy(lambda agent: agent.visual_result(fields, description))

    def transcribe(self, image: bytes, prompt: str, schema: dict[str, Any], subject: str) -> dict[str, Any]:
        """One image of an original transcribed into `schema` by the image model; the model transcribes, code decides."""
        from audit.engine.checkers.ocr_visual_facts import request_ocr_visual_facts
        from audit.engine.runtime import get_runtime

        runtime = get_runtime()
        hook = getattr(runtime, "read_ocr_visual_facts", None)
        if runtime is not None and not callable(hook):
            raise unable(f"当前运行环境不提供原件图像转写，{subject}尚未核实，需人工复核。")
        request = hook if callable(hook) else request_ocr_visual_facts
        return self._from_legacy(lambda _agent: request(images=[image], prompt=prompt, response_schema={"name": "transcription", "schema": schema}))

    def drawing_title(self, item: Any) -> dict[str, Any]:
        """The title block of a same-round PDF drawing, transcribed once from the bottom right of its first page.

        A drawing's title block is often lettered with vector strokes that never reach the text layer.
        Its transcription is evidence of that drawing's own version.
        """
        version = str((getattr(item, "file_metadata", None) or {}).get("file_version_id") or item.audit_id)
        cache = self.__dict__.setdefault("_drawing_titles", {})
        if version not in cache:
            import fitz

            from .resources import drawing_title

            try:
                with fitz.open(str(item.file_url)) as document:
                    page = document[0]
                    area = fitz.Rect(page.rect.x1 * 0.55, page.rect.y1 * 0.7, page.rect.x1, page.rect.y1)
                    image = page.get_pixmap(dpi=min(200, int(2400 * 72 / area.width)), clip=area).tobytes("png")
                title = self.transcribe(image, drawing_title.PROMPT, drawing_title.SCHEMA, f"图纸{Path(str(item.file_url)).name}的标题栏")
                cache[version] = (title, {"kind": "pdf_region", "page": 1, "bbox": [area.x0, area.y0, area.x1, area.y1], "engine": "ocr_llm", "fact": "drawing_number"})
            except GuardStop as stop:
                cache[version] = stop
        if isinstance(cache[version], GuardStop):
            raise cache[version]
        title, locator = cache[version]
        # Each clause that relies on the title block carries it as its own evidence.
        self.legacy().witness(locator, title.get("drawing_number") or "", version, visual=True)
        return title

    def stamp(self) -> tuple[str, str]:
        """A controlled stamp: the words an EMF picture draws, else an image of the original.

        On a PDF original the clause fails when neither the image model nor Paddle's seal detection on every
        page finds one (product owner); a seal Paddle locates without the word 受控 is left for review.
        """
        from audit.engine.runtime import get_runtime

        status, message = self._from_legacy(lambda agent: agent.controlled_stamp())
        if status == "PASS" or not str(self.audit_file.file_url).lower().endswith(".pdf"):
            return status, message
        runtime = get_runtime()
        if runtime is not None and not callable(getattr(runtime, "read_ocr_visual_facts", None)):
            # The image model did not look here, so it did not "not find" a stamp.
            return status, message
        seals = self.paddle_seals()
        if seals is None:
            return status, message
        if any(compact(text) in {"受控", "受控章", "受控文件"} for texts in seals for text in texts):
            return "PASS", "Paddle印章检测在原页定位到受控章。"
        if seals:
            raise unable("Paddle印章检测在原页定位到印章，但未识别出“受控”字样，需人工复核是否为受控章。")
        return "NG", "LLM OCR与Paddle印章检测均未在原件中找到受控章，判不通过，请人工复核。"

    def paddle_seals(self) -> list[list[str]] | None:
        """The recognised words of each seal Paddle's seal detection locates on any page of this PDF original.

        Every page is read; None when one cannot be, since an unread page proves no absence.
        """
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf"):
            return None
        import fitz
        from documents.ocr.paddle.reader import PaddleEvidenceUnavailable, read_pdf_page_evidence

        from audit.engine.runtime import get_runtime
        from audit.pdf_tables import valid_pdf_bbox

        def read(agent: Any) -> list[list[str]] | None:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            # Shared with the image reads of the same original, which cache Paddle pages by (digest, page).
            cache = agent.__dict__.setdefault("_paddle_page_cache", {})
            reader = getattr(get_runtime(), "read_paddle_page", None)
            with fitz.open(path) as document:
                count = len(document)
            seals = []
            for number in range(1, count + 1):
                if (digest, number) not in cache:
                    try:
                        cache[(digest, number)] = reader(path, number) if callable(reader) else read_pdf_page_evidence(path, number)
                    except PaddleEvidenceUnavailable:
                        cache[(digest, number)] = None
                value = cache[(digest, number)]
                if value is None:
                    return None
                page = value["pages"][0]
                frame = page.get("frame") or {}
                for index, seal in enumerate(page.get("seals", [])):
                    if seal.get("detected") is True and valid_pdf_bbox(seal.get("pdf_bbox"), frame.get("pdf_width"), frame.get("pdf_height")):
                        locator = {"kind": "pdf_region", "page": number, "bbox": seal["pdf_bbox"], "element": "seal", "element_index": index, "engine": value.get("engine")}
                        agent.witness(locator, "Paddle检测到此区域存在印章", visual=True)
                        seals.append([str(text) for text in seal.get("recognized_text", [])])
            return seals

        return self._from_legacy(read)

    def _from_legacy(self, read: Any) -> Any:
        from documents.model_access import ModelUnavailable

        from .native import MissingFact, UnknownFact

        try:
            return read(self.legacy())
        except UnknownFact as exc:
            raise unable(str(exc)) from None
        except MissingFact as exc:
            return "NG", str(exc)
        except ModelUnavailable as exc:
            # Only a missing model grant leaves the fact open; a failed or uncertain request is a technical fault.
            if exc.code in ModelUnavailable.UNGRANTED:
                raise unable("当前没有可用的模型，所需原件事实尚未核实。") from None
            raise GuardStop(Decision.ERR, "原件模型请求失败或结果尚未确认，尚未取得业务结论。") from None
        except GuardStop:
            raise
        except Exception as exc:
            # A provider's own text never reaches the finding.
            raise GuardStop(Decision.ERR, f"原件读取失败（{type(exc).__name__}），尚未取得业务结论。") from None

    def _finalize_audit_result(self, result: Any, *, rule_no: int, phase: Any, ctx: RuleContext | None = None) -> Any:
        result = super()._finalize_audit_result(result, rule_no=rule_no, phase=phase, ctx=ctx)
        legacy = self.__dict__.get("_legacy")
        if legacy is None or not legacy._evidence_sources:
            return result
        sources, legacy._evidence_sources = tuple(legacy._evidence_sources), []
        from audit.contracts import AuditEvidence, EvidenceDiagnostic

        extension: Any = Path(str(self.audit_file.file_url)).suffix.lower().lstrip(".")
        evidence = result.evidence or AuditEvidence(
            required="structure_or_visual",
            used="none",
            source_format=extension,
            prepared_format=extension,
            sources=(),
            diagnostics=(EvidenceDiagnostic(code="uaes_source_fact", stage="evaluation", severity="info", message="客户原始条款的实际执行结果；未知事实未转为通过。"),),
        )
        merged = (*evidence.sources, *(source for source in sources if source not in evidence.sources))
        used = "both" if len({source.kind for source in merged}) > 1 else merged[0].kind
        # Each fact this clause read from the original image is a check made, as a Fluent read is.
        performed = (result.checks_performed or 0) + len(sources)
        return result.model_copy(update={"evidence": evidence.model_copy(update={"sources": merged, "used": used}), "checks_performed": performed})

    @staticmethod
    def section(sheet: Sheet, name: str):
        """The named data area of a found sheet; failing to locate its header is a reading gap, not a finding."""
        try:
            return sheet.section(name)
        except GuardStop as stop:
            if stop.decision != Decision.NG:
                raise
            raise unable(f"工作表“{sheet.title}”中未能定位{name}的表头，检查程序未能读取，需人工复核。") from None

    def drawn(self, sheet: Sheet, row: int, column: int) -> bool | None:
        """Whether a cell converted from this PDF original holds an image or a stroke on its page; None when it cannot be placed.

        A blank converted cell is not blank on the page when a graphic fills it; a PDF converts text only.
        """
        path = str(self.audit_file.file_url)
        if not path.lower().endswith(".pdf") or sheet.ws.parent is not self._workbook_cache:
            return False
        spec = self.__dict__.get("_pdf_spec")
        if spec is None:
            return None
        from documents.pdf import extract_pdf_tables

        from audit.pdf_tables import converted_cell_sources, graphic_inside

        if "_cell_sources" not in self.__dict__:
            # The same layout extracts the same grids the converted workbook was written from.
            extraction = extract_pdf_tables(Path(path).read_bytes(), spec=spec)
            grids = [group.table_grid for group in extraction.groups if group.success and group.table_grid is not None]
            shapes = [(len(grid.rows) - 1, len(grid.cols) - 1) for grid in grids]
            written = [(sheet_.max_row, sheet_.max_column) for sheet_ in self._workbook_cache.worksheets]
            self._cell_sources = converted_cell_sources(extraction) if all(r <= rows and c <= cols for (r, c), (rows, cols) in zip(written, shapes)) and len(shapes) == len(written) else {}
            self._cell_tolerance = extraction.spec.extraction.line_tolerance
        merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, row, column)
        anchor = (merged.min_row, merged.min_col) if merged else (row, column)
        found = self._cell_sources.get((self._workbook_cache.sheetnames.index(sheet.title), *anchor))
        if found is None:
            return None
        page, bbox = found
        return graphic_inside(path, page, bbox, self._cell_tolerance)

    def uncomputed(self, sheet: Sheet, row: int, column: int) -> bool:
        """A formula whose result the original does not store reads blank, yet its value is unknown."""
        path = str(self.audit_file.file_url)
        if not path.lower().endswith((".xlsx", ".xlsm")) or sheet.ws.parent is not self._workbook_cache:
            return False
        if "_formulas" not in self.__dict__:
            from audit.checker_lib.excel import load_workbook

            self._formulas = load_workbook(path, formulas=True)
        value = self._formulas[sheet.title].cell(row, column).value
        return isinstance(value, str) and value.startswith("=")

    def unread_pages(self) -> list[int]:
        """Scanned pages of a PDF original, which are not read: no text layer, or one picture covering the page.

        Words typed over a scan do not make the scanned content readable.
        """
        if not str(self.audit_file.file_url).lower().endswith(".pdf"):
            return []
        import fitz

        with fitz.open(str(self.audit_file.file_url)) as document:
            return [page.number + 1 for page in document if not page.get_text().strip() or any(abs(fitz.Rect(image["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for image in page.get_image_info())]

    @staticmethod
    def extent(sheet: Sheet) -> tuple[int, int]:
        """The rows and columns to scan: the used range, capped where a sheet's formatting runs to infinity."""
        from .resources import WHOLE_SHEET_COLS, WHOLE_SHEET_ROWS

        return min(sheet.ws.max_row, WHOLE_SHEET_ROWS), min(sheet.ws.max_column, WHOLE_SHEET_COLS)

    @staticmethod
    def labelled(sheet: Sheet, aliases: tuple[str, ...]) -> list[str]:
        """Values written right of each label cell, or after "label:" inside the same cell, anywhere on the sheet."""
        wanted = {compact(alias) for alias in aliases}
        values = []
        rows, columns = UaesExcelAgent.extent(sheet)
        for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns):
            raw = unicodedata.normalize("NFKC", str(cell.value()))
            if compact(raw) in wanted:
                found = cell.find_right(max_offset=8)
                if found is not None:
                    values.append(str(found.value()).strip())
                continue
            match = re.match(r"\s*(.+?)\s*[:：]\s*(.+)", raw, re.S)
            if match and compact(match.group(1)) in wanted:
                values.append(match.group(2).strip())
        return values


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)
