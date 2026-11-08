"""Shared readers for directory checkers.

Import a function directly. This package is not a registry, a plugin list,
or a checker version.
"""

from audit.checker_lib.evidence import content_sha256, json_locator, structure_diagnostic, structure_evidence, version_id
from audit.checker_lib.excel import column_letter, load_workbook
from audit.checker_lib.pdf import convert_pdf_to_excel, read_tables
from audit.checker_lib.tables import cell_span, columns_have_merge, find_headers, is_blank, label_matches, sheet_has_value, window_has_merge
from audit.checker_lib.visual import observe_excel_region

__all__ = [
    "cell_span",
    "column_letter",
    "columns_have_merge",
    "content_sha256",
    "convert_pdf_to_excel",
    "find_headers",
    "is_blank",
    "json_locator",
    "label_matches",
    "load_workbook",
    "observe_excel_region",
    "read_tables",
    "sheet_has_value",
    "structure_diagnostic",
    "structure_evidence",
    "version_id",
    "window_has_merge",
]
