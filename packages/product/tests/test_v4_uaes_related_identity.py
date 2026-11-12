"""UAES pairs frozen original identities without relying on upload names."""

import json
from pathlib import Path

import pytest
from audit.checker_lib.uaes.fmea import UaesPfmeaAgent
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.kinds import PpapDocumentType
from audit.presets import preset_rule_body
from openpyxl import Workbook

PFMEA_HEADER = ["工序/功能/要求", "潜在失效模式", "潜在失效后果", "SEV严重度", "CLASS类别", "潜在失效原因", "OCC频度", "目前的探测控制", "探测度"]


def original(tmp_path, version, *, target=False, title="特殊特性清单", part="P1", customer="UAES", revision="A", project="M1", mark="CC"):
    path = tmp_path / (version + ".xlsx")
    book = Workbook()
    book.active.title = "PFMEA" if target else title
    rows = [["客户", customer], ["零件号", part], ["零件名称", "支架"], ["版本", revision], ["项目", project]]
    if target:
        rows += [PFMEA_HEADER, ["孔径", "孔径超差", "装配不良", 7, "CC", "刀具磨损", 3, "CMM", 4]]
    else:
        rows += [["序号", "特殊特性名称", "特性值", "特性类别"], [1, "孔径", "10±0.1", mark]]
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    return AuditInput(audit_id=version, file_url=str(path), file_metadata={"original_filename": "supplier-file.xlsx"})


def check(target, references, *, typed=True):
    body = preset_rule_body("UAES", "U07")
    group = GroupContextInput(
        by_audit_id={item.audit_id: item for item in [target, *references]},
        by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): references} if typed else {},
    )
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    return UaesPfmeaAgent(target, rule, group).check(2)


def test_renamed_original_uses_its_frozen_type_and_keeps_both_citations(tmp_path):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference")])
    assert result.status == "PASS", result.suggestions
    assert {json.loads(source.locator)["file_version_id"] for source in result.evidence.sources} == {"target", "reference"}


def test_untyped_original_can_neither_be_counted_nor_excluded(tmp_path):
    # Only a frozen type identifies a counterpart; an untyped original keeps the clause unknown, never NG.
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference")], typed=False)
    assert result.status == "UNABLE", result.suggestions


def test_filename_alone_cannot_turn_an_untyped_unrelated_original_into_a_counterpart(tmp_path):
    reference = original(tmp_path, "reference", title="其他报告")
    reference.file_metadata["original_filename"] = "特殊特性清单.xlsx"
    result = check(original(tmp_path, "target", target=True), [reference], typed=False)
    assert result.status == "UNABLE"


@pytest.mark.parametrize(("changes", "expected"), [({"part": "P2"}, "NG"), ({"project": "M2"}, "NG"), ({"project": None}, "UNABLE")])
def test_counterpart_must_share_the_identities_its_clause_names(tmp_path, changes, expected):
    # U07:3 asks for 同项目、同物料编号 and nothing else: another part or project is no counterpart
    # (the list is missing), an unstated project cannot be confirmed.
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference", **changes)])
    assert result.status == expected, result.suggestions


@pytest.mark.parametrize("changes", [{"customer": "理想"}, {"customer": None}, {"revision": "B"}, {"revision": None}])
def test_identities_the_clause_does_not_name_do_not_pair_files(tmp_path, changes):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference", **changes)])
    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize("part", ["无", "/", "TBD", "XXX", "待定"])
def test_matching_part_placeholders_never_establish_a_formal_pair(tmp_path, part):
    result = check(original(tmp_path, "target", target=True, part=part), [original(tmp_path, "reference", part=part)])
    assert result.status == "UNABLE"


@pytest.mark.parametrize(("second", "expected"), [("CC", "PASS"), ("SC", "NG")])
def test_every_typed_counterpart_is_reconciled(tmp_path, second, expected):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "one"), original(tmp_path, "two", mark=second)])
    assert result.status == expected, result.suggestions


def test_typed_and_untyped_counterparts_cannot_hide_a_second_candidate(tmp_path):
    target = original(tmp_path, "target", target=True)
    first, second = original(tmp_path, "one"), original(tmp_path, "two")
    body = preset_rule_body("UAES", "U07")
    group = GroupContextInput(
        by_audit_id={item.audit_id: item for item in [target, first, second]},
        by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): [first]},
    )
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    assert UaesPfmeaAgent(target, rule, group).check(2).status == "UNABLE"


@pytest.mark.parametrize("mention", ["参考特殊特性清单", "比对特殊特性清单", "核对特殊特性清单", "特殊特性清单需要另行提供"])
def test_title_that_only_mentions_a_reference_does_not_establish_identity(tmp_path, mention):
    reference = original(tmp_path, "reference", title="其他报告")
    from openpyxl import load_workbook

    book = load_workbook(reference.file_url)
    book.active.insert_rows(1)
    book.active.cell(1, 1, mention)
    book.save(reference.file_url)
    book.close()
    assert check(original(tmp_path, "target", target=True), [reference], typed=False).status == "UNABLE"


def test_reference_field_value_is_not_an_untyped_source_title(tmp_path):
    from openpyxl import load_workbook

    reference = original(tmp_path, "reference", title="其他报告")
    book = load_workbook(reference.file_url)
    book.active.insert_rows(1)
    book.active["A1"], book.active["B1"] = "比对文件", "特殊特性清单"
    book.save(reference.file_url)
    book.close()
    assert check(original(tmp_path, "target", target=True), [reference], typed=False).status == "UNABLE"


@pytest.mark.parametrize("title", ["特殊特性清单", "特殊特性清单 V2.1", "APQP-特殊特性清单", "Process special characteristic list", "特殊特性清单（Process special characteristic list）"])
@pytest.mark.parametrize("label", [None, "文件名称"])
def test_native_merged_title_does_not_identify_untyped_counterpart(tmp_path, title, label):
    from openpyxl import load_workbook

    reference = original(tmp_path, "reference", title="其他报告")
    book = load_workbook(reference.file_url)
    book.active.insert_rows(1)
    if label:
        book.active["A1"], book.active["B1"] = label, title
        book.active.merge_cells("B1:D1")
    else:
        book.active["A1"] = title
        book.active.merge_cells("A1:D1")
    book.save(reference.file_url)
    book.close()
    # A title read from an untyped original no longer identifies it: the clause stays unknown.
    result = check(original(tmp_path, "target", target=True), [reference], typed=False)
    assert result.status == "UNABLE", result.suggestions


def test_explicit_typed_counterpart_does_not_need_an_untyped_title_match(tmp_path):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference", title="供应商自定义表名")])
    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize("label", ["比对文件：", "比对文件", "Reference document:", "Reference document", "文件名称：", "Document title:", "供应商有限公司"])
def test_docx_native_title_keeps_split_field_context(tmp_path, label):
    from docx import Document

    document = Document()
    document.add_paragraph(label)
    document.add_paragraph("特殊特性清单")
    table = document.add_table(rows=0, cols=2)
    for values in [("客户", "UAES"), ("零件号", "P1"), ("零件名称", "支架"), ("版本", "A"), ("项目", "M1"), ("特性名称", "特性类别"), ("孔径", "CC")]:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = values
    path = tmp_path / "reference.docx"
    document.save(path)
    reference = AuditInput(audit_id="reference", file_url=str(path))
    target = original(tmp_path, "target", target=True)
    result = check(target, [reference], typed=False)
    assert result.status == "UNABLE", result.suggestions


def test_provided_unreadable_counterpart_is_an_execution_error(tmp_path):
    reference = original(tmp_path, "reference")
    Path(reference.file_url).write_bytes(b"not a workbook")
    result = check(original(tmp_path, "target", target=True), [reference])
    assert result.status == "ERR"


def test_valid_sample_photo_cannot_break_an_independent_typed_characteristic_pair(tmp_path):
    from PIL import Image

    target, reference = original(tmp_path, "target", target=True), original(tmp_path, "reference")
    path = tmp_path / "sample.png"
    Image.new("RGB", (16, 12), "white").save(path)
    photo = AuditInput(audit_id="photo", file_url=str(path))
    group = GroupContextInput(by_audit_id={"reference": reference, "photo": photo}, by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): [reference], "E07": [photo]})
    body = preset_rule_body("UAES", "U07")
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    result = UaesPfmeaAgent(target, rule, group).check(2)
    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize("unavailable_type", ["E07", "控制计划", "过程流程图", "Control plan", "T11"])
def test_unavailable_unrelated_type_does_not_break_a_readable_formal_pair(tmp_path, unavailable_type):
    target, reference = original(tmp_path, "target", target=True), original(tmp_path, "reference")
    group = GroupContextInput(
        by_audit_id={"reference": reference}, by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): [reference]}, unavailable_files={"other": unavailable_type}
    )
    body = preset_rule_body("UAES", "U07")
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    assert UaesPfmeaAgent(target, rule, group).check(2).status == "PASS"


@pytest.mark.parametrize("unavailable_type", [None, "unknown", "过程特殊特性清单", "T12"])
def test_unavailable_required_or_unclassified_type_keeps_its_execution_error(tmp_path, unavailable_type):
    target, reference = original(tmp_path, "target", target=True), original(tmp_path, "reference")
    group = GroupContextInput(
        by_audit_id={"reference": reference}, by_doc_type={PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST.get_type_name(): [reference]}, unavailable_files={"other": unavailable_type}
    )
    body = preset_rule_body("UAES", "U07")
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    assert UaesPfmeaAgent(target, rule, group).check(2).status == "ERR"


def test_msa_broad_type_keeps_its_actual_kappa_subtype_available(tmp_path):
    from audit.checker_lib.uaes.msa import UaesMsaAgent

    target = original(tmp_path, "target", target=True)
    reference = original(tmp_path, "reference", title="Kappa分析报告")
    group = GroupContextInput(
        by_audit_id={"target": target, "reference": reference},
        by_doc_type={T.get_type_name(): [reference] for T in [PpapDocumentType.MSA_REPORT]},
    )
    body = preset_rule_body("UAES", "U05")
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    agent = UaesMsaAgent(target, rule, group)
    # A broad MSA type still lends its Kappa report to the clauses that pair one.
    assert [report.where for report in agent.kappas(agent.rule_ctx(agent._load_workbook()))] == ["“Kappa分析报告”"]


@pytest.mark.parametrize(
    "field,value",
    [("part", "待确认"), ("part", "none"), ("part", "-"), ("part", "工程更改版本"), ("part", "ＴＢＤ"), ("project", "待确认")],
)
def test_equal_unknown_source_identity_cannot_certify_a_formal_pair(tmp_path, field, value):
    target = original(tmp_path, "target", target=True, **{field: value})
    reference = original(tmp_path, "reference", **{field: value})
    assert check(target, [reference]).status == "UNABLE"


@pytest.mark.parametrize("part", ["P-123", "P/123", "P.123", "000123"])
def test_valid_source_part_identity_retains_punctuation_and_leading_zeros(tmp_path, part):
    assert check(original(tmp_path, "target", target=True, part=part), [original(tmp_path, "reference", part=part)]).status == "PASS"


def test_a_clause_naming_the_project_needs_it_on_both_originals(tmp_path):
    assert check(original(tmp_path, "target", target=True, project=None), [original(tmp_path, "reference", project=None)]).status == "UNABLE"
