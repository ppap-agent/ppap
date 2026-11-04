"""Shared PSW PDF evidence selection for V2 and V3 agents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, cast

from openpyxl import load_workbook

from audit.contracts import AuditInput, EvidenceDiagnostic, EvidenceSource
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.rule_engine.excel_ops import DefaultExcelOps

from .psw_evidence_trace import (
    DIAGNOSTIC_EVALUATION_RULE_UNAVAILABLE,
    DIAGNOSTIC_EVALUATION_RUNTIME_ERROR,
    DIAGNOSTIC_PDF_CONVERSION_FAILED,
    EvidenceUse,
    with_psw_evidence,
)
from .psw_visual_evidence import (
    PSW_ALWAYS_VISUAL_RULES,
    PSW_CHECKBOX_DEPENDENT_RULES,
    PSW_RULE_FIELDS,
    evaluate_psw_observation,
)

logger = logging.getLogger(__name__)

_STRUCTURED_ANCHORS = (
    "零件提交保证书",
    "重量",
    "供应商名称/供应商代码",
    "提交原因",
    "认可情况",
)
_VISUAL_RULES = PSW_ALWAYS_VISUAL_RULES | PSW_CHECKBOX_DEPENDENT_RULES


@dataclass(frozen=True, slots=True)
class PswEvidenceCompleteness:
    """Explain whether a converted workbook is authoritative for PSW evidence."""

    structured_fields_complete: bool
    checkbox_controls_complete: bool
    stamp_signature_complete: bool
    missing_structured_anchors: tuple[str, ...]
    checkbox_control_count: int
    workbook_image_count: int
    checkbox_complete_rules: tuple[int, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def supports_checkbox_rule(self, rule_no: int) -> bool:
        """Return whether converted controls are authoritative for one rule."""
        return self.checkbox_controls_complete or rule_no in self.checkbox_complete_rules

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "1.0",
            "structured_fields": {
                "complete": self.structured_fields_complete,
                "missing_anchors": list(self.missing_structured_anchors),
            },
            "checkbox_controls": {
                "complete": self.checkbox_controls_complete,
                "count": self.checkbox_control_count,
                "complete_rules": list(self.checkbox_complete_rules),
                "reason": "authoritative_controls_found" if self.checkbox_controls_complete else "no_authoritative_checkbox_controls",
            },
            "stamp_signature": {
                "complete": self.stamp_signature_complete,
                "workbook_image_count": self.workbook_image_count,
                "reason": "converted_workbook_not_authoritative_for_stamp_or_signature",
            },
            "diagnostics": list(self.diagnostics),
        }


def unavailable_psw_conversion_evidence(reason: str) -> PswEvidenceCompleteness:
    return PswEvidenceCompleteness(
        structured_fields_complete=False,
        checkbox_controls_complete=False,
        stamp_signature_complete=False,
        missing_structured_anchors=_STRUCTURED_ANCHORS,
        checkbox_control_count=0,
        workbook_image_count=0,
        checkbox_complete_rules=(),
        diagnostics=(reason,),
    )


def assess_psw_conversion_evidence(xlsx_path: str | Path) -> PswEvidenceCompleteness:
    """Inspect actual workbook semantics instead of trusting file creation."""
    path = Path(xlsx_path)
    try:
        workbook = load_workbook(path, read_only=False, data_only=False)
    except Exception as exc:
        return unavailable_psw_conversion_evidence(f"workbook_unreadable:{type(exc).__name__}")

    try:
        compact_text = "\n".join(str(cell.value).replace(" ", "") for sheet in workbook.worksheets for row in sheet.iter_rows() for cell in row if cell.value is not None).lower()
        missing_anchors = tuple(anchor for anchor in _STRUCTURED_ANCHORS if anchor.replace(" ", "").lower() not in compact_text)
        ops = DefaultExcelOps()
        checkbox_controls = [control for sheet in workbook.worksheets for control in ops.get_checkbox_controls(sheet, source_path=str(path))]
        image_count = sum(len(getattr(sheet, "_images", []) or []) for sheet in workbook.worksheets)
    finally:
        workbook.close()

    diagnostics: list[str] = []
    if missing_anchors:
        diagnostics.append("missing_structured_anchors")
    if checkbox_controls:
        diagnostics.append("checkbox_controls_observed_without_completeness_proof")
    else:
        diagnostics.append("missing_authoritative_checkbox_controls")
    diagnostics.append("stamp_signature_requires_original_pdf")
    return PswEvidenceCompleteness(
        structured_fields_complete=not missing_anchors,
        checkbox_controls_complete=False,
        stamp_signature_complete=False,
        missing_structured_anchors=missing_anchors,
        checkbox_control_count=len(checkbox_controls),
        workbook_image_count=image_count,
        checkbox_complete_rules=(),
        diagnostics=tuple(diagnostics),
    )


class _PswEvidenceHost(Protocol):
    audit_file: AuditInput
    conversion_artifact: Any
    excel_agent: Any
    rule_checkers: list[Any]

    @property
    def document_type_name(self) -> str: ...

    def log_audit_start(self, ith: int) -> None: ...

    def log_audit_result(self, result: AuditResult, ith: int) -> None: ...

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult: ...

    def get_rule_by_index(self, ith: int) -> Optional[dict[str, Any]]: ...


class PswPdfEvidenceMixin:
    """Select deterministic workbook or original-page evidence per PSW rule."""

    conversion_artifact: Any
    excel_agent: Any
    psw_evidence: PswEvidenceCompleteness
    _psw_conversion_exception: Optional[Exception]

    def _host(self) -> _PswEvidenceHost:
        return cast(_PswEvidenceHost, self)

    def _convert_pdf_to_excel(self) -> Optional[str]:
        parent_convert = getattr(super(), "_convert_pdf_to_excel")
        try:
            path = cast(Optional[str], parent_convert())
        except Exception as exc:
            self._psw_conversion_exception = exc
            self.psw_evidence = unavailable_psw_conversion_evidence(f"conversion_exception:{type(exc).__name__}")
            raise
        self._psw_conversion_exception = None
        evidence = assess_psw_conversion_evidence(path) if path else unavailable_psw_conversion_evidence("conversion_failed")
        self.psw_evidence = evidence
        artifact = getattr(self, "conversion_artifact", None)
        if artifact is not None:
            artifact.diagnostics["evidence_completeness"] = evidence.to_dict()
        return path

    def _evidence(self) -> PswEvidenceCompleteness:
        return getattr(
            self,
            "psw_evidence",
            unavailable_psw_conversion_evidence("evidence_not_assessed"),
        )

    def _trace_result(
        self,
        result: AuditResult,
        *,
        rule_no: int,
        used: EvidenceUse,
        attempted: EvidenceUse | None = None,
        diagnostic: EvidenceDiagnostic | None = None,
        audit: AuditInput | None = None,
        sources: tuple[EvidenceSource, ...] | None = None,
    ) -> AuditResult:
        trace_audit = audit or self._host().audit_file
        if audit is None:
            metadata = dict(trace_audit.file_metadata or {})
            metadata.setdefault("source_pdf_url", trace_audit.file_url)
            artifact = getattr(self, "conversion_artifact", None)
            metadata.setdefault(
                "pdf_conversion_strategy",
                str(getattr(artifact, "strategy_used", "") or "local_pdf_to_xlsx"),
            )
            trace_audit = trace_audit.model_copy(update={"file_metadata": metadata})
        return with_psw_evidence(
            result,
            audit=trace_audit,
            rule_no=rule_no,
            used=used,
            attempted=attempted,
            diagnostics=(diagnostic,) if diagnostic is not None else (),
            sources=sources,
        )

    def _failed_conversion_rule(self, ith: int) -> AuditResult:
        host = self._host()
        conversion_exception = getattr(self, "_psw_conversion_exception", None)
        if conversion_exception is not None:
            result = host.create_error_result(f"PDF 转换运行异常（{type(conversion_exception).__name__}）。", ith)
            return self._trace_result(
                result,
                rule_no=ith + 1,
                used="none",
                diagnostic=EvidenceDiagnostic(
                    code=DIAGNOSTIC_PDF_CONVERSION_FAILED,
                    stage="conversion",
                    severity="error",
                    message="PDF 转换发生运行时错误。",
                ),
            )
        artifact = getattr(self, "conversion_artifact", None)
        if artifact is None:
            result = host.create_error_result("PDF 转换未产生诊断结果。", ith)
            return self._trace_result(
                result,
                rule_no=ith + 1,
                used="none",
                diagnostic=EvidenceDiagnostic(
                    code=DIAGNOSTIC_PDF_CONVERSION_FAILED,
                    stage="conversion",
                    severity="error",
                    message="PDF 转换未产生诊断结果。",
                ),
            )
        diagnostics = getattr(artifact, "diagnostics", {}) if artifact is not None else {}
        error_code = str(diagnostics.get("error_code", "") or "")
        if error_code in {
            "paddle_evidence_disabled",
            "paddle_evidence_unconfigured",
            "paddle_page_selection_required",
            "paddle_table_unconfirmed",
            "paddle_table_incomplete",
            "paddle_request_budget_exhausted",
            "paddle_scope_closed",
        }:
            return self._trace_result(
                AuditResult(classification="PDF结构化证据", item=f"PSW - 规则{ith + 1}", priority="P0", status="UNABLE", suggestions="原生或Paddle证据尚未形成可确认的表格，请核对原件。"),
                rule_no=ith + 1,
                used="none",
                diagnostic=EvidenceDiagnostic(code="psw.pdf." + error_code, stage="conversion", severity="warning", message="当前原件表格内容未能确认；未作通过或不符合判断。"),
            )
        message = "PDF 转换未产出可读取的表格，无法执行结构检查。"
        if error_code:
            message += f"转换错误：{error_code}。"
        return self._trace_result(
            host.create_error_result(message, ith),
            rule_no=ith + 1,
            used="none",
            diagnostic=EvidenceDiagnostic(
                code=DIAGNOSTIC_PDF_CONVERSION_FAILED,
                stage="conversion",
                severity="error",
                message=message,
            ),
        )

    def fallback_rule_checkers(self, ith: int) -> Optional[AuditResult]:
        rule_no = ith + 1
        if rule_no not in _VISUAL_RULES:
            return None
        return self._run_paddle_rule(ith, rule_no)

    def _run_structured_rule(self, ith: int) -> Optional[AuditResult]:
        host = self._host()
        if host.excel_agent is None:
            return self._failed_conversion_rule(ith)
        result = cast(Optional[AuditResult], host.excel_agent.check(ith))
        if result is not None and result.evidence is None:
            result = self._trace_result(
                result,
                rule_no=ith + 1,
                used="structure",
                audit=cast(AuditInput, host.excel_agent.audit_file),
            )
        return result

    def check(self, ith: int) -> Optional[AuditResult]:
        host = self._host()
        host.log_audit_start(ith)
        rule_no = ith + 1
        result: Optional[AuditResult]
        try:
            if host.excel_agent is None:
                result = self.fallback_rule_checkers(ith) or self._failed_conversion_rule(ith)
            elif rule_no in PSW_ALWAYS_VISUAL_RULES:
                result = self._run_paddle_rule(ith, rule_no)
            elif rule_no == 7:
                structured = self._run_structured_rule(ith)
                if (structured is not None and str(structured.status).upper() == "PASS") or self._evidence().supports_checkbox_rule(rule_no):
                    result = structured
                else:
                    result = self._run_paddle_rule(ith, rule_no, used="both")
            elif rule_no in {8, 9, 10} and not self._evidence().supports_checkbox_rule(rule_no):
                result = self._run_paddle_rule(ith, rule_no)
            else:
                result = self._run_structured_rule(ith)
        except Exception as exc:
            logger.exception("PSW rule %s evidence evaluation failed", rule_no)
            result = self._trace_result(
                host.create_error_result(str(exc), ith),
                rule_no=rule_no,
                used="none",
                diagnostic=EvidenceDiagnostic(
                    code=DIAGNOSTIC_EVALUATION_RUNTIME_ERROR,
                    stage="evaluation",
                    severity="error",
                    message="PSW 证据编排发生运行时错误。",
                ),
            )

        if result is not None:
            host.log_audit_result(result, ith)
        from audit.checkers.v4.paddle_evidence import observe_psw_shadow

        observe_psw_shadow(self, rule_no)
        return result

    async def run(self) -> list[AuditResult]:
        if not self._host().rule_checkers:
            return [
                self._trace_result(
                    self._host().create_error_result(
                        "No available rule checkers for PSW PDF audit.",
                        0,
                    ),
                    rule_no=1,
                    used="none",
                    diagnostic=EvidenceDiagnostic(
                        code=DIAGNOSTIC_EVALUATION_RULE_UNAVAILABLE,
                        stage="evaluation",
                        severity="error",
                        message="PSW PDF 未找到可执行的规则。",
                    ),
                )
            ]
        results: list[AuditResult] = []
        for ith in range(len(self._host().rule_checkers)):
            result = self.check(ith)
            if result is not None:
                results.append(result)
        return results

    def _resolve_success_slots(self, excel_rule_count: int) -> int:
        rules = getattr(getattr(self, "rule", None), "rules", None)
        if isinstance(rules, list) and not rules:
            return excel_rule_count
        parent_resolve = getattr(super(), "_resolve_success_slots")
        return cast(int, parent_resolve(excel_rule_count))

    def _run_paddle_rule(
        self,
        ith: int,
        rule_no: int,
        *,
        used: EvidenceUse = "visual",
    ) -> AuditResult:
        from audit.checkers.v4.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts

        host = self._host()
        if not host.get_rule_by_index(ith):
            return self._trace_result(
                host.create_error_result("无法获取规则数据", ith),
                rule_no=rule_no,
                used="none",
                diagnostic=EvidenceDiagnostic(code=DIAGNOSTIC_EVALUATION_RULE_UNAVAILABLE, stage="evaluation", severity="error", message=f"PSW PDF 规则 {rule_no} 缺少可执行配置。"),
            )
        sources: list[EvidenceSource] = []

        def witness(locator: dict[str, object], excerpt: str, *, visual: bool = False) -> None:
            sources.append(EvidenceSource(kind="visual" if visual else "structure", locator=json.dumps(locator, ensure_ascii=False, separators=(",", ":")), excerpt=excerpt[:500]))

        if not hasattr(self, "_paddle_page_cache"):
            self._paddle_page_cache: dict = {}
        try:
            facts = read_original_pdf_visual_facts(host.audit_file, PSW_RULE_FIELDS[rule_no], witness, operation=f"psw_rule_{rule_no}", cache=self._paddle_page_cache)
            result = evaluate_psw_observation(rule_no, facts)
            diagnostic = None
            if result.status == "UNABLE":
                diagnostic = EvidenceDiagnostic(code="psw.paddle.fact_unconfirmed", stage="visual", severity="warning", message="原生或Paddle证据未能确认所需事实；不调用LLM补判。")
        except VisualFactsUnavailable:
            result = evaluate_psw_observation(rule_no, {})
            diagnostic = EvidenceDiagnostic(code="psw.paddle.fact_unconfirmed", stage="visual", severity="warning", message="当前原件不能提供可确认的Paddle事实，请核对原件。")
        except Exception as exc:
            result = host.create_error_result(f"Paddle证据读取失败（{type(exc).__name__}）。", ith)
            diagnostic = EvidenceDiagnostic(code="psw.paddle.runtime_error", stage="visual", severity="error", message="Paddle证据读取发生技术错误，未形成业务判断。")
        source_kinds = {source.kind for source in sources}
        evidence_used: EvidenceUse = "both" if source_kinds == {"structure", "visual"} else "visual" if "visual" in source_kinds else "structure" if sources else "none"
        if used == "both" and evidence_used == "visual":
            # The preceding structured rule read a real submission note/field.
            evidence_used = "both"
            sources.insert(0, EvidenceSource(kind="structure", locator="pdf://converted-table-grid"))
        traced = self._trace_result(result, rule_no=rule_no, used=evidence_used, diagnostic=diagnostic, sources=tuple(sources))
        # This path never renders pages for an LLM. Report only observed sources.
        if traced.evidence is not None:
            strategies = ("native_pdf", "paddle_single_page") if "visual" in source_kinds else ("native_pdf",)
            traced = traced.model_copy(update={"evidence": traced.evidence.model_copy(update={"conversion_strategies": strategies, "prepared_format": "pdf"})})
        return traced


__all__ = [
    "PswEvidenceCompleteness",
    "PswPdfEvidenceMixin",
    "assess_psw_conversion_evidence",
    "unavailable_psw_conversion_evidence",
]
