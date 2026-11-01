"""Structured DOCX table adapter shared by deterministic audit agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from documents.files import download_file_sync
from documents.word import (
    WordDocument,
    WordImageEvidence,
    WordTableBlock,
    extract_docx_structure,
    resolve_docx_image_evidence,
    word_document_to_workbook,
)
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from audit.engine.rule_engine import GuardStop, RuleContext
from audit.engine.rule_engine.contracts import Decision, RuleResource
from audit.engine.rule_engine.excel_ops import DefaultExcelOps

_MAX_DOCX_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DocxPrimaryTableContract:
    """Shared recognition contract for one DOCX business form."""

    title: str
    anchors: tuple[str, ...]
    minimum_anchor_hits: int = 2
    placeholder_markers: tuple[str, ...] = ()


EQUIPMENT_DOCX_PRIMARY_TABLE = DocxPrimaryTableContract(
    title="设备验收清单",
    anchors=("产线/工序", "设备开发", "设备验收", "设备", "验收结果"),
)
PSW_DOCX_PRIMARY_TABLE = DocxPrimaryTableContract(
    title="PSW主表",
    anchors=("零件提交保证书", "重量", "供应商名称/供应商代码", "提交原因", "认可情况"),
    placeholder_markers=("xxxx", "填写禁用物质报告编号", "sample", "示例"),
)


def _normalize_text(value: object) -> str:
    return "".join(str(value).split()).lower()


def _sheet_text(sheet: Worksheet) -> str:
    return _normalize_text("\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None))


def _primary_table_score(
    workbook: Workbook,
    sheet: Worksheet,
    contract: DocxPrimaryTableContract,
    resource: RuleResource | None,
) -> tuple[int, int, int, int]:
    resolved_columns: dict[str, int] = {}
    data_cells = 0
    if resource is not None:
        original_title = sheet.title
        sheet.title = contract.title
        try:
            context = RuleContext(ops=None, workbook=workbook, resource=resource)
            for section_spec in resource.sections.values():
                try:
                    section = context.section(sheet, section_spec.canonical_name)
                except GuardStop as stop:
                    if stop.status == "ERR":
                        raise
                    continue
                for column in resource.columns.values():
                    if column.section_name != section_spec.canonical_name:
                        continue
                    resolved = context.optional_column(section, column.canonical_name)
                    if resolved is not None:
                        resolved_columns[column.canonical_name] = resolved.data_start_row

            data_start_row = min(resolved_columns.values(), default=sheet.max_row + 1)
            data_cells = sum(context.ops.is_filled(cell.value) for row in sheet.iter_rows(min_row=data_start_row) for cell in row)
        finally:
            sheet.title = original_title

    text = _sheet_text(sheet)
    anchors = tuple(_normalize_text(anchor) for anchor in contract.anchors)
    anchor_hits = sum(anchor in text for anchor in anchors)
    placeholder_hits = sum(text.count(_normalize_text(marker)) for marker in contract.placeholder_markers)
    return len(resolved_columns), data_cells, anchor_hits, -placeholder_hits


def label_docx_primary_table(
    workbook: Workbook,
    contract: DocxPrimaryTableContract,
    resource: RuleResource | None,
) -> Worksheet:
    """Label and return the sole primary table accepted by the business rules."""
    if not workbook.worksheets:
        raise GuardStop(Decision.NG, "DOCX 未包含可审核表格。")

    scored_sheets = [(_primary_table_score(workbook, sheet, contract, resource), sheet) for sheet in workbook.worksheets]
    best_score = max(score for score, _sheet in scored_sheets)
    if best_score[2] < contract.minimum_anchor_hits:
        raise GuardStop(Decision.NG, f"DOCX 未找到满足 {contract.title} 结构契约的主表。")
    best_sheets = [sheet for score, sheet in scored_sheets if score == best_score]
    if len(best_sheets) != 1:
        titles = ", ".join(sheet.title for sheet in best_sheets)
        raise GuardStop(Decision.NG, f"DOCX 主表候选存在歧义: {titles}")
    best_sheets[0].title = contract.title
    return best_sheets[0]


class WordEvidenceExcelOps(DefaultExcelOps):
    """Adapt source-located Word evidence to the existing rule interface."""

    def __init__(
        self,
        document: WordDocument,
        workbook: Workbook,
        images: tuple[WordImageEvidence, ...],
    ) -> None:
        super().__init__()
        tables = [block for block in document.blocks if isinstance(block, WordTableBlock)]
        self._table_by_sheet = {id(sheet): table for sheet, table in zip(workbook.worksheets, tables, strict=False)}
        self._document = document
        self._images = images

    def _cell_position(self, ws: Worksheet, source_path: str) -> tuple[int, int] | None:
        table = self._table_by_sheet.get(id(ws))
        if table is None:
            return None
        matching = [cell for cell in table.cells if cell.origin and source_path.startswith(cell.source.path)]
        if not matching:
            return None
        cell = max(matching, key=lambda item: len(item.source.path))
        return cell.row + 1, cell.col + 1

    def get_checkbox_controls(
        self,
        ws: Worksheet,
        source_path: str | None = None,
    ) -> list[tuple[int, int, bool]]:
        del source_path
        controls: list[tuple[int, int, bool]] = []
        for evidence in self._document.visual_evidence:
            if evidence.kind not in {"checkbox_character", "content_control"} or evidence.checked is None:
                continue
            position = self._cell_position(ws, evidence.source.path)
            if position is not None:
                controls.append((*position, evidence.checked))
        return sorted(controls, key=lambda item: (item[0], item[1], not item[2]))

    def get_sheet_images_base64(self, ws: Worksheet) -> list[str]:
        return [image.data_url for image in self.image_evidence(ws)]

    def image_evidence(self, ws: Worksheet) -> tuple[WordImageEvidence, ...]:
        """Return source-located images that belong to one Word table view."""
        return tuple(image for image in self._images if self._cell_position(ws, image.evidence.source.path) is not None)

    def all_image_evidence(self) -> tuple[WordImageEvidence, ...]:
        """Return source-located images from the complete Word document."""
        return self._images


class StructuredDocxWorkbookMixin:
    """Expose authoritative DOCX grids as an in-memory workbook view."""

    DOCX_PRIMARY_TABLE_CONTRACT: DocxPrimaryTableContract | None = None
    DOCX_NAMED_TABLE_ANCHORS: tuple[tuple[str, tuple[str, ...], int], ...] = ()

    _workbook_cache: Optional[Workbook]
    _docx_load_error: Exception | None = None
    _docx_bytes_cache: bytes | None = None
    _word_ops_cache: WordEvidenceExcelOps | None = None
    _word_images_cache: tuple[WordImageEvidence, ...] = ()
    _rule_resource: RuleResource | None
    word_document: WordDocument

    def _read_docx_bytes(self) -> bytes:
        if self._docx_bytes_cache is not None:
            return self._docx_bytes_cache
        location = str(getattr(getattr(self, "audit_file", None), "file_url", "") or "")
        parsed = urlparse(location)
        if parsed.scheme in {"http", "https"}:
            content, _ = download_file_sync(location, max_file_size=_MAX_DOCX_BYTES)
        else:
            if parsed.scheme not in {"", "file"}:
                raise OSError(f"不支持的 DOCX 来源协议: {parsed.scheme}")
            raw_path = parsed.path if parsed.scheme == "file" else location
            content = Path(unquote(raw_path)).read_bytes()
        self._docx_bytes_cache = content
        return content

    def _label_primary_tables(self, workbook: Workbook) -> None:
        contract = self.DOCX_PRIMARY_TABLE_CONTRACT
        if contract is None:
            return
        label_docx_primary_table(workbook, contract, self._rule_resource)

    def _label_named_tables(self, workbook: Workbook) -> None:
        for title, raw_anchors, minimum_hits in self.DOCX_NAMED_TABLE_ANCHORS:
            anchors = tuple(_normalize_text(anchor) for anchor in raw_anchors)
            primary_title = self.DOCX_PRIMARY_TABLE_CONTRACT.title if self.DOCX_PRIMARY_TABLE_CONTRACT else ""
            scored = [(sum(anchor in _sheet_text(sheet) for anchor in anchors), sheet) for sheet in workbook.worksheets if sheet.title != primary_title]
            if not scored:
                continue
            best_score = max(score for score, _sheet in scored)
            if best_score < minimum_hits:
                continue
            candidates = [sheet for score, sheet in scored if score == best_score]
            if len(candidates) != 1:
                names = ", ".join(sheet.title for sheet in candidates)
                raise GuardStop(Decision.NG, f"DOCX 表格[{title}]候选存在歧义: {names}")
            candidates[0].title = title

    def rule_ctx(self, workbook: Workbook) -> RuleContext:
        resource = self._rule_resource
        if resource is None:
            raise RuntimeError("DOCX 文档类型缺少代码资源定义")
        if self._word_ops_cache is None:
            self._word_ops_cache = WordEvidenceExcelOps(
                self.word_document,
                workbook,
                self._word_images_cache,
            )
        return RuleContext(
            ops=self._word_ops_cache,
            workbook=workbook,
            resource=resource,
            source_path=None,
        )

    def _load_workbook(self) -> Workbook:
        if self._workbook_cache is not None:
            return self._workbook_cache

        if self._docx_load_error is not None:
            raise self._docx_load_error

        try:
            content = self._read_docx_bytes()
            self.word_document = extract_docx_structure(content)
            self._word_images_cache = resolve_docx_image_evidence(content, self.word_document)
            workbook = word_document_to_workbook(self.word_document)
            self._label_primary_tables(workbook)
            self._label_named_tables(workbook)
        except Exception as exc:
            self._docx_load_error = exc
            raise

        self._workbook_cache = workbook
        return workbook


__all__ = [
    "DocxPrimaryTableContract",
    "EQUIPMENT_DOCX_PRIMARY_TABLE",
    "PSW_DOCX_PRIMARY_TABLE",
    "StructuredDocxWorkbookMixin",
    "label_docx_primary_table",
]
