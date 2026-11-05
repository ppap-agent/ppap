"""Public service API for PDF table extraction and conversion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..table.models import TableGrid
from ._assembly import assemble_group_pages
from ._grouping import group_pages
from ._region import resolve_table_region
from ._stitch import stitch_pages_with_outcome
from ._writer import write_workbook
from .errors import PdfConversionError
from .result import ConversionResult, ExtractionResult, GroupExtraction, PageExtraction, PdfSource
from .spec import DEFAULT_SPEC, FailureMode, PdfSpec, SheetNameMode, validate_pdf_spec

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._linegrid_engine import LinegridEngine


def extract_pdf_tables(source: PdfSource, *, spec: PdfSpec | None = None) -> ExtractionResult:
    resolved_spec = spec or DEFAULT_SPEC

    try:
        validate_pdf_spec(resolved_spec)
    except ValueError as exc:
        raise PdfConversionError("invalid_spec", str(exc), {"phase": "validate_spec"}) from exc

    try:
        from ._linegrid_engine import LinegridEngine

        file_content = _read_source_bytes(source)
        page_count = _count_pdf_pages(file_content)

        engine = LinegridEngine()
        engine.configure(resolved_spec.extraction)

        try:
            page_groups = group_pages(
                file_content,
                page_count=page_count,
                grouping=resolved_spec.grouping,
                pdf_extractor=engine.pdf_extractor,
            )
        except ValueError as exc:
            raise PdfConversionError(
                "grouping_failed",
                str(exc),
                {
                    "phase": "group_pages",
                    "page_count": page_count,
                },
            ) from exc

        group_results: list[GroupExtraction] = []
        for group_index, resolved_group in enumerate(page_groups):
            group_results.extend(
                _run_group(
                    file_content=file_content,
                    group_index=group_index,
                    pages=resolved_group.pages,
                    group_label=resolved_group.label,
                    group_diagnostics=resolved_group.diagnostics,
                    spec=resolved_spec,
                    engine=engine,
                )
            )
        group_results = _reindex_groups(group_results, resolved_spec)

        if not group_results:
            raise PdfConversionError(
                "no_groups",
                "No page groups resolved for this PDF",
                {"page_count": page_count},
            )

        if resolved_spec.failure_mode == FailureMode.STRICT:
            failed = [group for group in group_results if not group.success]
            if failed:
                raise PdfConversionError(
                    "strict_mode_failed",
                    "Strict mode failed: at least one group extraction failed",
                    {
                        "page_count": page_count,
                        "failed_groups": [group.group_index for group in failed],
                    },
                )

        if not any(group.success for group in group_results):
            raise PdfConversionError(
                "no_successful_groups",
                "No successful table groups extracted",
                {"page_count": page_count},
            )

        diagnostics = _build_diagnostics(page_count=page_count, groups=group_results)
        return ExtractionResult(spec=resolved_spec, groups=group_results, diagnostics=diagnostics)

    except PdfConversionError:
        raise
    except Exception as exc:
        raise PdfConversionError(
            "extract_failed",
            f"PDF extraction failed: {exc}",
            {"exception_type": type(exc).__name__},
        ) from exc


def convert_pdf_to_excel(
    source: PdfSource,
    output_path: str,
    *,
    spec: PdfSpec | None = None,
) -> ConversionResult:
    extraction = extract_pdf_tables(source, spec=spec)

    try:
        output_spec = (spec or DEFAULT_SPEC).output
        output_saved = write_workbook(
            extraction.groups,
            output_path,
            output_spec,
            source=source,
        )
        if not output_saved:
            raise PdfConversionError(
                "write_failed",
                "No successful group available for workbook rendering",
                extraction.diagnostics,
            )

        diagnostics = {
            **extraction.diagnostics,
            "output_path": output_path,
            "output_saved": True,
        }
        return ConversionResult(
            output_path=output_path,
            extraction=extraction,
            diagnostics=diagnostics,
        )
    except PdfConversionError:
        raise
    except Exception as exc:
        raise PdfConversionError(
            "write_failed",
            f"Workbook writing failed: {exc}",
            extraction.diagnostics,
        ) from exc


def _run_group(
    *,
    file_content: bytes,
    group_index: int,
    pages: tuple[int, ...],
    group_label: str | None,
    group_diagnostics: tuple[str, ...],
    spec: PdfSpec,
    engine: LinegridEngine,
) -> list[GroupExtraction]:
    page_results: list[PageExtraction] = []
    page_heights = _resolve_page_heights(file_content, pages)

    for page in pages:
        table_region = None
        try:
            table_region = resolve_table_region(
                file_content,
                page_index=page,
                region=spec.region,
                pdf_extractor=engine.pdf_extractor,
            )
            extraction = engine.extract_page(
                file_content,
                page_index=page,
                table_region=table_region,
                color_filter=spec.extraction.color_filter,
            )

            page_results.append(
                PageExtraction(
                    page_index=page,
                    success=extraction.success,
                    table_grid=extraction.table_grid,
                    span_boxes=extraction.span_boxes,
                    line_elements=extraction.line_elements,
                    intersections_count=extraction.intersections_count,
                    cells_count=extraction.cells_count,
                    page_height=page_heights.get(page),
                    outer_text_lines=extraction.outer_text_lines,
                    table_region=table_region,
                    error_message=extraction.error_message,
                )
            )
        except Exception as exc:
            logger.warning("page extraction failed: page=%s error=%s", page, exc)
            page_results.append(
                PageExtraction(
                    page_index=page,
                    success=False,
                    table_grid=TableGrid(rows=[], cols=[], cells=[]),
                    span_boxes=[],
                    line_elements=[],
                    intersections_count=0,
                    cells_count=0,
                    page_height=page_heights.get(page),
                    outer_text_lines=[],
                    table_region=table_region,
                    error_message=str(exc),
                )
            )

    assembled_page_results, assembly_diagnostics = assemble_group_pages(page_results, group_label=group_label)
    base_group_diagnostics = list((*group_diagnostics, *assembly_diagnostics))

    if spec.failure_mode == FailureMode.STRICT and any(not page_result.success for page_result in assembled_page_results):
        return [
            GroupExtraction(
                group_index=group_index,
                pages=pages,
                strategy=spec.stitch.mode,
                success=False,
                page_results=assembled_page_results,
                table_grid=None,
                cells_count=0,
                intersections_count=sum(result.intersections_count for result in assembled_page_results),
                error_message="strict mode: at least one page extraction failed",
                sheet_name=_group_sheet_name(spec, group_index, pages, group_label),
                group_label=group_label,
                group_diagnostics=tuple(base_group_diagnostics),
            )
        ]

    successful_page_results = [page_result for page_result in assembled_page_results if page_result.success and page_result.table_grid.rows and page_result.table_grid.cols]
    if not successful_page_results:
        return [
            GroupExtraction(
                group_index=group_index,
                pages=pages,
                strategy=spec.stitch.mode,
                success=False,
                page_results=assembled_page_results,
                table_grid=None,
                cells_count=0,
                intersections_count=sum(result.intersections_count for result in assembled_page_results),
                error_message="no successful page grids to stitch",
                sheet_name=_group_sheet_name(spec, group_index, pages, group_label),
                group_label=group_label,
                group_diagnostics=tuple(base_group_diagnostics),
            )
        ]

    failed_pages = [page_result.page_index + 1 for page_result in assembled_page_results if not (page_result.success and page_result.table_grid.rows and page_result.table_grid.cols)]

    try:
        split_groups: list[GroupExtraction] = []
        position_by_page = {page_result.page_index: idx for idx, page_result in enumerate(assembled_page_results)}
        last_consumed_position = -1
        cursor = 0
        split_index = 0
        while cursor < len(successful_page_results):
            chunk = successful_page_results[cursor:]
            outcome = stitch_pages_with_outcome(chunk, spec.stitch)
            consumed_page_count = max(1, outcome.consumed_page_count)
            consumed_chunk = chunk[:consumed_page_count]
            consumed_pages = tuple(page_result.page_index for page_result in consumed_chunk)
            end_position = position_by_page[consumed_chunk[-1].page_index]
            chunk_page_results = assembled_page_results[last_consumed_position + 1 : end_position + 1]
            if not chunk_page_results:
                chunk_page_results = consumed_chunk
            chunk_diagnostics = list(base_group_diagnostics)
            if failed_pages and split_index == 0:
                chunk_diagnostics.append(f"best_effort pages without usable grids: {failed_pages}")
            if outcome.stop_reason is not None:
                stopped_page = chunk[consumed_page_count].page_index + 1 if consumed_page_count < len(chunk) else None
                if stopped_page is not None:
                    if outcome.stop_reason == "large_y_gap":
                        gap = outcome.large_y_gap if outcome.large_y_gap is not None else float("nan")
                        threshold = outcome.large_y_gap_threshold if outcome.large_y_gap_threshold is not None else float("nan")
                        chunk_diagnostics.append(f"stitch split before page={stopped_page} due to large_y_gap(gap={gap:.2f}, threshold={threshold:.2f})")
                    else:
                        chunk_diagnostics.append(f"stitch split before page={stopped_page} due to remap overlap")
            success = bool(outcome.table_grid.rows and outcome.table_grid.cols and outcome.table_grid.cells)
            split_groups.append(
                GroupExtraction(
                    group_index=group_index + split_index,
                    pages=consumed_pages,
                    strategy=spec.stitch.mode,
                    success=success,
                    page_results=chunk_page_results,
                    table_grid=outcome.table_grid if success else None,
                    cells_count=len(outcome.table_grid.cells) if success else 0,
                    intersections_count=sum(result.intersections_count for result in chunk_page_results),
                    error_message=None if success else "stitched grid is empty",
                    sheet_name=_group_sheet_name(spec, group_index + split_index, consumed_pages, group_label),
                    group_label=group_label,
                    group_diagnostics=tuple(chunk_diagnostics),
                )
            )
            cursor += consumed_page_count
            split_index += 1
            last_consumed_position = end_position

        trailing_page_results = assembled_page_results[last_consumed_position + 1 :]
        if split_groups and trailing_page_results:
            trailing_pages = [page_result.page_index + 1 for page_result in trailing_page_results]
            last_group = split_groups[-1]
            extended_page_results = [*last_group.page_results, *trailing_page_results]
            extended_diagnostics = [
                *last_group.group_diagnostics,
                f"best_effort retained trailing pages without usable grids: {trailing_pages}",
            ]
            split_groups[-1] = GroupExtraction(
                group_index=last_group.group_index,
                pages=last_group.pages,
                strategy=last_group.strategy,
                success=last_group.success,
                page_results=extended_page_results,
                table_grid=last_group.table_grid,
                cells_count=last_group.cells_count,
                intersections_count=sum(result.intersections_count for result in extended_page_results),
                error_message=last_group.error_message,
                sheet_name=last_group.sheet_name,
                group_label=last_group.group_label,
                group_diagnostics=tuple(extended_diagnostics),
            )
        return split_groups
    except Exception as exc:
        return [
            GroupExtraction(
                group_index=group_index,
                pages=pages,
                strategy=spec.stitch.mode,
                success=False,
                page_results=assembled_page_results,
                table_grid=None,
                cells_count=0,
                intersections_count=sum(result.intersections_count for result in assembled_page_results),
                error_message=str(exc),
                sheet_name=_group_sheet_name(spec, group_index, pages, group_label),
                group_label=group_label,
                group_diagnostics=tuple(base_group_diagnostics),
            )
        ]


def _read_source_bytes(source: PdfSource) -> bytes:
    if isinstance(source, bytes):
        return source
    with open(source, "rb") as f:
        return f.read()


def _resolve_page_heights(file_content: bytes, pages: tuple[int, ...]) -> dict[int, float]:
    if not pages:
        return {}

    page_heights: dict[int, float] = {}
    try:
        import fitz

        with fitz.open(stream=file_content, filetype="pdf") as doc:
            for page_index in pages:
                if 0 <= page_index < len(doc):
                    page_heights[page_index] = float(doc[page_index].rect.height)
    except Exception as exc:
        logger.warning("unable to resolve page heights for stitch y-gap split: %s", exc)
    return page_heights


def _count_pdf_pages(file_content: bytes) -> int:
    try:
        import fitz

        doc = fitz.open(stream=file_content, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception as exc:
        raise PdfConversionError(
            "invalid_pdf",
            f"Unable to read PDF pages: {exc}",
            {"phase": "count_pages"},
        ) from exc


def _group_sheet_name(spec: PdfSpec, group_index: int, pages: tuple[int, ...], group_label: str | None) -> str:
    if spec.output.sheet_name_mode == SheetNameMode.GROUP_LABEL and group_label:
        return group_label

    prefix = spec.output.sheet_name_prefix
    if not pages:
        return f"{prefix}_{group_index + 1}"
    return f"{prefix}_{group_index + 1}_p{pages[0] + 1}-{pages[-1] + 1}"


def _reindex_groups(groups: list[GroupExtraction], spec: PdfSpec) -> list[GroupExtraction]:
    reindexed: list[GroupExtraction] = []
    for new_index, group in enumerate(groups):
        reindexed.append(
            GroupExtraction(
                group_index=new_index,
                pages=group.pages,
                strategy=group.strategy,
                success=group.success,
                page_results=group.page_results,
                table_grid=group.table_grid,
                cells_count=group.cells_count,
                intersections_count=group.intersections_count,
                error_message=group.error_message,
                sheet_name=_group_sheet_name(spec, new_index, group.pages, group.group_label),
                group_label=group.group_label,
                group_diagnostics=group.group_diagnostics,
            )
        )
    return reindexed


def _build_diagnostics(page_count: int, groups: list[GroupExtraction]) -> dict[str, object]:
    unique_page_results: dict[int, PageExtraction] = {}
    for group in groups:
        for page_result in group.page_results:
            unique_page_results.setdefault(page_result.page_index, page_result)
    page_results = [unique_page_results[index] for index in sorted(unique_page_results)]
    return {
        "page_count": page_count,
        "group_count": len(groups),
        "group_success_count": sum(1 for group in groups if group.success),
        "group_labels": [group.group_label for group in groups],
        "page_result_count": len(page_results),
        "page_success_count": sum(1 for page in page_results if page.success),
        "outer_text_line_count": sum(len(page.outer_text_lines) for page in page_results),
        "page_outer_text_line_counts": [len(page.outer_text_lines) for page in page_results],
        "strategy_modes": [group.strategy.value for group in groups],
        "group_errors": [group.error_message for group in groups if group.error_message],
        "group_diagnostics": [diag for group in groups for diag in group.group_diagnostics],
        "original_pages": [
            {
                "page": page.page_index + 1,
                "success": page.success,
                "region": None if page.table_region is None else [None if value is None else round(float(value), 2) for value in page.table_region],
                "error": page.error_message,
            }
            for page in page_results
        ],
    }
