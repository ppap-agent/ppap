"""Stable per-rule PSW evidence provenance for every supported file format."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal, cast
from urllib.parse import unquote, urlparse

from audit.contracts import (
    AuditEvidence,
    AuditInput,
    AuditResultItem,
    EvidenceDiagnostic,
    EvidenceSource,
)

EvidenceUse = Literal["none", "structure", "visual", "both"]
SourceFormat = Literal["xlsx", "xls", "xlsm", "pdf", "docx", "doc", "png", "jpg", "jpeg", "webp"]
PreparedFormat = Literal["xlsx", "xls", "xlsm", "pdf", "docx", "png"]

DIAGNOSTIC_DOC_CONVERSION_FAILED = "psw.doc.conversion_failed"
DIAGNOSTIC_PDF_CONVERSION_FAILED = "psw.pdf.conversion_failed"
DIAGNOSTIC_STRUCTURE_INSUFFICIENT = "psw.structure.insufficient"
DIAGNOSTIC_VISUAL_MISSING = "psw.visual.missing"
DIAGNOSTIC_VISUAL_UNVERIFIABLE = "psw.visual.unverifiable"
DIAGNOSTIC_VISUAL_RUNTIME_ERROR = "psw.visual.runtime_error"
DIAGNOSTIC_EVALUATION_RUNTIME_ERROR = "psw.evaluation.runtime_error"
DIAGNOSTIC_EVALUATION_RULE_UNAVAILABLE = "psw.evaluation.rule_unavailable"


def required_psw_evidence(rule_no: int) -> Literal["structure", "visual", "structure_or_visual"]:
    """Return the format-neutral evidence requirement for one PSW rule."""
    if rule_no in {3, 5}:
        return "visual"
    if rule_no in {7, 8, 9, 10}:
        return "structure_or_visual"
    return "structure"


def _suffix(location: str) -> str:
    parsed = urlparse(location)
    return Path(unquote(parsed.path or location)).suffix.lower().lstrip(".")


def _source_preparation(
    audit: AuditInput,
    used: EvidenceUse,
    attempted: EvidenceUse,
) -> tuple[SourceFormat, PreparedFormat, tuple[str, ...], tuple[EvidenceSource, ...]]:
    metadata = audit.file_metadata or {}
    legacy = metadata.get("legacy_doc_conversion")
    source_pdf = str(metadata.get("source_pdf_url", "") or "")
    current_format = _suffix(audit.file_url)

    if isinstance(legacy, dict):
        legacy_strategies = ("local_libreoffice_doc_to_docx", "docx_ooxml")
        sources = _word_sources(used)
        return "doc", "docx", legacy_strategies, sources
    if source_pdf:
        conversion_strategy = str(metadata.get("pdf_conversion_strategy", "") or "local_pdf_to_xlsx")
        if attempted == "both":
            pdf_strategies: tuple[str, ...] = (
                conversion_strategy,
                "local_pdf_page_render",
            )
        elif attempted in {"none", "structure"}:
            pdf_strategies = (conversion_strategy,)
        else:
            pdf_strategies = ("local_pdf_page_render",)
        sources = _pdf_sources(used)
        prepared_format: PreparedFormat = "xlsx" if attempted in {"none", "structure", "both"} else "pdf"
        return "pdf", prepared_format, pdf_strategies, sources
    if current_format == "pdf":
        strategies: tuple[str, ...]
        if attempted == "both":
            strategies = ("local_pdf_to_xlsx", "local_pdf_page_render")
        elif attempted in {"none", "structure"}:
            strategies = ("local_pdf_to_xlsx",)
        else:
            strategies = ("local_pdf_page_render",)
        prepared: PreparedFormat = "xlsx" if attempted in {"none", "structure", "both"} else "pdf"
        return "pdf", prepared, strategies, _pdf_sources(used)
    if current_format in {"png", "jpg", "jpeg", "webp"}:
        carrier = cast(Literal["png", "jpg", "jpeg", "webp"], current_format)
        sources = (EvidenceSource(kind="visual", locator="image://original"),) if used in {"visual", "both"} else ()
        return carrier, "png", ("original_image_decode",), sources
    if current_format == "docx":
        return "docx", "docx", ("docx_ooxml",), _word_sources(used)
    if current_format == "doc":
        return "doc", "docx", ("local_libreoffice_doc_to_docx",), ()
    if current_format in {"xls", "xlsm", "xlsx"}:
        spreadsheet_format = cast(Literal["xls", "xlsm", "xlsx"], current_format)
        return spreadsheet_format, spreadsheet_format, ("native_excel",), _excel_sources(used)
    raise ValueError(f"unsupported PSW evidence format: {current_format or 'unknown'}")


def _excel_sources(used: EvidenceUse) -> tuple[EvidenceSource, ...]:
    sources: list[EvidenceSource] = []
    if used in {"structure", "both"}:
        sources.append(EvidenceSource(kind="structure", locator="workbook://structured-cells"))
    if used in {"visual", "both"}:
        sources.append(EvidenceSource(kind="visual", locator="workbook://embedded-images"))
    return tuple(sources)


def _pdf_sources(used: EvidenceUse) -> tuple[EvidenceSource, ...]:
    sources: list[EvidenceSource] = []
    if used in {"structure", "both"}:
        sources.append(EvidenceSource(kind="structure", locator="pdf://converted-table-grid"))
    if used in {"visual", "both"}:
        sources.append(EvidenceSource(kind="visual", locator="pdf://rendered-pages"))
    return tuple(sources)


def _word_sources(used: EvidenceUse) -> tuple[EvidenceSource, ...]:
    sources: list[EvidenceSource] = []
    if used in {"structure", "both"}:
        sources.append(EvidenceSource(kind="structure", locator="docx://word/document.xml"))
    if used in {"visual", "both"}:
        sources.append(EvidenceSource(kind="visual", locator="docx://word/media"))
    return tuple(sources)


def with_psw_evidence(
    result: AuditResultItem,
    *,
    audit: AuditInput,
    rule_no: int,
    used: EvidenceUse,
    attempted: EvidenceUse | None = None,
    diagnostics: Iterable[EvidenceDiagnostic] = (),
    sources: Iterable[EvidenceSource] | None = None,
) -> AuditResultItem:
    """Attach deterministic evidence provenance without changing the decision."""
    source_format, prepared_format, strategies, default_sources = _source_preparation(
        audit,
        used,
        attempted or used,
    )
    evidence = AuditEvidence(
        required=required_psw_evidence(rule_no),
        used=used,
        source_format=source_format,
        prepared_format=prepared_format,
        conversion_strategies=strategies,
        sources=tuple(sources) if sources is not None else default_sources,
        diagnostics=tuple(diagnostics),
    )
    return result.model_copy(update={"evidence": evidence})


def finalize_excel_psw_result(
    result: AuditResultItem,
    *,
    audit: AuditInput,
    rule_no: int,
    phase: Literal["decision", "guard", "error"],
) -> AuditResultItem:
    """Attach truthful native-workbook evidence at the result seam."""
    is_error = phase == "error" or result.status.upper() == "ERR"
    if is_error:
        return with_psw_evidence(
            result,
            audit=audit,
            rule_no=rule_no,
            used="none",
            diagnostics=(
                EvidenceDiagnostic(
                    code=DIAGNOSTIC_EVALUATION_RUNTIME_ERROR,
                    stage="evaluation",
                    severity="error",
                    message="PSW 规则执行发生运行时错误。",
                ),
            ),
        )

    if phase == "guard":
        used: EvidenceUse = "structure"
    elif rule_no == 3:
        used = "visual"
    elif rule_no == 5:
        used = "both"
    else:
        used = "structure"
    return with_psw_evidence(
        result,
        audit=audit,
        rule_no=rule_no,
        used=used,
    )


__all__ = [
    "DIAGNOSTIC_DOC_CONVERSION_FAILED",
    "DIAGNOSTIC_EVALUATION_RUNTIME_ERROR",
    "DIAGNOSTIC_EVALUATION_RULE_UNAVAILABLE",
    "DIAGNOSTIC_PDF_CONVERSION_FAILED",
    "DIAGNOSTIC_STRUCTURE_INSUFFICIENT",
    "DIAGNOSTIC_VISUAL_MISSING",
    "DIAGNOSTIC_VISUAL_RUNTIME_ERROR",
    "DIAGNOSTIC_VISUAL_UNVERIFIABLE",
    "EvidenceUse",
    "finalize_excel_psw_result",
    "required_psw_evidence",
    "with_psw_evidence",
]
