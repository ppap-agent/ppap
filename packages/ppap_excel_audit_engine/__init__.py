"""PPAP Excel audit engine."""

from .engine import run_audit
from .routing import (
    DOCX_AGENTS,
    DOCX_EXTENSIONS,
    EXCEL_AGENTS,
    EXCEL_EXTENSIONS,
    PDF_AGENTS,
    PDF_EXTENSIONS,
    get_document_type_from_rule,
    iter_supported_doc_types,
    resolve_engine_agent_class,
)
from .runtime import AuditRuntime, get_runtime

__all__ = [
    "run_audit",
    "get_document_type_from_rule",
    "iter_supported_doc_types",
    "resolve_engine_agent_class",
    "EXCEL_AGENTS",
    "PDF_AGENTS",
    "EXCEL_EXTENSIONS",
    "PDF_EXTENSIONS",
    "DOCX_AGENTS",
    "DOCX_EXTENSIONS",
    "AuditRuntime",
    "get_runtime",
]
