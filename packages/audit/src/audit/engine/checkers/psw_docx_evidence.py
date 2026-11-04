"""Shared DOCX PSW adapter over structured and source-located Word evidence."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, cast

from documents.word import WordBlock, WordDocument, WordParagraphBlock, WordTableBlock
from openpyxl import Workbook

from audit.contracts import AuditInput, EvidenceDiagnostic, EvidenceSource
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.rule_engine import GuardStop, RuleContext

from .docx_workbook_agent import (
    PSW_DOCX_PRIMARY_TABLE,
    DocxPrimaryTableContract,
    WordEvidenceExcelOps,
)
from .psw_evidence_trace import (
    DIAGNOSTIC_EVALUATION_RUNTIME_ERROR,
    DIAGNOSTIC_STRUCTURE_INSUFFICIENT,
    DIAGNOSTIC_VISUAL_MISSING,
    DIAGNOSTIC_VISUAL_RUNTIME_ERROR,
    EvidenceUse,
    with_psw_evidence,
)
from .psw_visual_evidence import PSW_ALWAYS_VISUAL_RULES, evaluate_psw_observation


class _PswDocxHost(Protocol):
    audit_file: AuditInput
    word_document: WordDocument

    def _load_workbook(self) -> Workbook: ...

    def rule_ctx(self, workbook: Workbook) -> RuleContext: ...

    def log_audit_start(self, ith: int) -> None: ...

    def log_audit_result(self, result: AuditResult, ith: int) -> None: ...

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult: ...


class PswDocxEvidenceMixin:
    """Route PSW DOCX rules without rendering or office conversion."""

    DOCX_PRIMARY_TABLE_CONTRACT: DocxPrimaryTableContract | None = PSW_DOCX_PRIMARY_TABLE
    DOCX_NAMED_TABLE_ANCHORS: tuple[tuple[str, tuple[str, ...], int], ...] = (("PSW交付物清单", ("生产件批准交付物文件清单", "项目", "附件"), 2),)

    def _host(self) -> _PswDocxHost:
        return cast(_PswDocxHost, self)

    def _trace_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        used: EvidenceUse,
        diagnostic: EvidenceDiagnostic | None = None,
        sources: tuple[EvidenceSource, ...] | None = None,
    ) -> AuditResult:
        return with_psw_evidence(
            result,
            audit=self._host().audit_file,
            rule_no=rule_no,
            used=used,
            diagnostics=(diagnostic,) if diagnostic is not None else (),
            sources=sources,
        )

    def _word_text_evidence(self) -> list[WordParagraphBlock]:
        sources: list[WordParagraphBlock] = []

        def visit(blocks: Sequence[WordBlock]) -> None:
            for block in blocks:
                if isinstance(block, WordParagraphBlock):
                    if block.text.strip():
                        sources.append(block)
                elif isinstance(block, WordTableBlock):
                    for cell in block.cells:
                        if cell.origin:
                            visit(cell.blocks)

        document = self._host().word_document
        visit((*document.blocks, *document.headers, *document.footers))
        return sources

    def _missing_visual_result(self, rule_no: int) -> AuditResult:
        document = getattr(self, "word_document", None)
        sources = []
        if document is not None:
            sources = [f"{item.source.part}:{item.source.path}" for item in document.visual_evidence if item.kind in {"embedded_image", "drawing"}]
        source_note = f"；已发现但无法验证的来源: {', '.join(sources)}" if sources else ""
        return self._trace_result(
            AuditResult(
                classification="DOCX视觉证据",
                item=f"PSW - 规则{rule_no}",
                priority="P0",
                status="UNABLE",
                suggestions=f"未找到可验证的印章或签字事实，请核对原件{source_note}",
            ),
            rule_no=rule_no,
            used="none",
            diagnostic=EvidenceDiagnostic(
                code=DIAGNOSTIC_VISUAL_MISSING,
                stage="visual",
                severity="warning",
                message="DOCX 未找到可验证的内嵌视觉证据；缺少证据不证明缺少印章或签字。",
            ),
        )

    def check(self, ith: int) -> Optional[AuditResult]:
        rule_no = ith + 1
        if rule_no not in PSW_ALWAYS_VISUAL_RULES:
            parent_check = getattr(super(), "check")
            return cast(Optional[AuditResult], parent_check(ith))

        host = self._host()
        host.log_audit_start(ith)
        try:
            workbook = host._load_workbook()
            context = host.rule_ctx(workbook)
            ops = cast(WordEvidenceExcelOps, context.ops)
            evidence = ops.all_image_evidence()
            if not evidence:
                result = self._missing_visual_result(rule_no)
            else:
                text_evidence = self._word_text_evidence()
                # The current Paddle contract accepts selected PDF pages. A
                # DOCX image is not a PDF page and OCR text cannot establish a
                # signature or full seal wording. Do not render, infer, or call
                # an LLM to manufacture these facts.
                result = evaluate_psw_observation(rule_no, {})
                sources = tuple(EvidenceSource(kind="structure", locator=f"docx://{part}") for part in dict.fromkeys(block.source.part for block in text_evidence))
                result = self._trace_result(
                    result,
                    rule_no=rule_no,
                    used="structure" if sources else "none",
                    diagnostic=EvidenceDiagnostic(code="psw.paddle.unsupported_source", stage="visual", severity="warning", message="当前Paddle单页证据不支持DOCX印章或签字判断，请核对原件。"),
                    sources=sources,
                )
        except GuardStop as stop:
            is_runtime_error = stop.status == "ERR"
            result = self._trace_result(
                AuditResult(
                    classification="DOCX结构化证据",
                    item=f"PSW - 规则{rule_no}",
                    priority="P0",
                    status="ERR" if is_runtime_error else "UNABLE",
                    suggestions=stop.message,
                ),
                rule_no=rule_no,
                used="none" if is_runtime_error else "structure",
                diagnostic=EvidenceDiagnostic(
                    code=(DIAGNOSTIC_EVALUATION_RUNTIME_ERROR if is_runtime_error else DIAGNOSTIC_STRUCTURE_INSUFFICIENT),
                    stage="evaluation" if is_runtime_error else "structure",
                    severity="error" if is_runtime_error else "warning",
                    message=("DOCX 规则执行发生运行时错误。" if is_runtime_error else "DOCX 结构不足，无法定位 PSW 视觉证据。"),
                ),
            )
        except Exception as exc:
            result = self._trace_result(
                host.create_error_result(str(exc), ith),
                rule_no=rule_no,
                used="none",
                diagnostic=EvidenceDiagnostic(
                    code=DIAGNOSTIC_VISUAL_RUNTIME_ERROR,
                    stage="visual",
                    severity="error",
                    message="DOCX 视觉证据处理发生运行时错误。",
                ),
            )
        host.log_audit_result(result, ith)
        return result


__all__ = ["PswDocxEvidenceMixin"]
