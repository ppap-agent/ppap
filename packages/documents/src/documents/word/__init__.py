"""Public Word structured-output API."""

from .legacy import (
    LegacyDocConversionError,
    LegacyDocConversionLimits,
    convert_legacy_doc_to_docx,
    is_legacy_word_doc,
)
from .local_broker import (
    check_legacy_doc_broker,
    convert_legacy_doc_for_runtime,
    convert_legacy_doc_via_local_broker,
    serve_legacy_doc_conversion,
)
from .models import (
    WordBlock,
    WordDiagnostic,
    WordDocument,
    WordImageEvidence,
    WordParagraphBlock,
    WordSource,
    WordTableBlock,
    WordTableCell,
    WordVisualEvidence,
)
from .parser import extract_docx_structure, resolve_docx_image_evidence
from .workbook import WordWorkbookAdapterError, word_document_to_workbook

__all__ = [
    "LegacyDocConversionError",
    "LegacyDocConversionLimits",
    "WordBlock",
    "WordDiagnostic",
    "WordDocument",
    "WordImageEvidence",
    "WordParagraphBlock",
    "WordSource",
    "WordTableBlock",
    "WordTableCell",
    "WordVisualEvidence",
    "WordWorkbookAdapterError",
    "check_legacy_doc_broker",
    "convert_legacy_doc_to_docx",
    "convert_legacy_doc_for_runtime",
    "convert_legacy_doc_via_local_broker",
    "extract_docx_structure",
    "is_legacy_word_doc",
    "resolve_docx_image_evidence",
    "serve_legacy_doc_conversion",
    "word_document_to_workbook",
]
