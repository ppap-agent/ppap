"""PDF -> Excel delegator agents.

This module centralizes the previously duplicated PDF wrappers that only
converted PDF files and delegated rule checks to Excel agents.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any, List, Optional, Type

from ppap_core.contracts import AuditInput, GroupContextInput, RuleInput
from ppap_core.contracts import AuditResultItem as AuditResult

from ..agents.base_agent import BaseAgent
from .annual_test_requirements_agent import AnnualTestRequirementsAgent
from .control_plan_agent import ControlPlanAgent
from .dpvr_excel_agent import DpvrExcelAgent
from .dvp_r_excel_agent import DvpRExcelAgent
from .equipment_list_agent import EquipmentListAgent
from .initial_process_capacity_agent import InitialProcessCapacityAgent
from .msa_report_agent import MsaReportAgent
from .pdf_agent import PDFAgent
from .pfmea_agent import PfmeaAgent
from .plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from .process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from .process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
from .sub_supplier_list_excel_agent import SubSupplierListExcelAgent
from .tooling_approval_agent import ToolingApprovalAgent
from .xir_assembly_issue_agent import XirAssemblyIssueAgent

logger = logging.getLogger(__name__)


class PdfExcelDelegatorAgent(PDFAgent):
    """Generic PDF delegator that proxies checks to a target Excel agent."""

    TARGET_EXCEL_AGENT_CLASS: Optional[Type[BaseAgent]] = None
    FALLBACK_AGENT_RULES: Optional[List[str]] = None

    def __init__(
        self,
        audit_file: AuditInput,
        rule: RuleInput,
        group_context: GroupContextInput | None = None,
    ) -> None:
        super().__init__(audit_file, rule, group_context=group_context)
        self.excel_agent = None

        if not self._init_excel_delegate():
            self._configure_failed_slots()

    def _init_excel_delegate(self) -> bool:
        excel_cls = self.TARGET_EXCEL_AGENT_CLASS
        if excel_cls is None:
            logger.error("TARGET_EXCEL_AGENT_CLASS is not configured for %s", type(self).__name__)
            return False

        try:
            temp_excel_path = self._convert_pdf_to_excel()
            if not temp_excel_path:
                logger.error("PDF to Excel conversion failed for %s", type(self).__name__)
                return False

            self._register_temp_file(temp_excel_path)
            preprocessed_path = self.preprocess_excel_path(temp_excel_path)
            if not preprocessed_path:
                logger.error("Excel preprocess failed for %s", type(self).__name__)
                return False
            if preprocessed_path != temp_excel_path:
                self._register_temp_file(preprocessed_path)

            file_metadata = getattr(self.audit_file, "file_metadata", None)
            if isinstance(file_metadata, dict):
                normalized_metadata = dict(file_metadata)
            else:
                normalized_metadata = {}
            normalized_metadata.setdefault(
                "source_pdf_url",
                str(getattr(self.audit_file, "file_url", "") or ""),
            )
            strategy = str(getattr(getattr(self, "conversion_artifact", None), "strategy_used", "") or "")
            normalized_metadata.setdefault("pdf_conversion_strategy", strategy or "local_pdf_to_xlsx")

            excel_audit_file = AuditInput(
                audit_id=str(getattr(self.audit_file, "audit_id", "")),
                file_url=preprocessed_path,
                customer=getattr(self.audit_file, "customer", None),
                model=getattr(self.audit_file, "model", None),
                part=getattr(self.audit_file, "part", None),
                content=getattr(self.audit_file, "content", None),
                file_metadata=normalized_metadata,
                project_id=str(getattr(self.audit_file, "project_id", "default") or "default"),
            )
            self.excel_agent = self._create_excel_agent(excel_cls, excel_audit_file)
            if not self.excel_agent:
                return False

            excel_checkers = list(getattr(self.excel_agent, "rule_checkers", []) or [])
            excel_rules = list(getattr(self.excel_agent, "agent_rules", []) or [])
            slots = self._resolve_success_slots(len(excel_checkers))
            self.rule_checkers = [self.excel_agent.check] * slots
            self.agent_rules = excel_rules[:slots] if excel_rules else [f"{self.document_type_name}-规则{i + 1}" for i in range(slots)]
            logger.debug(
                "%s delegated to %s with %d slots",
                type(self).__name__,
                excel_cls.__name__,
                slots,
            )
            return True
        except Exception as exc:
            logger.error("Failed to initialize %s: %s", type(self).__name__, exc, exc_info=True)
            return False

    def _create_excel_agent(self, excel_cls: Type[BaseAgent], excel_audit_file: AuditInput) -> Any:
        return excel_cls(
            excel_audit_file,
            self.rule,
            group_context=self.group_context,
        )

    def close(self) -> None:
        """Close the delegated workbook before removing conversion files."""
        try:
            close = getattr(self.excel_agent, "close", None)
            if callable(close):
                close()
        finally:
            self._cleanup_temp_files()

    def _resolve_success_slots(self, excel_rule_count: int) -> int:
        rules = getattr(self.rule, "rules", [])
        if isinstance(rules, list):
            return min(excel_rule_count, len(rules))
        return excel_rule_count

    def _resolve_failed_slots(self) -> int:
        rules = getattr(self.rule, "rules", [])
        if isinstance(rules, list):
            return len(rules)
        return 0

    def _resolve_fallback_agent_rules(self, slots: int) -> List[str]:
        default_rules = [f"{self.document_type_name}-规则{i + 1}" for i in range(slots)]
        if not self.FALLBACK_AGENT_RULES:
            return default_rules
        preset = list(self.FALLBACK_AGENT_RULES)
        if len(preset) >= slots:
            return preset[:slots]
        return preset + default_rules[len(preset) :]

    def _configure_failed_slots(self) -> None:
        slots = self._resolve_failed_slots()
        self.rule_checkers = [self._run_fallback_rule] * slots
        self.agent_rules = self._resolve_fallback_agent_rules(slots)

    def _failed_conversion_rule(self, ith: int) -> AuditResult:
        return self.create_error_result(
            "PDF to Excel conversion failed, cannot run deterministic rule checker.",
            ith,
        )

    def preprocess_excel_path(self, path: str) -> str:
        return path

    def fallback_rule_checkers(self, ith: int) -> Optional[AuditResult]:
        return None

    def target_page_selector(self, target_keywords: List[str]) -> List[int]:
        found_pages = self._find_target_pages(
            target_keywords,
            strategies=[
                self._find_target_pages_by_textboxes,
                partial(
                    self._find_target_pages_by_text,
                    page_texts=self._get_pdf_page_texts(),
                ),
                partial(
                    self._find_target_pages_by_text,
                    page_texts=self._ocr_pdf_page_texts(),
                ),
            ],
        )
        return sorted(set(found_pages.values()))

    def get_images_for_keywords(self, target_keywords: List[str]) -> List[str]:
        target_page_indices = self.target_page_selector(target_keywords)
        if not target_page_indices:
            logger.warning(
                "No target pages found for keywords %s in %s, fallback to all pages",
                target_keywords,
                type(self).__name__,
            )
            return self._get_pdf_images_sync()
        return self._get_specific_page_images(target_page_indices)

    def _run_fallback_rule(self, ith: int) -> Optional[AuditResult]:
        fallback_result = self.fallback_rule_checkers(ith)
        if fallback_result is not None:
            return fallback_result
        return self._failed_conversion_rule(ith)

    def _collect_rule_checker_results(self) -> list[AuditResult]:
        results: list[AuditResult] = []
        for ith in range(len(self.rule_checkers)):
            result = self.check(ith)
            if result is not None:
                results.append(result)
        return results

    async def run(self) -> list[AuditResult]:
        if self.excel_agent:
            logger.debug(
                "%s delegated run() to %s",
                type(self).__name__,
                type(self.excel_agent).__name__,
            )
            excel_run = getattr(self.excel_agent, "run", None)
            if callable(excel_run):
                maybe_result = excel_run()
                if hasattr(maybe_result, "__await__"):
                    return await maybe_result
                return maybe_result
            return self._collect_rule_checker_results()

        if not self.rule_checkers:
            logger.error("No available rule checkers for %s", type(self).__name__)
            return [self.create_error_result("PDF to Excel conversion failed, cannot run audit.", 0)]

        return self._collect_rule_checker_results()


class AnnualTestRequirementsPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = AnnualTestRequirementsAgent


class ControlPlanPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = ControlPlanAgent


class DvpRPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = DvpRExcelAgent


class DpvrPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = DpvrExcelAgent


class EquipmentListPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = EquipmentListAgent


class InitialProcessCapacityPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = InitialProcessCapacityAgent


class MsaReportPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = MsaReportAgent


class PfmeaPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = PfmeaAgent


class PlantCapacityEvaluationPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = PlantCapacityEvaluationAgent


class ProcessFlowChartPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = ProcessFlowChartExcelAgent


class ProcessSpecialCharacteristicPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = ProcessSpecialCharacteristicAgent


class SubSupplierListPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = SubSupplierListExcelAgent


class ToolingApprovalPdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = ToolingApprovalAgent


class XirAssemblyIssuePdfExcelDelegatorAgent(PdfExcelDelegatorAgent):
    TARGET_EXCEL_AGENT_CLASS = XirAssemblyIssueAgent
