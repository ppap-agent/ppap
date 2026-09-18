"""
PPAP专用审核代理模块

本模块包含针对16种不同PPAP文档类型的专业审核代理。
每个代理都实现了特定的审核逻辑，结合AI和传统方法来提供准确的审核结果。
"""

# Import base classes first
from .base_agent import BaseAgent
from .excel_agent import ExcelAgent

# Import all specific agents
try:
    from .annual_test_requirements_agent import AnnualTestRequirementsAgent
    from .checking_fixture_approval_agent import CheckingFixtureApprovalAgent
    from .control_plan_agent import ControlPlanAgent
    from .dimension_measurement_pdf_agent import DimensionMeasurementPDFAgent
    from .dvp_r_excel_agent import DvpRExcelAgent
    from .equipment_list_agent import EquipmentListAgent
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
    from .psw_excel_agent import PswExcelAgent
    from .psw_pdf_agent import PswPDFAgent
    from .sub_supplier_list_excel_agent import SubSupplierListExcelAgent
    from .tooling_approval_agent import ToolingApprovalAgent
    from .xir_assembly_issue_agent import XirAssemblyIssueAgent
except ImportError as e:
    # Handle import errors gracefully during development
    import logging

    logging.getLogger(__name__).warning(f"Some agents could not be imported: {e}")

__all__ = [
    # Base classes
    "BaseAgent",
    "ExcelAgent",
    # Agent implementations
    "MsaReportAgent",
    "MsaReportPdfExcelDelegatorAgent",
    "DimensionMeasurementPDFAgent",
    "DvpRPdfExcelDelegatorAgent",
    "DvpRExcelAgent",
    "SubSupplierListExcelAgent",
    "SubSupplierListPdfExcelDelegatorAgent",
    "CheckingFixtureApprovalAgent",
    "ToolingApprovalAgent",
    "ToolingApprovalPdfExcelDelegatorAgent",
    "InitialProcessCapacityAgent",
    "InitialProcessCapacityPdfExcelDelegatorAgent",
    "ProcessFlowChartPdfExcelDelegatorAgent",
    "ProcessFlowChartExcelAgent",
    "PfmeaAgent",
    "PfmeaPdfExcelDelegatorAgent",
    "ControlPlanAgent",
    "ControlPlanPdfExcelDelegatorAgent",
    "ProcessSpecialCharacteristicAgent",
    "ProcessSpecialCharacteristicPdfExcelDelegatorAgent",
    "EquipmentListAgent",
    "EquipmentListPdfExcelDelegatorAgent",
    "PlantCapacityEvaluationAgent",
    "PlantCapacityEvaluationPdfExcelDelegatorAgent",
    "XirAssemblyIssueAgent",
    "XirAssemblyIssuePdfExcelDelegatorAgent",
    "AnnualTestRequirementsAgent",
    "AnnualTestRequirementsPdfExcelDelegatorAgent",
    "DpvrPdfExcelDelegatorAgent",
    "PswPDFAgent",
    "PswExcelAgent",
]
