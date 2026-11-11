from __future__ import annotations

from types import SimpleNamespace

import pytest
from audit.checker_lib.li.control_plan_agent import (
    ControlPlanAgent as V2ControlPlanAgent,
)
from audit.checker_lib.li.dimension_measurement_excel_agent import (
    DimensionMeasurementExcelAgent,
)
from audit.checker_lib.li.equipment_list_docx_agent import (
    EquipmentListDocxAgent,
)
from audit.checker_lib.li.pdf_excel_delegator_agent import (
    ControlPlanPdfExcelDelegatorAgent,
)
from audit.checker_lib.li.psw_docx_agent import PswDocxAgent
from audit.checker_lib.nio.control_plan_agent import (
    ControlPlanAgent as V3OverlayControlPlanAgent,
)
from audit.checker_lib.nio.equipment_list_docx_agent import (
    EquipmentListDocxAgent as V3EquipmentListDocxAgent,
)
from audit.checker_lib.nio.pdf_excel_delegator_agent import (
    ControlPlanPdfExcelDelegatorAgent as V3ControlPlanPdfExcelDelegatorAgent,
)
from audit.checker_lib.nio.psw_docx_agent import (
    PswDocxAgent as V3PswDocxAgent,
)
from audit.engine.routing import (
    AUDIT_TASK_EXTENSIONS,
    get_document_type_from_rule,
    iter_supported_doc_types,
    resolve_engine_agent_class,
)
from audit.kinds import PpapDocumentType


def test_iter_supported_doc_types_contains_known_types() -> None:
    supported = set(iter_supported_doc_types())
    assert PpapDocumentType.CONTROL_PLAN in supported
    assert PpapDocumentType.PSW in supported
    assert PpapDocumentType.DIMENSION_MEASUREMENT_REPORT in supported


def test_audit_task_file_type_contract_includes_structured_docx() -> None:
    assert AUDIT_TASK_EXTENSIONS == {".doc", ".docx", ".pdf", ".xls", ".xlsm", ".xlsx"}
    assert ".ppt" not in AUDIT_TASK_EXTENSIONS
    assert ".pptx" not in AUDIT_TASK_EXTENSIONS


def test_resolve_engine_agent_class_pdf_path() -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.CONTROL_PLAN,
        "/tmp/test.pdf",
    )
    assert agent_cls is ControlPlanPdfExcelDelegatorAgent


def test_resolve_engine_agent_class_excel_path() -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.CONTROL_PLAN,
        "/tmp/test.xlsx",
    )
    assert agent_cls is V2ControlPlanAgent


def test_resolve_engine_agent_class_doc01_excel_path() -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.DIMENSION_MEASUREMENT_REPORT,
        "/tmp/1-全功能检验报告.xlsx",
    )
    assert agent_cls is DimensionMeasurementExcelAgent


def test_resolve_engine_agent_class_pdf_suffix_with_query() -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.CONTROL_PLAN,
        "http://example.com/a.pdf?sig=1",
    )
    assert agent_cls is ControlPlanPdfExcelDelegatorAgent


@pytest.mark.parametrize("rule_profile", ["v2", "v3"])
@pytest.mark.parametrize("file_url", ["/tmp/test.doc", "/tmp/test.unknown"])
def test_resolve_engine_agent_class_does_not_fall_back_for_unsupported_files(
    file_url: str,
    rule_profile: str,
) -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.CONTROL_PLAN,
        file_url,
        rule_profile,
    )
    assert agent_cls is None


@pytest.mark.parametrize(
    ("rule_profile", "expected_agent"),
    [("v2", EquipmentListDocxAgent), ("v3", V3EquipmentListDocxAgent)],
)
def test_resolve_equipment_docx_agent(
    rule_profile: str,
    expected_agent: type[object],
) -> None:
    assert (
        resolve_engine_agent_class(
            PpapDocumentType.EQUIPMENT_LIST,
            "/tmp/量检具、检测试验设备清单.docx",
            rule_profile,
        )
        is expected_agent
    )


@pytest.mark.parametrize(
    ("rule_profile", "expected_agent"),
    [("v2", PswDocxAgent), ("v3", V3PswDocxAgent)],
)
def test_resolve_psw_docx_agent(
    rule_profile: str,
    expected_agent: type[object],
) -> None:
    assert (
        resolve_engine_agent_class(
            PpapDocumentType.PSW,
            "/tmp/零件提交保证书.docx",
            rule_profile,
        )
        is expected_agent
    )


def test_docx_does_not_fall_back_to_an_unrelated_agent() -> None:
    assert (
        resolve_engine_agent_class(
            PpapDocumentType.CONTROL_PLAN,
            "/tmp/control-plan.docx",
        )
        is None
    )


def test_resolve_engine_agent_class_v3_overlay_prefers_overlay() -> None:
    agent_cls = resolve_engine_agent_class(
        PpapDocumentType.CONTROL_PLAN,
        "/tmp/test.xlsx",
        "v3",
    )
    assert agent_cls is V3OverlayControlPlanAgent


@pytest.mark.parametrize(
    ("file_url", "expected_agent"),
    [
        (
            "https://example.test/%E6%8E%A7%E5%88%B6%E8%AE%A1%E5%88%92.PDF?sig=1",
            ControlPlanPdfExcelDelegatorAgent,
        ),
        (
            "https://example.test/%E6%8E%A7%E5%88%B6%E8%AE%A1%E5%88%92.XLSM?sig=1",
            V2ControlPlanAgent,
        ),
    ],
)
def test_resolve_engine_agent_class_normalizes_encoded_urls(
    file_url: str,
    expected_agent: type[object],
) -> None:
    assert resolve_engine_agent_class(PpapDocumentType.CONTROL_PLAN, file_url) is expected_agent


@pytest.mark.parametrize(
    ("file_url", "rule_profile", "expected_agent"),
    [
        ("/tmp/test.xls", "v2", V2ControlPlanAgent),
        ("/tmp/test.xlsm", "v2", V2ControlPlanAgent),
        ("/tmp/test.xls", "v3", V3OverlayControlPlanAgent),
        ("/tmp/test.xlsm", "v3", V3OverlayControlPlanAgent),
        ("/tmp/test.pdf", "v3", V3ControlPlanPdfExcelDelegatorAgent),
    ],
)
def test_resolve_engine_agent_class_preserves_supported_routes(
    file_url: str,
    rule_profile: str,
    expected_agent: type[object],
) -> None:
    assert resolve_engine_agent_class(PpapDocumentType.CONTROL_PLAN, file_url, rule_profile) is expected_agent


def test_get_document_type_from_rule_cn_name() -> None:
    rule = SimpleNamespace(type="控制计划")
    assert get_document_type_from_rule(rule) == PpapDocumentType.CONTROL_PLAN


def test_get_document_type_from_rule_en_name() -> None:
    rule = SimpleNamespace(type="Control plan")
    assert get_document_type_from_rule(rule) == PpapDocumentType.CONTROL_PLAN


def test_get_document_type_from_rule_enum_name() -> None:
    rule = SimpleNamespace(type="PFMEA")
    assert get_document_type_from_rule(rule) == PpapDocumentType.PFMEA


@pytest.mark.parametrize(
    ("rule_type", "expected"),
    [
        ("全功能检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("尺寸检验报告", PpapDocumentType.DIMENSION_MEASUREMENT_REPORT),
        ("特殊特性清单", PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST),
        ("检具认可报告", PpapDocumentType.CHECKING_FIXTURE_APPROVAL_REPORT),
        ("测量系统分析报告", PpapDocumentType.MSA_REPORT),
        ("CP", PpapDocumentType.CONTROL_PLAN),
    ],
)
def test_get_document_type_from_rule_aliases(rule_type: str, expected: PpapDocumentType) -> None:
    rule = SimpleNamespace(type=rule_type)
    assert get_document_type_from_rule(rule) == expected


def test_get_document_type_from_rule_unknown() -> None:
    rule = SimpleNamespace(type="not-exists")
    assert get_document_type_from_rule(rule) is None


def test_get_document_type_from_rule_empty() -> None:
    rule = SimpleNamespace(type=None)
    assert get_document_type_from_rule(rule) is None
