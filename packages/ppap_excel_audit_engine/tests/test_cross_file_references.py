"""Current V3 reference extraction uses real files and preserves source identity."""

from types import SimpleNamespace

import fitz
import pytest
from openpyxl import Workbook
from ppap_core.contracts import AuditInput, ConversionArtifact, GroupContextInput, RuleInput
from ppap_core.types import PpapDocumentType
from ppap_excel_audit_engine.app_runtime import run_audit_engine
from ppap_excel_audit_engine.checkers.agents_v3_overlay.cross_file_extractors import extract_rows_from_file
from ppap_excel_audit_engine.runtime import bind_runtime

FLOW_FIELDS = {"operation_no": ["工序号"], "operation_name": ["工序名称"]}


def save_flow(path, operation=10):
    workbook = Workbook()
    workbook.active.title = "过程流程图"
    workbook.active.append(["工序号", "工序名称"])
    workbook.active.append([operation, "切割"])
    workbook.save(path)
    workbook.close()


def save_pdf(path, operation=10):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), f"Operation {operation}: Cutting")
        document.save(path)


@pytest.mark.parametrize("file_format", ["xlsx", "pdf"])
def test_reference_file_extracts_real_values_and_source_location_without_runtime(tmp_path, monkeypatch, file_format):
    source = tmp_path / f"flow.{file_format}"
    converted = tmp_path / "converted.xlsx"
    auxiliary = tmp_path / "conversion.html"
    if file_format == "xlsx":
        save_flow(source)
    else:
        save_pdf(source)
        save_flow(converted)
        auxiliary.write_text("conversion diagnostic", encoding="utf-8")

        def convert(file_ref, *, enable_cache=True):
            assert file_ref.location == str(source)
            assert enable_cache is True
            return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

        monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)
    original_bytes = source.read_bytes()

    with bind_runtime(None):
        rows = extract_rows_from_file(str(source), FLOW_FIELDS, required_fields=tuple(FLOW_FIELDS))

    assert rows == [{"operation_no": "10", "operation_name": "切割", "__sheet_name": "过程流程图", "__row_number": "2"}]
    assert not converted.exists()
    assert not auxiliary.exists()
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("conversion_state", ["rejected", "corrupt", "raised"])
def test_unreadable_pdf_reference_does_not_supply_rows(tmp_path, monkeypatch, conversion_state):
    source = tmp_path / "flow.pdf"
    save_pdf(source)
    original_bytes = source.read_bytes()
    converted = tmp_path / "converted.xlsx"
    auxiliary = tmp_path / "conversion.html"
    if conversion_state == "corrupt":
        converted.write_bytes(b"not an Excel workbook")
        auxiliary.write_text("conversion diagnostic", encoding="utf-8")

    def convert(file_ref, *, enable_cache=True):
        assert file_ref.location == str(source)
        assert enable_cache is True
        if conversion_state == "rejected":
            return ConversionArtifact(success=False, diagnostics={"error_code": "conversion_unavailable"})
        if conversion_state == "raised":
            raise OSError(f"private-converter-token: {file_ref.location}")
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)

    with bind_runtime(None):
        with pytest.raises(OSError, match="^本轮冻结关联资料无法读取，不能完成本项核对。$"):
            extract_rows_from_file(str(source), FLOW_FIELDS, required_fields=tuple(FLOW_FIELDS))

    assert not converted.exists()
    assert not auxiliary.exists()
    assert source.read_bytes() == original_bytes


def test_two_level_characteristic_headers_preserve_only_data_and_original_row_numbers(tmp_path):
    source = tmp_path / "characteristics.xlsx"
    workbook = Workbook()
    sheet = workbook.active
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
    workbook.save(source)
    workbook.close()
    fields = {"char_class": ["特性分类"], "char_description": ["特性描述"], "char_value": ["特性值"], "special_type": ["特殊特性种类", "特殊特性类别"]}

    with bind_runtime(None):
        rows = extract_rows_from_file(str(source), fields, required_fields=tuple(fields))

    assert rows == [{"char_class": "产品", "char_description": "External Projection", "char_value": "预防性防错", "special_type": "法律法规", "__sheet_name": "特殊特性清单", "__row_number": "4"}]


@pytest.mark.parametrize(
    ("conversion_state", "operation", "expected"),
    [("valid", 10, "PASS"), ("valid", 99, "NG"), ("rejected", 10, "ERR"), ("corrupt", 10, "ERR"), ("raised", 10, "ERR"), ("missing-columns", 10, "NG")],
    ids=["matching-process", "different-process", "conversion-rejected", "corrupt-workbook", "converter-exception", "readable-missing-columns"],
)
@pytest.mark.parametrize("context_form", ["typed", "attributes"])
def test_app_runtime_compares_actual_pdf_reference_rows_and_cleans_all_conversion_files(tmp_path, monkeypatch, conversion_state, operation, expected, context_form):
    plan_path = tmp_path / "plan.xlsx"
    workbook = Workbook()
    workbook.active.title = "控制计划"
    workbook.active.append(["零件/工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"])
    workbook.active.append([10, "切割", "外观", None, "SC", "作业指导书", "连续/自检"])
    workbook.save(plan_path)
    workbook.close()
    source = tmp_path / "flow.pdf"
    save_pdf(source, operation)
    original_bytes = source.read_bytes()
    flow = AuditInput(audit_id="flow", file_url=str(source))
    group = GroupContextInput(task_group_id="submitted-group", by_doc_type={"过程流程图": [flow]}, by_audit_id={"flow": flow})
    if context_form == "attributes":
        flow = SimpleNamespace(audit_id="flow", file_url=str(source))
        group = SimpleNamespace(task_group_id="submitted-group", by_doc_type={PpapDocumentType.PROCESS_FLOW_CHART: [flow]}, by_audit_id={"flow": flow})
    converted_paths = []
    seen_sources = []

    def convert(file_ref, *, enable_cache=True):
        assert enable_cache is True
        seen_sources.append(file_ref.location)
        if conversion_state == "rejected":
            return ConversionArtifact(success=False, diagnostics={"error_code": "conversion_unavailable"})
        if conversion_state == "raised":
            raise OSError(f"private-converter-token: {file_ref.location}")
        converted = tmp_path / f"converted-{len(seen_sources)}.xlsx"
        if conversion_state == "corrupt":
            converted.write_bytes(b"not an Excel workbook")
        elif conversion_state == "missing-columns":
            workbook = Workbook()
            workbook.active.append(["说明"])
            workbook.active.append(["可读文件，但没有工序列"])
            workbook.save(converted)
            workbook.close()
        else:
            save_flow(converted, operation)
        auxiliary = converted.with_suffix(".html")
        auxiliary.write_text("conversion diagnostic", encoding="utf-8")
        converted_paths.extend([converted, auxiliary])
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    # Use the real app runtime, pipeline, parser and checker. Only external
    # conversion is controlled; each request gets its own temporary artifact.
    monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)
    rules = RuleInput(
        rule_id="confirmed-plan-rules",
        rule_profile="v3",
        type="控制计划",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 12)],
    )

    results = run_audit_engine(AuditInput(audit_id="plan", file_url=str(plan_path)), rules, group)

    assert len(results) == 11
    comparison = results[1]
    assert comparison.status == expected
    # This readable fixture lacks the separate characteristic column. Its
    # independent business NG must survive an unrelated reference failure.
    assert results[3].status == "NG"
    assert "未找到" in results[3].suggestions
    if operation == 99:
        assert "PFD 工序在控制计划中未找到对应" in comparison.suggestions
        assert "第2行" in comparison.suggestions
    elif expected == "ERR":
        assert "本轮冻结关联资料无法读取，不能完成本项核对。" in comparison.suggestions
    elif conversion_state == "missing-columns":
        assert "过程流程图无法识别必要列结构" in comparison.suggestions
    assert all(str(source) not in row.suggestions and "private-converter-token" not in row.suggestions for row in results)
    assert seen_sources and set(seen_sources) == {str(source)}
    assert all(not path.exists() for path in converted_paths)
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("context_form", ["typed", "attributes"])
@pytest.mark.parametrize(
    ("unavailable", "expected"),
    [({}, "NG"), ({"unreadable-flow": "过程流程图"}, "ERR"), ({"unknown-unreadable": None}, "ERR")],
    ids=["not-provided", "unreadable-related-file", "unreadable-unknown-type"],
)
def test_public_runtime_preserves_unavailable_context_without_reclassifying_business_absence(tmp_path, context_form, unavailable, expected):
    plan_path = tmp_path / "control-plan.xlsx"
    workbook = Workbook()
    workbook.active.title = "控制计划"
    workbook.active.append(["零件/工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"])
    workbook.active.append([10, "切割", "外观", None, "SC", "作业指导书", "连续/自检"])
    workbook.save(plan_path)
    workbook.close()
    original = plan_path.read_bytes()
    group = GroupContextInput(task_group_id="frozen-group", unavailable_files=unavailable)
    if context_form == "attributes":
        group = SimpleNamespace(task_group_id="frozen-group", by_doc_type={}, by_audit_id={}, rules_by_id={}, unavailable_files=unavailable)
    rules = RuleInput(
        rule_id="confirmed-plan-rules",
        rule_profile="v3",
        type="控制计划",
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 12)],
    )

    results = run_audit_engine(AuditInput(audit_id="plan", file_url=str(plan_path)), rules, group)

    assert len(results) == 11
    assert results[1].status == expected
    assert ("本轮冻结关联资料无法读取" if unavailable else "缺少关联文档") in results[1].suggestions
    assert results[3].status == "NG"
    assert "未找到" in results[3].suggestions
    assert group.unavailable_files == unavailable
    assert plan_path.read_bytes() == original


@pytest.mark.parametrize(
    ("document_type", "reference_type", "affected_rules", "rule_count", "independent_rule", "independent_status"),
    [
        ("控制计划", PpapDocumentType.PROCESS_FLOW_CHART, (8,), 11, 4, "NG"),
        ("控制计划", PpapDocumentType.PFMEA, (10,), 11, 4, "NG"),
        ("控制计划", PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST, (9,), 11, 4, "NG"),
        ("潜在过程失效模式及后果分析", PpapDocumentType.PROCESS_FLOW_CHART, (6, 7), 7, 1, "NG"),
        ("过程流程图", PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST, (9,), 9, 4, "PASS"),
    ],
    ids=["plan-flow", "plan-pfmea", "plan-characteristics", "pfmea-flow", "flow-characteristics"],
)
@pytest.mark.parametrize("conversion_state", ["rejected", "corrupt", "raised", "missing-columns", "not-provided"])
def test_public_runtime_preserves_reference_read_failures_across_direct_loader_consumers(
    tmp_path, monkeypatch, document_type, reference_type, affected_rules, rule_count, independent_rule, independent_status, conversion_state
):
    target = tmp_path / "target.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    if document_type == "控制计划":
        sheet.title = "控制计划"
        sheet.append(["零件/工序号", "工序名称", "产品", "过程", "特殊特性分类", "控制方法", "频率"])
        sheet.append([10, "切割", "外观", None, "SC", "作业指导书", "连续/自检"])
    elif document_type == "过程流程图":
        sheet.title = "过程流程图"
        sheet.append(["工序号", "工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别"])
        sheet.append([10, "注塑", "执行注塑", "外观", "SC", "注塑", "CC"])
    else:
        sheet.title = "PFMEA"
        sheet.append(["过程步骤", "特殊特性"])
        sheet.append(["10 注塑", "KPC"])
    workbook.save(target)
    workbook.close()
    original_target = target.read_bytes()
    source = tmp_path / "reference.pdf"
    save_pdf(source)
    original_reference = source.read_bytes()
    reference = AuditInput(audit_id="frozen-reference", file_url=str(source))
    group = GroupContextInput(task_group_id="frozen-group")
    if conversion_state != "not-provided":
        group = GroupContextInput(task_group_id="frozen-group", by_doc_type={reference_type.get_type_name(): [reference]}, by_audit_id={reference.audit_id: reference})
    seen_sources = []
    converted_paths = []

    def convert(file_ref, *, enable_cache=True):
        assert enable_cache is True
        seen_sources.append(file_ref.location)
        if conversion_state == "rejected":
            return ConversionArtifact(success=False, diagnostics={"error_code": "conversion_unavailable"})
        if conversion_state == "raised":
            raise OSError(f"private-converter-token: {file_ref.location}")
        converted = tmp_path / f"converted-{len(seen_sources)}.xlsx"
        if conversion_state == "corrupt":
            converted.write_bytes(b"not an Excel workbook")
        else:
            workbook = Workbook()
            workbook.active.title = reference_type.get_type_name()
            workbook.active.append(["说明"])
            workbook.active.append(["可读文件，但没有审核所需业务列"])
            workbook.save(converted)
            workbook.close()
        auxiliary = converted.with_suffix(".html")
        auxiliary.write_text("conversion diagnostic", encoding="utf-8")
        converted_paths.extend([converted, auxiliary])
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("ppap_pdf_to_excel.pipeline.convert_pdf_to_excel_auto", convert)
    rules = RuleInput(
        rule_id="confirmed-rules",
        rule_profile="v3",
        type=document_type,
        rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_count + 1)],
    )

    results = run_audit_engine(AuditInput(audit_id="target", file_url=str(target)), rules, group)

    assert len(results) == rule_count
    expected = "NG" if conversion_state in {"missing-columns", "not-provided"} else "ERR"
    for rule_number in affected_rules:
        assert results[rule_number - 1].status == expected
        if expected == "ERR":
            assert "本轮冻结关联资料无法读取，不能完成本项核对。" in results[rule_number - 1].suggestions
    assert results[independent_rule - 1].status == independent_status
    assert all(str(source) not in row.suggestions and "private-converter-token" not in row.suggestions for row in results)
    assert set(seen_sources) == (set() if conversion_state == "not-provided" else {str(source)})
    assert all(not path.exists() for path in converted_paths)
    assert target.read_bytes() == original_target
    assert source.read_bytes() == original_reference
