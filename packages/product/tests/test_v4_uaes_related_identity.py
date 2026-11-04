"""UAES pairs frozen original identities without relying on upload names."""

import json
from pathlib import Path

import pytest
from audit.checkers.v4.uaes.fmea import UaesPfmeaAgent
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.kinds import PpapDocumentType
from audit.presets import preset_rule_body
from openpyxl import Workbook


def original(tmp_path, version, *, target=False, title="特殊特性清单", part="P1", customer="UAES", revision="A"):
    path = tmp_path / (version + ".xlsx")
    book = Workbook()
    book.active.title = "PFMEA" if target else title
    for row in [
        ["客户", customer],
        ["零件号", part],
        ["零件名称", "支架"],
        ["版本", revision],
        ["功能/要求", "特殊特性"] if target else ["特性名称", "特性类别"],
        ["孔径", "CC"],
    ]:
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


@pytest.mark.parametrize("typed", [True, False])
def test_renamed_original_uses_frozen_type_or_original_title_and_keeps_both_citations(tmp_path, typed):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference")], typed=typed)
    assert result.status == "PASS", result.suggestions
    assert {json.loads(source.locator)["file_version_id"] for source in result.evidence.sources} == {"target", "reference"}


def test_filename_alone_cannot_turn_an_untyped_unrelated_original_into_a_counterpart(tmp_path):
    reference = original(tmp_path, "reference", title="其他报告")
    reference.file_metadata["original_filename"] = "特殊特性清单.xlsx"
    result = check(original(tmp_path, "target", target=True), [reference], typed=False)
    assert result.status == "UNABLE"


@pytest.mark.parametrize("changes", [{"part": "P2"}, {"customer": "理想"}, {"revision": "B"}])
def test_typed_counterpart_still_requires_matching_original_identity(tmp_path, changes):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "reference", **changes)])
    assert result.status == "UNABLE"


def test_multiple_typed_counterparts_remain_unknown(tmp_path):
    result = check(original(tmp_path, "target", target=True), [original(tmp_path, "one"), original(tmp_path, "two")])
    assert result.status == "UNABLE"


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


def test_title_that_only_mentions_a_reference_does_not_establish_identity(tmp_path):
    reference = original(tmp_path, "reference", title="其他报告")
    from openpyxl import load_workbook

    book = load_workbook(reference.file_url)
    book.active.insert_rows(1)
    book.active.cell(1, 1, "参考特殊特性清单")
    book.save(reference.file_url)
    book.close()
    assert check(original(tmp_path, "target", target=True), [reference], typed=False).status == "UNABLE"


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


def test_msa_broad_type_keeps_its_actual_kappa_subtype_available(tmp_path):
    from audit.checkers.v4.uaes.msa import UaesMsaAgent

    target = original(tmp_path, "target", target=True)
    reference = original(tmp_path, "reference", title="Kappa分析报告")
    group = GroupContextInput(
        by_audit_id={"target": target, "reference": reference},
        by_doc_type={T.get_type_name(): [reference] for T in [PpapDocumentType.MSA_REPORT]},
    )
    body = preset_rule_body("UAES", "U05")
    rule = RuleInput(rule_id="frozen-rule", **{name: value for name, value in body.items() if name != "kind"})
    assert UaesMsaAgent(target, rule, group).related(("kappa",)).version == "reference"
