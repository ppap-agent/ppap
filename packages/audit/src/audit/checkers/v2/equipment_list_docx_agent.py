"""DOCX equipment-list audit using the structured Word grid."""

from audit.engine.checkers.docx_workbook_agent import (
    EQUIPMENT_DOCX_PRIMARY_TABLE,
    StructuredDocxWorkbookMixin,
)

from .equipment_list_agent import EquipmentListAgent


class EquipmentListDocxAgent(StructuredDocxWorkbookMixin, EquipmentListAgent):
    """Reuse deterministic V2 equipment rules over the Word grid view."""

    DOCX_PRIMARY_TABLE_CONTRACT = EQUIPMENT_DOCX_PRIMARY_TABLE
