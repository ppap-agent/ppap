"""Explicit Li Auto carriers; routing policy remains in engine.routing."""

from .annual_tests import LiAutoAnnualTestsAgent
from .capacity_study import LiAutoCapacityStudyAgent
from .control_plan import LiAutoControlPlanAgent
from .equipment import LiAutoEquipmentAgent
from .fixtures import LiAutoFixtureAgent
from .inspection import LiAutoInspectionAgent
from .issues import LiAutoIssuesAgent
from .materials import LiAutoMaterialsAgent
from .msa import LiAutoMsaAgent
from .pfmea import LiAutoPfmeaAgent
from .plant_capacity import LiAutoPlantCapacityAgent
from .process_flow import LiAutoProcessFlowAgent
from .psw import LiAutoPswAgent, LiAutoPswDocxAgent
from .special_characteristics import LiAutoSpecialCharacteristicsAgent
from .tooling import LiAutoToolingAgent
from .validation_plan import LiAutoValidationPlanAgent
from .verification_report import LiAutoVerificationReportAgent

EXCEL_AGENTS = {
    "L01": LiAutoInspectionAgent,
    "L02": LiAutoValidationPlanAgent,
    "L03": LiAutoVerificationReportAgent,
    "L04": LiAutoMaterialsAgent,
    "L05": LiAutoMsaAgent,
    "L06": LiAutoCapacityStudyAgent,
    "L07": LiAutoPfmeaAgent,
    "L08": LiAutoControlPlanAgent,
    "L09": LiAutoFixtureAgent,
    "L10": LiAutoToolingAgent,
    "L11": LiAutoProcessFlowAgent,
    "L12": LiAutoSpecialCharacteristicsAgent,
    "L13": LiAutoEquipmentAgent,
    "L14": LiAutoPlantCapacityAgent,
    "L15": LiAutoIssuesAgent,
    "L16": LiAutoAnnualTestsAgent,
    "L17": LiAutoPswAgent,
}

DOCX_AGENTS = {"L17": LiAutoPswDocxAgent}

# Verified untamplated PDF text/grid carrier; unread visual facts stay UNABLE.
PDF_AGENTS = {"L03": LiAutoVerificationReportAgent}
