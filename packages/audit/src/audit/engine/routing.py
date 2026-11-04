"""Canonical doc-type inference and checker class routing."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional, Type
from urllib.parse import unquote, urlparse

from documents.files import get_file_extension_from_url

from audit.checkers.v2.annual_test_requirements_agent import (
    AnnualTestRequirementsAgent,
)
from audit.checkers.v2.base_agent import BaseAgent
from audit.checkers.v2.checking_fixture_approval_agent import (
    CheckingFixtureApprovalAgent,
)
from audit.checkers.v2.checking_fixture_approval_excel_agent import (
    CheckingFixtureApprovalExcelAgent,
)
from audit.checkers.v2.control_plan_agent import ControlPlanAgent
from audit.checkers.v2.dimension_measurement_excel_agent import (
    DimensionMeasurementExcelAgent,
)
from audit.checkers.v2.dimension_measurement_pdf_agent import (
    DimensionMeasurementPDFAgent,
)
from audit.checkers.v2.dpvr_excel_agent import DpvrExcelAgent
from audit.checkers.v2.dvp_r_excel_agent import DvpRExcelAgent
from audit.checkers.v2.equipment_list_agent import (
    EquipmentListAgent,
)
from audit.checkers.v2.equipment_list_docx_agent import (
    EquipmentListDocxAgent,
)
from audit.checkers.v2.initial_process_capacity_agent import (
    InitialProcessCapacityAgent,
)
from audit.checkers.v2.msa_report_agent import MsaReportAgent
from audit.checkers.v2.pdf_excel_delegator_agent import (
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
from audit.checkers.v2.pfmea_agent import PfmeaAgent
from audit.checkers.v2.plant_capacity_evaluation_agent import (
    PlantCapacityEvaluationAgent,
)
from audit.checkers.v2.process_flow_chart_excel_agent import (
    ProcessFlowChartExcelAgent,
)
from audit.checkers.v2.process_special_characteristic_agent import (
    ProcessSpecialCharacteristicAgent,
)
from audit.checkers.v2.psw_docx_agent import PswDocxAgent
from audit.checkers.v2.psw_excel_agent import PswExcelAgent
from audit.checkers.v2.psw_pdf_agent import PswPDFAgent
from audit.checkers.v2.sub_supplier_list_excel_agent import (
    SubSupplierListExcelAgent,
)
from audit.checkers.v2.tooling_approval_agent import (
    ToolingApprovalAgent,
)
from audit.checkers.v2.xir_assembly_issue_agent import (
    XirAssemblyIssueAgent,
)
from audit.checkers.v3 import (
    V3_OVERLAY_DOCX_AGENTS,
    V3_OVERLAY_EXCEL_AGENTS,
    V3_OVERLAY_PDF_AGENTS,
)
from audit.checkers.v4 import li_auto as li_auto_checkers
from audit.checkers.v4 import uaes as uaes_checkers
from audit.checkers.v4.nio.control_plan import NioControlPlanAgent
from audit.checkers.v4.nio.measurement import NioMeasurementAgent
from audit.checkers.v4.nio.pfmea import NioPfmeaAgent
from audit.checkers.v4.nio.process_flow import NioProcessFlowAgent
from audit.checkers.v4.nio.special_characteristics import NioSpecialCharacteristicsAgent
from audit.kinds import PpapDocumentType
from audit.rule_profile import RULE_PROFILE_V2, RULE_PROFILE_V3, RULE_PROFILE_V4, normalize_rule_profile

EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
LEGACY_DOC_EXTENSIONS = {".doc"}
AUDIT_TASK_EXTENSIONS = EXCEL_EXTENSIONS | PDF_EXTENSIONS | DOCX_EXTENSIONS | LEGACY_DOC_EXTENSIONS


class _AuditFileKind(str, Enum):
    """Canonical audit-file routing kinds."""

    EXCEL = "excel"
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    UNSUPPORTED = "unsupported"


def _audit_file_kind_from_extension(extension: str) -> _AuditFileKind:
    if extension in EXCEL_EXTENSIONS:
        return _AuditFileKind.EXCEL
    if extension in PDF_EXTENSIONS:
        return _AuditFileKind.PDF
    if extension == ".docx":
        return _AuditFileKind.DOCX
    if extension == ".doc":
        return _AuditFileKind.DOC
    return _AuditFileKind.UNSUPPORTED


def _resolve_audit_file_kind(file_url: str) -> _AuditFileKind:
    """Classify an audit file from its decoded URL or local-path suffix."""
    extension = get_file_extension_from_url(str(file_url)).lower()
    return _audit_file_kind_from_extension(extension)


def get_audit_file_compatibility_error(file_url: str) -> Optional[str]:
    """Return an actionable error when the current audit runtime cannot route a file."""
    decoded_path = unquote(urlparse(str(file_url)).path)
    extension = Path(decoded_path).suffix.lower()
    file_kind = _audit_file_kind_from_extension(extension)
    if file_kind in {_AuditFileKind.EXCEL, _AuditFileKind.PDF, _AuditFileKind.DOCX, _AuditFileKind.DOC}:
        return None

    filename = os.path.basename(decoded_path)
    display_name = filename or str(file_url)
    display_extension = extension or "无扩展名"
    return f"审核文件 '{display_name}' 使用不支持的审核格式 '{display_extension}'；当前仅支持 DOC、DOCX、PDF 或 Excel。"


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


def get_document_type_from_rule(rule: Any) -> Optional[PpapDocumentType]:
    """Infer PPAP document type from rule.type string."""
    rule_type = getattr(rule, "type", None)
    if not rule_type:
        return None

    doc_type = PpapDocumentType.from_string(str(rule_type))
    if doc_type == PpapDocumentType.UNKNOWN:
        return None
    return doc_type


def iter_supported_doc_types() -> Iterable[PpapDocumentType]:
    return set(V2_EXCEL_AGENTS.keys()) | set(V2_PDF_AGENTS.keys()) | set(V3_OVERLAY_EXCEL_AGENTS.keys()) | set(V3_OVERLAY_PDF_AGENTS.keys())


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
        checker: type[Any] | None = None
        if preset.customer == "UAES":
            candidate = uaes_checkers.CLASS_BY_ENTRY.get(preset.entry_id)
            checker = candidate if candidate and extension in candidate.SUPPORTED_EXTENSIONS else None
        elif preset.customer == "理想":
            carrier_map = (
                li_auto_checkers.EXCEL_AGENTS
                if extension in EXCEL_EXTENSIONS
                else li_auto_checkers.DOCX_AGENTS
                if extension in DOCX_EXTENSIONS
                else getattr(li_auto_checkers, "PDF_AGENTS", {})
                if extension in PDF_EXTENSIONS
                else {}
            )
            checker = carrier_map.get(preset.entry_id)
        elif preset.customer == "蔚来" and extension in EXCEL_EXTENSIONS | PDF_EXTENSIONS:
            checker = {"N01": NioMeasurementAgent, "N02": NioPfmeaAgent, "N03": NioControlPlanAgent, "N04": NioSpecialCharacteristicsAgent, "N05": NioProcessFlowAgent}.get(preset.entry_id)
        if checker is not None:
            return checker
        profile = RULE_PROFILE_V3
    file_kind = _resolve_audit_file_kind(file_url)
    primary_map = _pick_agent_map(file_kind, V2_EXCEL_AGENTS, V2_PDF_AGENTS, V2_DOCX_AGENTS)
    if primary_map is None:
        return None
    if profile == RULE_PROFILE_V3:
        overlay_map = _pick_agent_map(
            file_kind,
            V3_OVERLAY_EXCEL_AGENTS,
            V3_OVERLAY_PDF_AGENTS,
            V3_OVERLAY_DOCX_AGENTS,
        )
        return (overlay_map.get(doc_type) if overlay_map is not None else None) or primary_map.get(doc_type)
    return primary_map.get(doc_type)


EXCEL_AGENTS = V2_EXCEL_AGENTS
PDF_AGENTS = V2_PDF_AGENTS
DOCX_AGENTS = V2_DOCX_AGENTS
