"""Document assembly boundary for future non-table enrichment."""

from __future__ import annotations

from .result import PageExtraction


def assemble_group_pages(page_results: list[PageExtraction], *, group_label: str | None) -> tuple[list[PageExtraction], tuple[str, ...]]:
    _ = group_label
    return page_results, ("assembly_noop",)
