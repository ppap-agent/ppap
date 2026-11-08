"""L17 PSW PDF carrier owned by v4.

The evidence policy is the shared engine mixin. Converted rows are checked by
``LiAutoPswAgent``. This module does not import or subclass a v2/v3 PSW class.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import unquote

from audit.contracts import AuditInput, GroupContextInput, RuleInput, RuleItem
from audit.contracts import AuditResultItem as AuditResult
from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin
from audit.kinds import PpapDocumentType

from .psw import LiAutoPswAgent

logger = logging.getLogger(__name__)


def _has_original_pages(artifact: Any) -> bool:
    diagnostics = getattr(artifact, "diagnostics", None) or {}
    nested = diagnostics.get("pdf_v2_diagnostics") if isinstance(diagnostics, dict) else None
    pages = nested.get("original_pages") if isinstance(nested, dict) else None
    return isinstance(pages, list) and any(isinstance(page, dict) for page in pages)


class _L17PswPdfHost:
    """PDF-to-workbook host for one v4 PSW carrier.

    The few runtime helpers below are copied for this carrier. It does not
    inherit a versioned checker base class.
    """

    TARGET_EXCEL_AGENT_CLASS: type[Any] | None = None
    FALLBACK_AGENT_RULES: tuple[str, ...] | list[str] | None = None

    def __init__(self, audit_file: AuditInput, rule: RuleInput, group_context: GroupContextInput | None = None) -> None:
        self.audit_file = audit_file
        self.rule = rule
        self.group_context = group_context
        self.logger = logging.getLogger(type(self).__name__)
        self.rule_checkers: list[Any] = []
        self.agent_rules: list[str] = []
        self.temp_files: list[str] = []
        self.conversion_artifact: Any = None
        self.excel_agent: Any = None
        if not self._init_excel_delegate():
            self._configure_failed_slots()

    def _register_temp_file(self, file_path: str | None) -> None:
        if file_path:
            self.temp_files.append(file_path)

    def _cleanup_temp_files(self) -> None:
        for file_path in self.temp_files:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    logger.error("未能清理临时表格 %s", file_path)

    def __del__(self) -> None:
        self._cleanup_temp_files()

    def close(self) -> None:
        try:
            close = getattr(self.excel_agent, "close", None)
            if callable(close):
                close()
        finally:
            self._cleanup_temp_files()

    def _convert_pdf_to_excel(self) -> str | None:
        from documents.contracts import FileRef
        from documents.conversion import convert_pdf_to_excel_auto

        from audit.engine.runtime import get_runtime

        file_url = str(self.audit_file.file_url)
        declared = getattr(type(self), "DOC_TYPE", None)
        doc_type: PpapDocumentType | None = declared if isinstance(declared, PpapDocumentType) else PpapDocumentType.from_string(str(self.rule.type or ""))
        if doc_type == PpapDocumentType.UNKNOWN:
            doc_type = None
        runtime = get_runtime()
        if runtime is not None:
            artifact = runtime.convert_pdf_to_excel(FileRef(location=file_url), enable_cache=True, doc_type=doc_type)
        else:
            artifact = convert_pdf_to_excel_auto(FileRef(location=file_url), enable_cache=True, doc_type=doc_type)
        self.conversion_artifact = artifact
        if not artifact.success or not artifact.xlsx_path:
            logger.error("PDF 转表格失败: %s", artifact.diagnostics)
            return None
        for temp_path in artifact.temp_files:
            if temp_path != artifact.xlsx_path:
                self._register_temp_file(temp_path)
        return artifact.xlsx_path

    def _init_excel_delegate(self) -> bool:
        excel_cls = self.TARGET_EXCEL_AGENT_CLASS
        if excel_cls is None:
            return False
        try:
            temp_excel_path = self._convert_pdf_to_excel()
            if not temp_excel_path:
                return False
            self._register_temp_file(temp_excel_path)
            file_metadata = getattr(self.audit_file, "file_metadata", None)
            normalized_metadata = dict(file_metadata) if isinstance(file_metadata, dict) else {}
            normalized_metadata.setdefault("source_pdf_url", str(getattr(self.audit_file, "file_url", "") or ""))
            strategy = str(getattr(getattr(self, "conversion_artifact", None), "strategy_used", "") or "")
            normalized_metadata.setdefault("pdf_conversion_strategy", strategy or "local_pdf_to_xlsx")
            excel_audit_file = AuditInput(
                audit_id=str(getattr(self.audit_file, "audit_id", "")),
                file_url=temp_excel_path,
                customer=getattr(self.audit_file, "customer", None),
                model=getattr(self.audit_file, "model", None),
                part=getattr(self.audit_file, "part", None),
                content=getattr(self.audit_file, "content", None),
                file_metadata=normalized_metadata,
                project_id=str(getattr(self.audit_file, "project_id", "default") or "default"),
            )
            self.excel_agent = excel_cls(excel_audit_file, self.rule, group_context=self.group_context)
            excel_rules = list(getattr(self.excel_agent, "agent_rules", []) or [])
            slots = self._resolve_success_slots(len(list(getattr(self.excel_agent, "rule_checkers", []) or [])))
            self.rule_checkers = [self.excel_agent.check] * slots
            self.agent_rules = excel_rules[:slots] if excel_rules else [f"{self.document_type_name}-规则{i + 1}" for i in range(slots)]
            bound = getattr(self.excel_agent, "checks", None)
            if bound:
                self.checks = tuple(self._with_pdf_evidence_check(item) for item in bound)
            return True
        except Exception:
            logger.exception("未能为 %s 建立表格检查", type(self).__name__)
            return False

    def _with_pdf_evidence_check(self, checker: Any) -> Any:
        from audit.engine.checkers.rule_check import RuleCheck

        delegator = self

        def run() -> Any:
            return delegator._apply_pdf_evidence(checker.check())

        return RuleCheck(checker.rule_item, run, binding_error=getattr(checker, "binding_error", None))

    def _apply_pdf_evidence(self, result: AuditResult | None) -> AuditResult | None:
        artifact = self.conversion_artifact
        delegate = self.excel_agent
        if result is None or artifact is None or delegate is None or not _has_original_pages(artifact):
            return result
        decorate = getattr(delegate, "_with_source_evidence", None)
        if not callable(decorate):
            return result
        previous = getattr(delegate, "_conversion_artifact", None)
        delegate._conversion_artifact = artifact
        try:
            return decorate(result)
        finally:
            delegate._conversion_artifact = previous

    def _resolve_success_slots(self, excel_rule_count: int) -> int:
        rules = getattr(self.rule, "rules", [])
        if isinstance(rules, list):
            return min(excel_rule_count, len(rules))
        return excel_rule_count

    def _resolve_failed_slots(self) -> int:
        rules = getattr(self.rule, "rules", [])
        return len(rules) if isinstance(rules, list) else 0

    def _resolve_fallback_agent_rules(self, slots: int) -> list[str]:
        default_rules = [f"{self.document_type_name}-规则{i + 1}" for i in range(slots)]
        preset = list(self.FALLBACK_AGENT_RULES or [])
        if len(preset) >= slots:
            return preset[:slots]
        return preset + default_rules[len(preset) :]

    def _configure_failed_slots(self) -> None:
        slots = self._resolve_failed_slots()
        self.rule_checkers = [self._run_fallback_rule] * slots
        self.agent_rules = self._resolve_fallback_agent_rules(slots)

    def _run_fallback_rule(self, ith: int) -> AuditResult | None:
        fallback_result = self.fallback_rule_checkers(ith)
        if fallback_result is not None:
            return fallback_result
        return self._failed_conversion_rule(ith)

    def fallback_rule_checkers(self, ith: int) -> AuditResult | None:
        return None

    def _failed_conversion_rule(self, ith: int) -> AuditResult:
        return self.create_error_result("PDF to Excel conversion failed, cannot run deterministic rule checker.", ith)

    @property
    def document_type_name(self) -> str:
        return type(self).__name__.replace("Agent", "")

    def get_rule_by_index(self, ith: int) -> dict[str, Any] | None:
        try:
            rules = getattr(self.rule, "rules", [])
            if isinstance(rules, list) and 0 <= ith < len(rules):
                rule = rules[ith]
                if isinstance(rule, dict):
                    return rule
                return RuleItem.model_validate(rule).model_dump()
            return None
        except Exception:
            self.logger.exception("获取规则失败")
            return None

    def create_error_result(self, error_msg: str, ith: int = -1) -> AuditResult:
        return AuditResult(
            classification="规则审核",
            item=f"{self.document_type_name} - 规则{ith + 1}",
            priority="P0",
            status="ERR",
            suggestions=f"审核过程中出现错误: {error_msg}",
        )

    def log_audit_start(self, ith: int) -> None:
        self.logger.debug("开始审核 %s - 文件: %s, 规则: %s", self.document_type_name, os.path.basename(unquote(str(getattr(self.audit_file, "file_url", "") or ""))), ith + 1)

    def log_audit_result(self, result: AuditResult, ith: int) -> None:
        self.logger.debug("审核完成 %s - 规则: %s, 状态: %s, 项目: %s", self.document_type_name, ith + 1, result.status, result.item)


class LiAutoPswPdfAgent(PswPdfEvidenceMixin, _L17PswPdfHost):
    """理想 L17 PDF。正式条款由本版本 ``LiAutoPswAgent`` 执行。"""

    DOC_TYPE = PpapDocumentType.PSW
    TARGET_EXCEL_AGENT_CLASS = LiAutoPswAgent
    FALLBACK_AGENT_RULES = LiAutoPswAgent.EXPECTED_RULE_CATEGORIES


def _publish_same_version_clauses(pdf_cls: type[Any], excel_cls: type[Any]) -> None:
    """Name the same v4 clauses on the PDF carrier without running them here.

    Frozen suite bindings require every carrier class to declare those methods.
    Execution still indexes ``TARGET_EXCEL_AGENT_CLASS`` and calls ``check_source_rule``.
    """
    from audit.engine.checkers.rule_declarations import discover_declared_rules

    for name, description in discover_declared_rules(excel_cls):

        def marker(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("l17_pdf_clause_runs_on_liauto_psw_agent")

        marker.__name__ = name
        marker.__qualname__ = f"{pdf_cls.__name__}.{name}"
        setattr(marker, "__rule_description__", description)
        setattr(pdf_cls, name, marker)


_publish_same_version_clauses(LiAutoPswPdfAgent, LiAutoPswAgent)
