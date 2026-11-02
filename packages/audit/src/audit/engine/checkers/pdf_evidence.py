"""Keep PDF original location and conversion gaps on one check result."""

from __future__ import annotations

import json
import math

from documents.contracts import ConversionArtifact

from audit.contracts import AuditEvidence, EvidenceDiagnostic, EvidenceSource


class PdfSourceError(RuntimeError):
    """The PDF did not become a usable workbook. This must not be PASS."""

    def __init__(self, artifact: ConversionArtifact) -> None:
        self.artifact = artifact
        diagnostics = artifact.diagnostics or {}
        super().__init__(str(diagnostics.get("error") or "PDF conversion failed"))


def _pdf_diagnostics(artifact: ConversionArtifact) -> dict:
    diagnostics = artifact.diagnostics or {}
    nested = diagnostics.get("pdf_v2_diagnostics")
    return nested if isinstance(nested, dict) else {}


def _original_pages(artifact: ConversionArtifact) -> list[dict]:
    pages = _pdf_diagnostics(artifact).get("original_pages")
    if not isinstance(pages, list):
        return []
    return [page for page in pages if isinstance(page, dict)]


def budget_exhausted(artifact: ConversionArtifact) -> bool:
    diagnostics = artifact.diagnostics or {}
    error_code = str(diagnostics.get("error_code") or "")
    return diagnostics.get("budget_exhausted") is True or "budget" in error_code


def conversion_blocks_pass(artifact: ConversionArtifact) -> bool:
    """A located, converted PDF can pass. A gap cannot become PASS."""
    if not artifact.success or budget_exhausted(artifact):
        return True
    pages = _original_pages(artifact)
    if not pages:
        return True
    return any(page.get("success") is not True for page in pages)


def _region_token(region: object) -> str | None:
    """Serialize one original box. x may be absent. This is not a scan limit."""
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    values: list[int | float | None] = []
    for value in region:
        if value is None:
            values.append(None)
            continue
        if type(value) is bool or type(value) not in {int, float}:
            return None
        if type(value) is float and not math.isfinite(value):
            return None
        values.append(value)
    return json.dumps(values)


def evidence_from_conversion(artifact: ConversionArtifact, *, file_version_id: str) -> AuditEvidence:
    pages = _original_pages(artifact)
    sources: list[EvidenceSource] = []
    diagnostics: list[EvidenceDiagnostic] = []
    for page in pages:
        number = page.get("page")
        if type(number) is not int or number < 1:
            diagnostics.append(
                EvidenceDiagnostic(
                    code="page_location_failed",
                    stage="structure",
                    severity="error",
                    message="页码无法解析，不能作为原件位置",
                )
            )
            continue
        locator = f"file:{file_version_id};page:{number}"
        region = page.get("region")
        if region is not None:
            region_token = _region_token(region)
            if region_token is None:
                diagnostics.append(
                    EvidenceDiagnostic(
                        code="region_unresolved",
                        stage="structure",
                        severity="error",
                        message=f"第{number}页区域无法解析，保留页码",
                    )
                )
            else:
                locator += f";region:{region_token}"
        sources.append(EvidenceSource(kind="structure", locator=locator))
        if page.get("success") is not True:
            diagnostics.append(
                EvidenceDiagnostic(
                    code="page_location_failed",
                    stage="structure",
                    severity="error",
                    message=f"第{number}页未能定位表格" if not page.get("error") else str(page.get("error"))[:500],
                )
            )
    if not sources:
        sources.append(EvidenceSource(kind="structure", locator=f"file:{file_version_id}"))
    observed = artifact.diagnostics or {}
    if not artifact.success:
        diagnostics.insert(
            0,
            EvidenceDiagnostic(
                code=str(observed.get("error_code") or "conversion_failed"),
                stage="conversion",
                severity="error",
                message=str(observed.get("error") or "PDF 转换失败")[:500],
            ),
        )
    if budget_exhausted(artifact):
        diagnostics.append(
            EvidenceDiagnostic(
                code="budget_exhausted",
                stage="conversion",
                severity="error",
                message="预算用尽，不能判为通过。",
            )
        )
    strategies: list[str] = []
    for attempt in observed.get("conversion_attempts") or []:
        if isinstance(attempt, dict) and attempt.get("backend"):
            strategies.append(str(attempt["backend"]))
    if artifact.strategy_used and artifact.strategy_used not in strategies:
        strategies.append(artifact.strategy_used)
    return AuditEvidence(
        required="structure",
        used="structure" if artifact.success else "none",
        source_format="pdf",
        prepared_format="xlsx" if artifact.success else "pdf",
        conversion_strategies=tuple(strategies),
        sources=tuple(sources),
        diagnostics=tuple(diagnostics),
    )
