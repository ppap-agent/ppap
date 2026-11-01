from pathlib import Path

import pytest
from audit.checkers.v3.pfmea_agent import PfmeaAgent
from audit.checkers.v3.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from audit.checkers.v3.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
from audit.contracts import (
    AuditInput,
    AuditResultItem,
    GroupContextInput,
    RuleInput,
)
from audit.engine.runtime import bind_runtime
from documents.contracts import ConversionArtifact
from openpyxl import Workbook


def saved_file(tmp_path: Path, name: str, workbook: Workbook) -> AuditInput:
    path = tmp_path / name
    workbook.save(path)
    workbook.close()
    return AuditInput(audit_id=name, file_url=str(path))


def check_workbook(tmp_path, agent_class, workbook, rule_number, group=None) -> AuditResultItem:
    agent = agent_class(
        saved_file(tmp_path, "target.xlsx", workbook),
        RuleInput(
            rule_id="confirmed-process-rules",
            rule_profile="v3",
            type=agent_class.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_number + 1)],
        ),
        group_context=group,
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


@pytest.mark.parametrize(
    ("extra_row", "missing_prevention", "expected"),
    [(False, False, "PASS"), (True, False, "PASS"), (False, True, "NG")],
    ids=["no-sequence-column", "ignore-unidentified-part", "missing-business-field"],
)
def test_characteristic_rows_require_fields_only_for_identified_parts(tmp_path, extra_row, missing_prevention, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "特殊特性清单"
    sheet.append(["零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数"])
    sheet.append(["P001", "part", "产品", "长度", "10", "SC", "关键", "首检", None if missing_prevention else "防错", "每批", "5"])
    if extra_row:
        sheet["C3"] = "产品"

    result = check_workbook(tmp_path, ProcessSpecialCharacteristicAgent, workbook, 2)

    assert result.status == expected
    if missing_prevention:
        assert "防错方案描述" in result.suggestions
        assert "行2" in result.suggestions


def flow_workbook() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别", "制造", "运输", "存储", "检验", "返工", "报废"])
    return workbook


def test_flow_header_requires_code_and_revision_date(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    for label, value in [
        ("零件号", "P001"),
        ("零件名称", "part"),
        ("项目车型", "model"),
        ("供应商名称", "supplier"),
        ("代码", None),
        ("编制日期", "2026-01-01"),
        ("修订日期", None),
        ("版本", "A"),
        ("团队", "team"),
    ]:
        sheet.append([label, value])

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 1)

    assert result.status == "NG"
    assert "代码字段" in result.suggestions
    assert "修订日期字段" in result.suggestions


@pytest.mark.parametrize(("rule_number", "category_cell", "category", "label"), [(2, "D2", "SC", "产品"), (3, "F2", "CC", "过程")])
def test_special_category_requires_its_corresponding_characteristic(tmp_path, rule_number, category_cell, category, label):
    workbook = flow_workbook()
    workbook.active["A2"] = "注塑"
    workbook.active[category_cell] = category

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, rule_number)

    assert result.status == "NG"
    assert label + "特性类别" in result.suggestions
    assert label + "特殊特性" in result.suggestions


@pytest.mark.parametrize("incoming", ["IQC", "进货检验入库", "原材料粒子检测", "原材料粒子检验", "外购件尺寸检测", "来料检验", "Incoming Inspection"])
def test_incoming_inspection_is_found_after_earlier_process_rows(tmp_path, incoming):
    workbook = flow_workbook()
    for process in ("备料", "上线", "清洗", incoming):
        workbook.active.append([process])

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 6)

    assert result.status == "PASS"


@pytest.mark.parametrize("process", ["原材料入库", "成品粒子检测", "装配质量检验", "原材料入库；成品尺寸检测", "原材料入库，成品质量检验", "原材料入库\n成品尺寸检测", "原材料入库\r\n成品质量检验"])
def test_material_storage_or_generic_inspection_is_not_incoming_inspection(tmp_path, process):
    workbook = flow_workbook()
    workbook.active.append([process])

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 6)

    assert result.status == "NG"


@pytest.mark.parametrize("shipment", ["包装发运", "成品发运", "发运至指定地点", "Shipment"])
def test_final_shipment_accepts_existing_synonyms(tmp_path, shipment):
    workbook = flow_workbook()
    workbook.active.append(["装配"])
    workbook.active.append([shipment])

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 7)

    assert result.status == "PASS"


@pytest.mark.parametrize(("next_process", "expected"), [("包装", "NG"), ("检验", "PASS")])
def test_rework_requires_inspection_in_the_next_row(tmp_path, next_process, expected):
    workbook = flow_workbook()
    for process in ("返工处理", next_process, "检验"):
        workbook.active.append([process])

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 8)

    assert result.status == expected
    if expected == "NG":
        assert "下一行不是检验或重检" in result.suggestions


@pytest.mark.parametrize(("flow_special", "expected"), [(None, "NG"), ("SC", "PASS")])
@pytest.mark.parametrize("reference_format", ["xlsx", "pdf"])
def test_pfmea_special_characteristic_is_checked_against_the_actual_flow_file(tmp_path, monkeypatch, flow_special, expected, reference_format):
    workbook = Workbook()
    workbook.active.title = "PFMEA"
    workbook.active.append(["过程步骤", "特殊特性"])
    workbook.active.append(["10 注塑", "KPC"])
    flow = Workbook()
    flow.active.title = "过程流程图"
    flow.active.append(["工序名称", "产品特性类别", "过程特性类别"])
    flow.active.append(["10 注塑", None, flow_special])
    related = saved_file(tmp_path, "flow.xlsx", flow)
    if reference_format == "pdf":
        converted = related.file_url
        source_pdf = str(tmp_path / "flow.pdf")

        def convert(file_ref, *, enable_cache=True):
            assert file_ref.location == source_pdf
            assert enable_cache is True
            return ConversionArtifact(success=True, xlsx_path=converted, temp_files=[converted])

        # Only the external converter is replaced; native loading, extraction,
        # cross-file matching and the PFMEA decision all run unchanged.
        monkeypatch.setattr("documents.conversion.pipeline.convert_pdf_to_excel_auto", convert)
        related = AuditInput(audit_id=related.audit_id, file_url=source_pdf)
    group = GroupContextInput(by_doc_type={"过程流程图": [related]}, by_audit_id={related.audit_id: related})

    with bind_runtime(None):
        result = check_workbook(tmp_path, PfmeaAgent, workbook, 7, group)

    assert result.status == expected
    if expected == "NG":
        assert "PFMEA过程步骤存在特殊特性" in result.suggestions
        assert "PFD对应工序未标注CC/SC" in result.suggestions
    if reference_format == "pdf":
        assert not (tmp_path / "flow.xlsx").exists()


def test_flow_cross_file_check_uses_current_file_and_ignores_characteristic_headers(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    for address, value in {"J7": "特殊特性类型", "J8": "产品特殊特性", "K8": "产品特性类别", "L8": "过程特殊特性", "M8": "过程特性类别", "J9": "重量(KPC)、外观、尺寸、壁厚"}.items():
        sheet[address] = value
    characteristics = Workbook()
    sheet = characteristics.active
    sheet.title = "特殊特性清单"
    for address, value in {
        "D2": "特殊特性",
        "K2": "特殊特性种类",
        "D3": "特性分类",
        "E3": "特性描述",
        "F3": "特性值",
        "D4": "产品",
        "E4": "External Projection",
        "F4": "预防性防错",
        "K4": "法律法规",
    }.items():
        sheet[address] = value
    related = saved_file(tmp_path, "characteristics.xlsx", characteristics)
    group = GroupContextInput(by_doc_type={"过程特殊特性清单": [related]}, by_audit_id={related.audit_id: related})

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 9, group)

    assert result.status == "NG"
    assert "缺少过程流程图" not in result.suggestions
    assert "External Projection" in result.suggestions
    assert "特性描述：特性值" not in result.suggestions
