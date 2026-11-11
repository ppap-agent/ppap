"""Explicit UAES source entry to actual customer Checker implementations."""

from .approval import UaesApprovalAgent
from .control_plan import UaesControlPlanAgent
from .engineering import UaesCapabilityAgent, UaesProcessFlowAgent
from .fmea import UaesPfmeaAgent
from .inspection import UaesInspectionAgent
from .materials import UaesMaterialsAgent
from .msa import UaesMsaAgent
from .records import (
    UaesControlledDocumentAgent,
    UaesEngineeringHistoryAgent,
    UaesLabQualificationAgent,
    UaesMeasurementComparisonAgent,
    UaesPackagingAgent,
    UaesPhotoAgent,
    UaesTraceabilityAgent,
    UaesTrialAgent,
)
from .special_characteristics import UaesSpecialCharacteristicsAgent
from .warrant import UaesWarrantAgent

CLASS_BY_ENTRY = {
    "U01": UaesInspectionAgent,
    "U04": UaesMaterialsAgent,
    "U05": UaesMsaAgent,
    "U07": UaesPfmeaAgent,
    "U08": UaesControlPlanAgent,
    "U17": UaesWarrantAgent,
    "U19": UaesApprovalAgent,
    "U02": UaesMeasurementComparisonAgent,
    "U03": UaesLabQualificationAgent,
    "U06": UaesCapabilityAgent,
    "U09": UaesPackagingAgent,
    "U10": UaesControlledDocumentAgent,
    "U11": UaesEngineeringHistoryAgent,
    "U12": UaesSpecialCharacteristicsAgent,
    "U13": UaesPhotoAgent,
    "U14": UaesControlledDocumentAgent,
    "U15": UaesTrialAgent,
    "U16": UaesProcessFlowAgent,
    "U18": UaesTraceabilityAgent,
}
