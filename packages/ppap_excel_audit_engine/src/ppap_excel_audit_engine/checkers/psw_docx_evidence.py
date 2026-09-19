"""Shared DOCX PSW adapter over structured and source-located Word evidence."""

from __future__ import annotations

from typing import Optional, Protocol, cast

from openpyxl import Workbook
from ppap_core.contracts import AuditInput, EvidenceDiagnostic, EvidenceSource
from ppap_core.contracts import AuditResultItem as AuditResult
from ppap_docops.word import WordDocument

from ppap_excel_audit_engine.rule_engine import GuardStop, RuleContext
from ppap_excel_audit_engine.runtime import get_runtime

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
    DIAGNOSTIC_VISUAL_UNVERIFIABLE,
    EvidenceUse,
    with_psw_evidence,
)
from .psw_visual_evidence import PSW_ALWAYS_VISUAL_RULES, PswVisualRuleEvaluator


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

    def _analyze_docx_images(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[str],
        json_format: bool,
    ) -> str:
        runtime = get_runtime()
        if runtime is None:
            raise RuntimeError("DOCX 视觉规则缺少运行时支持")
        return cast(
            str,
            runtime.run_image_prompt(
                system_prompt=system_prompt,
                user_text=user_prompt,
                image_base64_list=images,
                json_format=json_format,
            ),
        )

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
                status="NG",
                suggestions=f"未找到可验证的内嵌图片，不能确认印章或签字{source_note}",
            ),
            rule_no=rule_no,
            used="none",
            diagnostic=EvidenceDiagnostic(
                code=DIAGNOSTIC_VISUAL_MISSING,
                stage="visual",
                severity="error",
                message="DOCX 未找到可验证的内嵌视觉证据。",
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
                result = PswVisualRuleEvaluator(self._analyze_docx_images).evaluate(
                    rule_no,
                    [item.data_url for item in evidence],
                    source_locations=[f"{item.evidence.source.part}:{item.evidence.source.path}" for item in evidence],
                )
                diagnostic = None
                if result.status == "NG" and "视觉证据识别失败" in result.suggestions:
                    diagnostic = EvidenceDiagnostic(
                        code=DIAGNOSTIC_VISUAL_UNVERIFIABLE,
                        stage="visual",
                        severity="error",
                        message="视觉模型返回结果不可验证。",
                    )
                sources = tuple(
                    EvidenceSource(
                        kind="visual",
                        locator=f"docx://{item.evidence.source.part}#{item.evidence.source.path}",
                    )
                    for item in evidence
                )
                result = self._trace_result(
                    result,
                    rule_no=rule_no,
                    used="visual" if diagnostic is None else "none",
                    diagnostic=diagnostic,
                    sources=sources if diagnostic is None else (),
                )
        except GuardStop as stop:
            is_runtime_error = stop.status == "ERR"
            result = self._trace_result(
                AuditResult(
                    classification="DOCX结构化证据",
                    item=f"PSW - 规则{rule_no}",
                    priority="P0",
                    status=stop.status,
                    suggestions=stop.message,
                ),
                rule_no=rule_no,
                used="none" if is_runtime_error else "structure",
                diagnostic=EvidenceDiagnostic(
                    code=(DIAGNOSTIC_EVALUATION_RUNTIME_ERROR if is_runtime_error else DIAGNOSTIC_STRUCTURE_INSUFFICIENT),
                    stage="evaluation" if is_runtime_error else "structure",
                    severity="error",
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
