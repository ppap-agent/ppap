"""Shared readers for directory checkers.

Import a function directly. This package is not a registry, a plugin list,
or a checker version.
"""

from audit.checker_lib.evidence import content_sha256, json_locator, structure_evidence, version_id
from audit.checker_lib.excel import column_letter, load_workbook
from audit.checker_lib.tables import cell_span, find_headers, is_blank, label_matches
from audit.checker_lib.visual import observe_excel_region

__all__ = [
    "cell_span",
    "column_letter",
    "content_sha256",
    "find_headers",
    "is_blank",
    "json_locator",
    "label_matches",
    "load_workbook",
    "observe_excel_region",
    "structure_evidence",
    "version_id",
]
