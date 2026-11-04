"""00-A §4.1 availability is free, scoped, and never an engineering verdict."""

import json

import pytest
from audit.contracts import AuditInput, GroupContextInput
from audit.kinds import PpapDocumentType as T
from audit.precheck_dependencies import dependency_facts
from openpyxl import Workbook


def original(tmp_path, version, *, title="报告", customer="UAES", part="P1", revision="A", msa="属性型", extra=None, project="M1"):
    book = Workbook()
    book.active.title = title
    for row in [["客户", customer], ["零件号", part], ["零件名称", "支架"], ["版本", revision], ["项目", project], ["MSA类型", msa], ["实验室名称", "实验室A"], ["UAES批准的前提/备注", "条件认可"]]:
        book.active.append(row)
    if extra:
        book.create_sheet(extra).append(["项目", "附件"])
    path = tmp_path / (version + ".xlsx")
    book.save(path)
    book.close()
    return AuditInput(audit_id=version, file_url=str(path), file_metadata={"original_filename": "supplier-file.xlsx"})


def facts(audit, entry, group):
    return {row["id"].split(":dependency:")[1]: row for row in dependency_facts(audit, entry, group)}


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("U08", {"drawing", "pfd", "pfmea", "pgqp1"}),
        ("U07", {"characteristics", "pgqp1"}),
        ("U05", {"pgqp1", "control-plan", "kappa"}),
        ("U06", {"pgqp1"}),
        ("U04", {"drawing"}),
        ("U01", {"bubble"}),
        ("U19", {"deviation"}),
        ("L07", {"pfd"}),
        ("L17", {"delivery-list"}),
        ("N03", {"pfd", "characteristics", "pfmea"}),
        ("N02", {"pfd"}),
        ("N05", {"characteristics"}),
    ],
)
def test_each_source_dependency_distinguishes_customer_and_supplier_missing(tmp_path, entry, expected):
    result = facts(original(tmp_path, "target"), entry, GroupContextInput())
    assert set(result) == expected
    for name, observed in result.items():
        assert observed["status"] == "not_checked"
        assert observed["code"] == ("S04-C" if name == "drawing" else "S04-S")
        assert observed["warning_codes"] == ([] if name == "drawing" else ["W01"])
        assert observed["critical"] is False and observed["certified"] is False


@pytest.mark.parametrize("typed", [True, False])
def test_current_original_is_found_by_type_or_real_title_independent_of_filename(tmp_path, typed):
    target = original(tmp_path, "target", customer="蔚来")
    reference = original(tmp_path, "reference", title="过程流程图", customer="蔚来")
    group = GroupContextInput(by_audit_id={"reference": reference}, by_doc_type={T.PROCESS_FLOW_CHART.get_type_name(): [reference]} if typed else {})
    result = facts(target, "N02", group)["pfd"]
    assert result["status"] == "satisfied", result
    assert result["code"] is None and result["certified"] is False
    assert {json.loads(row["locator"])["file_version_id"] for row in result["evidence"]["sources"]} == {"target", "reference"}


@pytest.mark.parametrize("extra", ["生产件批准交付物文件清单", "PSW交付物清单"])
def test_embedded_delivery_list_is_not_falsely_missing(tmp_path, extra):
    target = original(tmp_path, "target", customer="理想", extra=extra)
    result = facts(target, "L17", GroupContextInput())["delivery-list"]
    assert result["status"] == "satisfied", result


def test_embedded_deviation_summary_counts_as_accessible_original(tmp_path):
    target = original(tmp_path, "target", extra="偏差认可汇总清单")
    assert facts(target, "U19", GroupContextInput())["deviation"]["status"] == "satisfied"


@pytest.mark.parametrize(("remarks", "status", "code"), [("完全认可", "not_checked", "S02"), ("尚未批准", "unknown", None), ("完全认可；条件认可", "unknown", None), (None, "unknown", None)])
def test_deviation_list_is_only_required_for_confirmed_conditional_approval(tmp_path, remarks, status, code):
    from openpyxl import load_workbook

    target = original(tmp_path, "target")
    book = load_workbook(target.file_url)
    book.active.cell(8, 2).value = remarks
    book.save(target.file_url)
    book.close()
    observed = facts(target, "U19", GroupContextInput())["deviation"]
    assert (observed["status"], observed["code"]) == (status, code)
    assert observed["warning_codes"] == []


@pytest.mark.parametrize("changes", [{"part": "other"}, {"customer": "理想"}, {"revision": "B"}])
def test_wrong_identity_is_unknown_without_missing_warning(tmp_path, changes):
    target = original(tmp_path, "target")
    reference = original(tmp_path, "reference", title="过程流程图", **changes)
    result = facts(target, "U08", GroupContextInput(by_doc_type={T.PROCESS_FLOW_CHART.get_type_name(): [reference]}))["pfd"]
    assert result["status"] == "unknown" and result["code"] is None and result["warning_codes"] == []


def test_multiple_candidates_do_not_choose_a_correct_looking_first_original(tmp_path):
    group = GroupContextInput(by_doc_type={T.PROCESS_FLOW_CHART.get_type_name(): [original(tmp_path, "one"), original(tmp_path, "two")]})
    result = facts(original(tmp_path, "target"), "U08", group)["pfd"]
    assert result["status"] == "unknown" and result["warning_codes"] == []


def test_provided_unreadable_original_is_error_instead_of_missing(tmp_path):
    target = original(tmp_path, "target")
    group = GroupContextInput(unavailable_files={"reference": T.PROCESS_FLOW_CHART.get_type_name()})
    observed = facts(target, "U08", group)["pfd"]
    assert observed["status"] == "error" and observed["code"] is None and observed["warning_codes"] == []


def test_unclassified_original_or_unknown_collection_does_not_prove_missing(tmp_path):
    target = original(tmp_path, "target")
    for group in [None, GroupContextInput(by_audit_id={"other": original(tmp_path, "other")})]:
        assert facts(target, "U08", group)["drawing"]["status"] == "unknown"


def test_source_outside_this_context_is_not_discovered_and_filename_is_not_identity(tmp_path):
    target = original(tmp_path, "target")
    original(tmp_path, "foreign", title="过程流程图")
    group = GroupContextInput()
    assert facts(target, "U08", group)["pfd"]["code"] == "S04-S"
    unrelated = original(tmp_path, "unrelated")
    unrelated.file_metadata["original_filename"] = "过程流程图.xlsx"
    assert facts(target, "U08", GroupContextInput(by_audit_id={"unrelated": unrelated}))["pfd"]["status"] == "unknown"


def test_kappa_needs_explicit_attribute_scope_and_lab_dvr_is_not_a_missing_obligation(tmp_path):
    for msa, status, code in [("计量型", "not_checked", "S02"), ("未确认", "unknown", None)]:
        result = facts(original(tmp_path, "target", msa=msa), "U05", GroupContextInput())["kappa"]
        assert (result["status"], result["code"]) == (status, code)
        assert result["warning_codes"] == []
    result = facts(original(tmp_path, "target"), "U03", GroupContextInput())["dvr"]
    assert result["status"] == "handoff" and result["code"] == "S03" and result["warning_codes"] == []


def test_matching_placeholders_do_not_establish_part_identity(tmp_path):
    target = original(tmp_path, "target", part="无")
    reference = original(tmp_path, "reference", part="无", title="过程流程图")
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "unknown"


def test_adjacent_field_labels_are_not_matching_part_numbers(tmp_path):
    target = original(tmp_path, "target", part="零件名称")
    reference = original(tmp_path, "reference", part="零件名称", title="过程流程图")
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "unknown"


def test_a_document_type_in_a_metadata_value_is_not_a_source_title(tmp_path):
    target = original(tmp_path, "target", part="过程流程图")
    reference = original(tmp_path, "reference", part="过程流程图", title="其他报告")
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "unknown"


def test_valid_unrelated_sample_photo_does_not_break_text_counterpart_checks(tmp_path):
    from PIL import Image

    path = tmp_path / "sample.png"
    Image.new("RGB", (16, 12), "white").save(path)
    photo = AuditInput(audit_id="photo", file_url=str(path))
    group = GroupContextInput(by_audit_id={"photo": photo}, by_doc_type={"E07": [photo]})
    observed = facts(original(tmp_path, "target"), "U08", group)
    assert all(row["status"] == "not_checked" for row in observed.values())
    assert observed["drawing"]["code"] == "S04-C" and observed["pfd"]["code"] == "S04-S"


def test_title_referring_to_another_document_does_not_establish_source_identity(tmp_path):
    target = original(tmp_path, "target")
    reference = original(tmp_path, "reference", title="非过程流程图")
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "unknown" and result["code"] is None


def test_material_drawing_pair_requires_the_source_project_identity(tmp_path):
    from openpyxl import load_workbook

    target = original(tmp_path, "target")
    reference = original(tmp_path, "reference", title="客户图纸")
    for item in (target, reference):
        book = load_workbook(item.file_url)
        book.active.delete_rows(5)
        book.save(item.file_url)
        book.close()
    result = facts(target, "U04", GroupContextInput(by_audit_id={"reference": reference}))["drawing"]
    assert result["status"] == "unknown" and result["code"] is None


@pytest.mark.parametrize("field,value", [("part", "待确认"), ("part", "none"), ("part", "-"), ("part", "工程更改版本"), ("part", "ＴＢＤ"), ("revision", "版本号"), ("revision", "待确认"), ("project", "待确认")])
def test_equal_unknown_identity_does_not_establish_dependency_availability(tmp_path, field, value):
    target = original(tmp_path, "target", **{field: value})
    reference = original(tmp_path, "reference", title="过程流程图", **{field: value})
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "unknown"
    assert result["code"] is None and result["warning_codes"] == []


@pytest.mark.parametrize("part", ["P-123", "P/123", "P.123", "000123"])
def test_dependency_availability_preserves_real_part_identity(tmp_path, part):
    target = original(tmp_path, "target", part=part)
    reference = original(tmp_path, "reference", title="过程流程图", part=part)
    result = facts(target, "U08", GroupContextInput(by_audit_id={"reference": reference}))["pfd"]
    assert result["status"] == "satisfied"
