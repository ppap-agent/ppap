from __future__ import annotations

import pytest
from audit.checker_lib.li import control_plan_agent as li_cp
from audit.checker_lib.li import dimension_measurement_excel_agent as li_dmr
from audit.checker_lib.li import equipment_list_docx_agent as li_equipment
from audit.checker_lib.li import pdf_excel_delegator_agent as li_pdf
from audit.checker_lib.li import psw_docx_agent as li_psw
from audit.checker_lib.nio import control_plan_agent as nio_cp
from audit.checker_lib.nio import equipment_list_docx_agent as nio_equipment
from audit.checker_lib.nio import pdf_excel_delegator_agent as nio_pdf
from audit.checker_lib.nio import psw_docx_agent as nio_psw
from audit.engine.routing import resolve_engine_agent_class
from audit.kinds import PpapDocumentType

CP = PpapDocumentType.CONTROL_PLAN


@pytest.mark.parametrize(
    ("doc_type", "file_url", "rule_profile", "expected"),
    [
        (CP, "/tmp/test.xlsx", "v2", li_cp.ControlPlanAgent),
        (CP, "/tmp/test.xls", "v2", li_cp.ControlPlanAgent),
        (CP, "https://example.test/%E6%8E%A7%E5%88%B6%E8%AE%A1%E5%88%92.XLSM?sig=1", "v2", li_cp.ControlPlanAgent),
        (CP, "https://example.test/%E6%8E%A7%E5%88%B6%E8%AE%A1%E5%88%92.PDF?sig=1", "v2", li_pdf.ControlPlanPdfExcelDelegatorAgent),
        (CP, "/tmp/test.xlsm", "v3", nio_cp.ControlPlanAgent),
        (CP, "/tmp/test.pdf", "v3", nio_pdf.ControlPlanPdfExcelDelegatorAgent),
        (PpapDocumentType.DIMENSION_MEASUREMENT_REPORT, "/tmp/1-全功能检验报告.xlsx", "v2", li_dmr.DimensionMeasurementExcelAgent),
        (PpapDocumentType.EQUIPMENT_LIST, "/tmp/量检具、检测试验设备清单.docx", "v2", li_equipment.EquipmentListDocxAgent),
        (PpapDocumentType.EQUIPMENT_LIST, "/tmp/量检具、检测试验设备清单.docx", "v3", nio_equipment.EquipmentListDocxAgent),
        (PpapDocumentType.PSW, "/tmp/零件提交保证书.docx", "v2", li_psw.PswDocxAgent),
        (PpapDocumentType.PSW, "/tmp/零件提交保证书.docx", "v3", nio_psw.PswDocxAgent),
        # Unsupported carriers never fall back to an unrelated agent.
        (CP, "/tmp/test.doc", "v2", None),
        (CP, "/tmp/test.unknown", "v3", None),
        (CP, "/tmp/control-plan.docx", "v2", None),
    ],
)
def test_routes(doc_type, file_url, rule_profile, expected) -> None:
    assert resolve_engine_agent_class(doc_type, file_url, rule_profile) is expected


@pytest.mark.parametrize(
    ("rule_type", "expected"),
    [
        ("控制计划", PpapDocumentType.CONTROL_PLAN),
        ("Control plan", PpapDocumentType.CONTROL_PLAN),
        ("PFMEA", PpapDocumentType.PFMEA),
        ("全功能检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("尺寸检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("特殊特性清单", PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST),
        ("检具认可报告", PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT),
        ("测量系统分析报告", PpapDocumentType.MSA_REPORT),
        ("CP", PpapDocumentType.CONTROL_PLAN),
        ("not-exists", PpapDocumentType.UNKNOWN),
        ("", PpapDocumentType.UNKNOWN),
    ],
)
def test_document_type_names_and_aliases(rule_type: str, expected: PpapDocumentType) -> None:
    assert PpapDocumentType.from_string(rule_type) is expected
