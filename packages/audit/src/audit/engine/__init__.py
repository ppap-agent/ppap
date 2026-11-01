"""PPAP Excel audit engine."""

from __future__ import annotations

from typing import Any

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


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    if name == "run_audit":
        from .engine import run_audit

        return run_audit
    if name in {"AuditRuntime", "get_runtime"}:
        from .runtime import AuditRuntime, get_runtime

        return {"AuditRuntime": AuditRuntime, "get_runtime": get_runtime}[name]
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

    values = {
        "DOCX_AGENTS": DOCX_AGENTS,
        "DOCX_EXTENSIONS": DOCX_EXTENSIONS,
        "EXCEL_AGENTS": EXCEL_AGENTS,
        "EXCEL_EXTENSIONS": EXCEL_EXTENSIONS,
        "PDF_AGENTS": PDF_AGENTS,
        "PDF_EXTENSIONS": PDF_EXTENSIONS,
        "get_document_type_from_rule": get_document_type_from_rule,
        "iter_supported_doc_types": iter_supported_doc_types,
        "resolve_engine_agent_class": resolve_engine_agent_class,
    }
    return values[name]
