"""v3 overlay agent registry (full takeover)."""

from __future__ import annotations

from audit.checker_lib.li.base_agent import BaseAgent
from audit.kinds import PpapDocumentType

from .annual_test_requirements_agent import AnnualTestRequirementsAgent
from .checking_fixture_approval_agent import CheckingFixtureApprovalAgent
from .checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from .control_plan_agent import ControlPlanAgent
from .dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from .dimension_measurement_pdf_agent import DimensionMeasurementPDFAgent
from .dpvr_excel_agent import DpvrExcelAgent
from .dvp_r_excel_agent import DvpRExcelAgent
from .equipment_list_agent import EquipmentListAgent
from .equipment_list_docx_agent import EquipmentListDocxAgent
from .initial_process_capacity_agent import InitialProcessCapacityAgent
from .msa_report_agent import MsaReportAgent
from .pdf_excel_delegator_agent import (
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
from .pfmea_agent import PfmeaAgent
from .plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from .process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from .process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
from .psw_docx_agent import PswDocxAgent
from .psw_excel_agent import PswExcelAgent
from .psw_pdf_agent import PswPDFAgent
from .sub_supplier_list_excel_agent import SubSupplierListExcelAgent
from .tooling_approval_agent import ToolingApprovalAgent
from .xir_assembly_issue_agent import XirAssemblyIssueAgent

V3_OVERLAY_EXCEL_AGENTS: dict[PpapDocumentType, type[BaseAgent]] = {
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


V3_OVERLAY_PDF_AGENTS: dict[PpapDocumentType, type[BaseAgent]] = {
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


V3_OVERLAY_DOCX_AGENTS: dict[PpapDocumentType, type[BaseAgent]] = {
    PpapDocumentType.EQUIPMENT_LIST: EquipmentListDocxAgent,
    PpapDocumentType.PSW: PswDocxAgent,
}
