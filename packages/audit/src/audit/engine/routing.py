"""Historical class routing, not selection of a directory Checker Revision.

Consumers are source rule declarations and the explicit from-scratch reference
reader in agent.execution.checker_context. Frozen Suite execution uses Checker
Core and does not choose a class here on failure.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Type

from documents.files import get_file_extension_from_url

from audit.checker_lib import uaes as uaes_checkers
from audit.checker_lib.li.annual_test_requirements_agent import (
    AnnualTestRequirementsAgent,
)
from audit.checker_lib.li.base_agent import BaseAgent
from audit.checker_lib.li.checking_fixture_approval_agent import (
    CheckingFixtureApprovalAgent,
)
from audit.checker_lib.li.checking_fixture_approval_excel_agent import (
    CheckingFixtureApprovalExcelAgent,
)
from audit.checker_lib.li.control_plan_agent import ControlPlanAgent
from audit.checker_lib.li.dimension_measurement_excel_agent import (
    DimensionMeasurementExcelAgent,
)
from audit.checker_lib.li.dimension_measurement_pdf_agent import (
    DimensionMeasurementPDFAgent,
)
from audit.checker_lib.li.dpvr_excel_agent import DpvrExcelAgent
from audit.checker_lib.li.dvp_r_excel_agent import DvpRExcelAgent
from audit.checker_lib.li.equipment_list_agent import (
    EquipmentListAgent,
)
from audit.checker_lib.li.equipment_list_docx_agent import (
    EquipmentListDocxAgent,
)
from audit.checker_lib.li.initial_process_capacity_agent import (
    InitialProcessCapacityAgent,
)
from audit.checker_lib.li.msa_report_agent import MsaReportAgent
from audit.checker_lib.li.pdf_excel_delegator_agent import (
    AnnualTestRequirementsPdfExcelDelegatorAgent,
    ControlPlanPdfExcelDelegatorAgent,
    DpvrPdfExcelDelegatorAgent,
    DvpRPdfExcelDelegatorAgent,
    EquipmentListPdfExcelDelegatorAgent,
    InitialProcessCapacityPdfExcelDelegatorAgent,
    MsaReportPdfExcelDelegatorAgent,
    PfmeaPdfExcelDelegatorAgent,
    PlantCapacityEvaluationPdfExcelDelegatorAgent,
    ProcessFlowChartPdfExcelDelegatorAgent,
    ProcessSpecialCharacteristicPdfExcelDelegatorAgent,
    SubSupplierListPdfExcelDelegatorAgent,
    ToolingApprovalPdfExcelDelegatorAgent,
    XirAssemblyIssuePdfExcelDelegatorAgent,
)
from audit.checker_lib.li.pfmea_agent import PfmeaAgent
from audit.checker_lib.li.plant_capacity_evaluation_agent import (
    PlantCapacityEvaluationAgent,
)
from audit.checker_lib.li.process_flow_chart_excel_agent import (
    ProcessFlowChartExcelAgent,
)
from audit.checker_lib.li.process_special_characteristic_agent import (
    ProcessSpecialCharacteristicAgent,
)
from audit.checker_lib.li.psw_docx_agent import PswDocxAgent
from audit.checker_lib.li.psw_excel_agent import PswExcelAgent
from audit.checker_lib.li.psw_pdf_agent import PswPDFAgent
from audit.checker_lib.li.sub_supplier_list_excel_agent import (
    SubSupplierListExcelAgent,
)
from audit.checker_lib.li.tooling_approval_agent import (
    ToolingApprovalAgent,
)
from audit.checker_lib.li.xir_assembly_issue_agent import (
    XirAssemblyIssueAgent,
)
from audit.checker_lib.nio import (
    V3_OVERLAY_DOCX_AGENTS,
    V3_OVERLAY_EXCEL_AGENTS,
    V3_OVERLAY_PDF_AGENTS,
)
from audit.kinds import PpapDocumentType
from audit.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3, RULE_PROFILE_V4, normalize_rule_profile

EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}


class _AuditFileKind(str, Enum):
    """Canonical audit-file routing kinds."""

    EXCEL = "excel"
    PDF = "pdf"
    DOCX = "docx"
    UNSUPPORTED = "unsupported"


def _resolve_audit_file_kind(file_url: str) -> _AuditFileKind:
    """Classify an audit file from its decoded URL or local-path suffix."""
    extension = get_file_extension_from_url(str(file_url)).lower()
    if extension in EXCEL_EXTENSIONS:
        return _AuditFileKind.EXCEL
    if extension in PDF_EXTENSIONS:
        return _AuditFileKind.PDF
    if extension in DOCX_EXTENSIONS:
        return _AuditFileKind.DOCX
    return _AuditFileKind.UNSUPPORTED


V2_EXCEL_AGENTS: dict[PpapDocumentType, Type[BaseAgent]] = {
    PpapDocumentType.SUB_SUPPLIER_LIST: SubSupplierListExcelAgent,
    PpapDocumentType.PROCESS_FLOW_CHART: ProcessFlowChartExcelAgent,
    PpapDocumentType.CONTROL_PLAN: ControlPlanAgent,
    PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST: ProcessSpecialCharacteristicAgent,
    PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT: PlantCapacityEvaluationAgent,
    PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT: XirAssemblyIssueAgent,
    PpapDocumentType.MSA_REPORT: MsaReportAgent,
    PpapDocumentType.PSW: PswExcelAgent,
    PpapDocumentType.DIMENSION_MEASUREMENT_REPORT: DimensionMeasurementExcelAgent,
    PpapDocumentType.DVP_R: DvpRExcelAgent,
    PpapDocumentType.PFMEA: PfmeaAgent,
    PpapDocumentType.EQUIPMENT_LIST: EquipmentListAgent,
    PpapDocumentType.ANNUAL_TEST_REQUIREMENTS: AnnualTestRequirementsAgent,
    PpapDocumentType.TOOLING_APPROVAL_REPORT: ToolingApprovalAgent,
    PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY: InitialProcessCapacityAgent,
    PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT: CheckingFixtureApprovalExcelAgent,
    PpapDocumentType.DPVR: DpvrExcelAgent,
}

V2_PDF_AGENTS: dict[PpapDocumentType, Type[BaseAgent]] = {
    PpapDocumentType.PROCESS_FLOW_CHART: ProcessFlowChartPdfExcelDelegatorAgent,
    PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT: CheckingFixtureApprovalAgent,
    PpapDocumentType.PSW: PswPDFAgent,
    PpapDocumentType.DIMENSION_MEASUREMENT_REPORT: DimensionMeasurementPDFAgent,
    PpapDocumentType.SUB_SUPPLIER_LIST: SubSupplierListPdfExcelDelegatorAgent,
    PpapDocumentType.TOOLING_APPROVAL_REPORT: ToolingApprovalPdfExcelDelegatorAgent,
    PpapDocumentType.PFMEA: PfmeaPdfExcelDelegatorAgent,
    PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST: ProcessSpecialCharacteristicPdfExcelDelegatorAgent,
    PpapDocumentType.EQUIPMENT_LIST: EquipmentListPdfExcelDelegatorAgent,
    PpapDocumentType.MSA_REPORT: MsaReportPdfExcelDelegatorAgent,
    PpapDocumentType.PLANT_CAPACITY_EVALUATION_REPORT: PlantCapacityEvaluationPdfExcelDelegatorAgent,
    PpapDocumentType.CONTROL_PLAN: ControlPlanPdfExcelDelegatorAgent,
    PpapDocumentType.ANNUAL_TEST_REQUIREMENTS: AnnualTestRequirementsPdfExcelDelegatorAgent,
    PpapDocumentType.DVP_R: DvpRPdfExcelDelegatorAgent,
    PpapDocumentType.DPVR: DpvrPdfExcelDelegatorAgent,
    PpapDocumentType.INITIAL_PROCESS_CAPACITY_STUDY: InitialProcessCapacityPdfExcelDelegatorAgent,
    PpapDocumentType.XIR_ASSEMBLY_ISSUE_REPORT: XirAssemblyIssuePdfExcelDelegatorAgent,
}

V2_DOCX_AGENTS: dict[PpapDocumentType, Type[BaseAgent]] = {
    PpapDocumentType.EQUIPMENT_LIST: EquipmentListDocxAgent,
    PpapDocumentType.PSW: PswDocxAgent,
}


def _pick_agent_map(
    file_kind: _AuditFileKind,
    excel_agents: dict[PpapDocumentType, type[Any]],
    pdf_agents: dict[PpapDocumentType, type[Any]],
    docx_agents: dict[PpapDocumentType, type[Any]],
) -> Optional[dict[PpapDocumentType, type[Any]]]:
    if file_kind is _AuditFileKind.PDF:
        return pdf_agents
    if file_kind is _AuditFileKind.EXCEL:
        return excel_agents
    if file_kind is _AuditFileKind.DOCX:
        return docx_agents
    return None


def resolve_engine_agent_class(
    doc_type: PpapDocumentType,
    file_url: str,
    rule_profile: str = RULE_PROFILE_V2,
    *,
    preset_key: str | None = None,
    preset_customer: str | None = None,
) -> Optional[type[Any]]:
    profile = normalize_rule_profile(rule_profile)
    if profile == RULE_PROFILE_V4:
        from audit.presets import preset_document_type, preset_rule

        preset = preset_rule(preset_customer, preset_key)
        expected_type = PpapDocumentType.from_string(preset_document_type(preset))
        if doc_type != expected_type:
            raise ValueError("preset_document_type_mismatch")
        extension = get_file_extension_from_url(str(file_url)).lower()
        if preset.customer == "UAES":
            candidate = uaes_checkers.CLASS_BY_ENTRY.get(preset.entry_id)
            return candidate if candidate and extension in candidate.SUPPORTED_EXTENSIONS else None
        # Li Auto runs the li implementations and NIO the nio ones, by document type.
        profile = RULE_PROFILE_V3 if preset.customer == "蔚来" else RULE_PROFILE_V2
    file_kind = _resolve_audit_file_kind(file_url)
    if profile == RULE_PROFILE_V3:
        overlay_map = _pick_agent_map(
            file_kind,
            V3_OVERLAY_EXCEL_AGENTS,
            V3_OVERLAY_PDF_AGENTS,
            V3_OVERLAY_DOCX_AGENTS,
        )
        # Explicit v3 uses its overlay only. A miss does not select the v2 class.
        return overlay_map.get(doc_type) if overlay_map is not None else None
    primary_map = _pick_agent_map(file_kind, V2_EXCEL_AGENTS, V2_PDF_AGENTS, V2_DOCX_AGENTS)
    if primary_map is None:
        return None
    return primary_map.get(doc_type)
