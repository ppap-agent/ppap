"""Internal adapter around the existing linegrid geometry backend."""

from __future__ import annotations

from ..extractors.pdf import PDFExtractor
from .linegrid.extractor import PDFTableExtractor, TableExtractionResult
from .result import TableRegion
from .spec import ExtractionSpec


class LinegridEngine:
    def __init__(self, extractor: PDFTableExtractor | None = None) -> None:
        self._extractor = extractor or PDFTableExtractor()

    @property
    def pdf_extractor(self) -> PDFExtractor:
        return self._extractor.pdf_extractor

    def configure(self, extraction: ExtractionSpec) -> None:
        self._extractor.set_tolerances(
            line_tolerance=extraction.line_tolerance,
            merge_tolerance=extraction.merge_tolerance,
            text_tolerance=extraction.text_tolerance,
            cross_cell_text_split=extraction.cross_cell_text_split,
            text_cell_assign=extraction.text_cell_assign,
        )

    def extract_page(
        self,
        file_content: bytes,
        *,
        page_index: int,
        table_region: TableRegion | None,
        color_filter: dict[str, object] | None,
    ) -> TableExtractionResult:
        return self._extractor.extract_table_from_pdf(
            file_content,
            page_range=(page_index, page_index),
            table_region=table_region,
            color_filter=color_filter,
        )
