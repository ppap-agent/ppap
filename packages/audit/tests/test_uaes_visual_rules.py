"""Source-owned UAES graphic rules keep native and original-image facts distinct."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import date, timedelta
from types import ModuleType, SimpleNamespace

import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image


def _workbook(sheets, *, drawing=False):
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    if drawing:
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "black").save(image, format="PNG")
        image.seek(0)
        book.worksheets[0].add_image(WorkbookImage(image), "B1")
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def _execute(tmp_path, monkeypatch, entry, clause, sheets, *, visual=None, error=None, drawing=False, peers=(), expected_sha=None):
    """Use the actual directory loader, factory adapter and frozen Core validator."""
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"UAES/{entry}")
    resources = ModuleType("resources")
    exec(compile(suite.files["resources.py"], f"{suite.source_path}/resources.py", "exec"), resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    checker = ModuleType(f"uaes_visual_test_{entry}")
    exec(compile(suite.files["checker.py"], f"{suite.source_path}/checker.py", "exec"), checker.__dict__)
    body = json.loads(suite.files["rules.json"])
    body["rules"] = [item for item in body["rules"] if item["id"] == clause]
    body["checker_binding"] = {clause: suite.entrypoints[clause]}
    assert len(body["rules"]) == 1
    content = _workbook(sheets, drawing=drawing)
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / (entry + ".xlsx")
    path.write_bytes(content)
    audit = AuditInput(audit_id="frozen-original", file_url=str(path), customer="UAES", project_id="project", file_metadata={"sha256": expected_sha or digest})
    rule = RuleInput(rule_id="frozen-rule", **{key: value for key, value in body.items() if key != "rule_id"})
    frozen = [FrozenInput(audit.audit_id, digest, "target", path.name)]
    by_type = {}
    for identity, kind, peer_sheets in peers:
        peer_path = tmp_path / (identity + ".xlsx")
        peer_content = _workbook(peer_sheets)
        peer_path.write_bytes(peer_content)
        peer = AuditInput(audit_id=identity, file_url=str(peer_path), customer="UAES", project_id="project", file_metadata={"sha256": hashlib.sha256(peer_content).hexdigest()})
        by_type.setdefault(kind, []).append(peer)
        frozen.append(FrozenInput(identity, hashlib.sha256(peer_content).hexdigest(), "reference", peer_path.name))
    group = GroupContextInput(by_doc_type=by_type, by_audit_id={peer.audit_id: peer for values in by_type.values() for peer in values})
    target = FrozenTarget(audit.audit_id, "revision", suite.suite_key, suite.revision_digest, body, RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"]))
    requests = []

    def observe(owner, fields, **_options):
        requests.append(dict(fields))
        if error:
            raise error
        values = {field: (visual or {}).get(field) for field in fields}
        for field, value in values.items():
            if value is not None:
                owner.witness({"kind": "xlsx_cell", "sheet": next(iter(sheets)), "cell": "B1"}, f"{field}={value}", visual=True)
        return values

    monkeypatch.setattr(SourceAudit, "visual_pdf", observe)
    opened = []
    context = SimpleNamespace(audit_file=audit, rule=rule, target=target, group_context=group, cache={}, on_close=opened.append, clause_id=clause)
    try:
        observed = check_factory(context, checker.create_audit).model_dump(mode="json")
        sources = (observed.get("evidence") or {}).get("sources", [])
        referenced = {audit.audit_id, *(json.loads(source["locator"])["file_version_id"] for source in sources)}
        observed["evidence_file_version_ids"] = sorted(referenced)
        checked = validate_results([observed], target=target, inputs=frozen)
        assert checked["coverage"] == [{"file_version_id": audit.audit_id, "clause_id": clause, "coverage": "mapped"}]
        result = checked["findings"][0]
        assert result["clause_id"] == clause
        if result["status"] == "PASS":
            assert result["evidence"]["sources"], result
        return result, requests
    finally:
        for opened_audit in reversed(opened):
            opened_audit.close()


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_comparison_requires_actual_four_party_signatures(tmp_path, monkeypatch, observed, expected):
    sheets = {"测量比较报告": [["测量比较报告"], ["供应商", "打印姓名"], ["UAES技术中心", "打印姓名"], ["UAES测量部门", "打印姓名"], ["UAES供应商质量开发工程师", "打印姓名"]]}
    result, requests = _execute(tmp_path, monkeypatch, "U02", "U02:1", sheets, visual={"four_signatures": observed})
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == {"four_signatures"}
    assert all(json.loads(source["locator"])["file_version_id"] == "frozen-original" for source in result["evidence"]["sources"])


@pytest.mark.parametrize("entry", ["U10", "U14"])
@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_controlled_stamp_labels_do_not_prove_an_actual_stamp(tmp_path, monkeypatch, entry, observed, expected):
    result, requests = _execute(tmp_path, monkeypatch, entry, f"{entry}:1", {"文件": [["受控章", "受控", "盖章处"]]}, visual={"controlled_stamp": observed})
    assert result["status"] == expected, result
    assert len(requests) == 1
    assert "真实盖上" in requests[0]["controlled_stamp"]
    assert "文字标签" in requests[0]["controlled_stamp"]


def test_visual_transport_failure_is_err_not_missing_stamp(tmp_path, monkeypatch):
    result, _requests = _execute(tmp_path, monkeypatch, "U10", "U10:1", {"文件": [["受控章"]]}, error=TimeoutError("provider timeout"))
    assert result["status"] == "ERR", result
    assert result["checks_performed"] == 0


def _trial(trial, inspection="☑合格 □不合格"):
    return {"试装单": [["流转记录", "流程结束", "流程完成"], ["试装结论", trial], ["检验结论", inspection]]}


@pytest.mark.parametrize(
    ("trial", "inspection", "expected"),
    [
        ("☑通过 □未通过", "☑合格 □不合格", "PASS"),
        ("通过☑ 未通过□", "合格☑ 不合格□", "PASS"),
        ("未通过☑", "☑合格 □不合格", "NG"),
        ("☑通过 □未通过", "不合格☑", "NG"),
        ("☑通过 ☑未通过", "☑合格 □不合格", "NG"),
        ("□通过 □未通过", "☑合格 □不合格", "NG"),
    ],
)
def test_trial_reads_complete_native_option_tokens(tmp_path, monkeypatch, trial, inspection, expected):
    result, requests = _execute(tmp_path, monkeypatch, "U15", "U15:1", _trial(trial, inspection))
    assert result["status"] == expected, result
    assert not requests, "Confirmed native checkbox facts do not need another provider call"


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_trial_image_overlay_is_not_inferred_from_native_empty_boxes(tmp_path, monkeypatch, observed, expected):
    result, requests = _execute(
        tmp_path,
        monkeypatch,
        "U15",
        "U15:1",
        _trial("□通过 □未通过", "□合格 □不合格"),
        drawing=True,
        visual={"trial_passed": observed, "inspection_passed": True},
    )
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == {"trial_passed", "inspection_passed"}


def test_trial_unmarked_labels_stay_unknown(tmp_path, monkeypatch):
    result, requests = _execute(tmp_path, monkeypatch, "U15", "U15:1", _trial("通过 未通过", "合格 不合格"))
    assert result["status"] == "UNABLE", result
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("facts", "expected"),
    [({"engineering_dates": True, "safety_one": True}, "PASS"), ({"engineering_dates": False, "safety_one": True}, "NG"), ({"engineering_dates": True, "safety_one": None}, "UNABLE")],
)
def test_warrant_dates_and_safety_require_their_own_original_observations(tmp_path, monkeypatch, facts, expected):
    rows = [[name, "已填写"] for name in ("零件名称", "客户零件号", "图纸号", "供应商零件号", "工程更改级别", "其他工程更改")]
    result, requests = _execute(tmp_path, monkeypatch, "U17", "U17:1", {"零件提交保证书": rows}, visual=facts)
    assert result["status"] == expected, result
    assert len(requests) == 1
    assert "各自对应" in requests[0]["engineering_dates"]


@pytest.mark.parametrize(("facts", "expected"), [({"handwriting": True, "seal": True}, "PASS"), ({"handwriting": False, "seal": True}, "NG"), ({"handwriting": True, "seal": None}, "UNABLE")])
def test_warrant_signature_and_stamp_cannot_borrow_printed_names(tmp_path, monkeypatch, facts, expected):
    result, requests = _execute(tmp_path, monkeypatch, "U17", "U17:3", {"零件提交保证书": [["供方代表签字", "打印姓名"], ["盖章处", "受控"]]}, visual=facts)
    assert result["status"] == expected, result
    assert "不能是打印姓名" in requests[0]["handwriting"]


def _material(choice):
    return {
        "零件提交保证书": [
            ["供方制造厂资料"],
            ["买方资料"],
            *[[name, "已填写"] for name in ("公司名称", "采购方及部门", "采购方代码", "应用", "以IMDS或者其它客户格式递交")],
            ["是否报告了受客户关注的材料信息", choice],
        ]
    }


@pytest.mark.parametrize(("choice", "expected"), [("☑是 □否", "PASS"), ("☑是 ☑否", "NG"), ("□是 □否", "NG"), ("是 否", "UNABLE")])
def test_warrant_material_requires_one_actual_choice(tmp_path, monkeypatch, choice, expected):
    result, _requests = _execute(tmp_path, monkeypatch, "U17", "U17:2", _material(choice))
    assert result["status"] == expected, result


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("☑其它----请说明", "NG"),
        ("其他☑ 请说明", "NG"),
        ("☑其他----请说明：更改工艺", "PASS"),
        ("☑其他----请说明 □初始提交", "NG"),
        ("☑初始提交 □其它----请说明", "PASS"),
        ("初始提交☑ 其它□----请说明", "UNABLE"),
        ("初始提交□ 其它☑----请说明", "UNABLE"),
        ("其它----请说明☑", "UNABLE"),
        ("□初始提交 □其它----请说明", "NG"),
    ],
)
def test_reason_other_selection_needs_scoped_explanation(tmp_path, monkeypatch, value, expected):
    result, _requests = _execute(tmp_path, monkeypatch, "U17", "U17:4", {"零件提交保证书": [["提交原因", value]]})
    assert result["status"] == expected, result


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"reason_selected": True, "other_selected": False, "other_explained": None}, "PASS"),
        ({"reason_selected": True, "other_selected": True, "other_explained": True}, "PASS"),
        ({"reason_selected": True, "other_selected": True, "other_explained": False}, "NG"),
        ({"reason_selected": True, "other_selected": None, "other_explained": None}, "UNABLE"),
        ({"reason_selected": False, "other_selected": False, "other_explained": None}, "NG"),
        ({"reason_selected": False, "other_selected": True, "other_explained": True}, "UNABLE"),
    ],
)
def test_reason_image_overlay_uses_only_required_boolean_facts(tmp_path, monkeypatch, facts, expected):
    result, requests = _execute(tmp_path, monkeypatch, "U17", "U17:4", {"零件提交保证书": [["提交原因", "□初始提交 □其它----请说明"]]}, visual=facts, drawing=True)
    assert result["status"] == expected, result
    assert len(requests) == 1


@pytest.mark.parametrize(("choice", "expected"), [("☑等级1 □等级2", "PASS"), ("☑等级1 ☑等级2", "NG"), ("□等级1 □等级2", "NG"), ("等级1 等级2", "UNABLE")])
def test_warrant_level_requires_exactly_one_selection(tmp_path, monkeypatch, choice, expected):
    result, _requests = _execute(tmp_path, monkeypatch, "U17", "U17:5", {"零件提交保证书": [["要求提交的等级", choice]]})
    assert result["status"] == expected, result


def test_warrant_level_empty_native_boxes_can_be_selected_in_overlay(tmp_path, monkeypatch):
    result, requests = _execute(tmp_path, monkeypatch, "U17", "U17:5", {"零件提交保证书": [["要求提交的等级", "□等级1 □等级2"]]}, drawing=True, visual={"level_exactly_one": True})
    assert result["status"] == "PASS", result
    assert len(requests) == 1


def _approval(remarks="完全认可", reject=None, *, item_header="检验项目"):
    return {
        "封面": [
            ["封面"],
            ["零件号", "P123"],
            ["零件名称", "组件"],
            ["版本", "A"],
            ["UAES批准的前提/备注", remarks],
            [item_header, "否决"],
            ["尺寸", reject],
            ["材料", None],
            ["性能", None],
            ["试装", None],
        ]
    }


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_full_approval_requires_all_three_signatures_and_ordered_dates(tmp_path, monkeypatch, observed, expected):
    result, requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval(), visual={"signed_dates": observed})
    assert result["status"] == expected, result
    assert "组长日期≤审核日期≤批准日期" in requests[0]["signed_dates"]


def test_confirmed_reject_decides_without_signature_request(tmp_path, monkeypatch):
    result, requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval(reject="×"))
    assert result["status"] == "NG", result
    assert not requests


def test_contradictory_approval_is_unknown(tmp_path, monkeypatch):
    result, requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval("完全认可但条件认可"))
    assert result["status"] == "UNABLE", result
    assert not requests


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_approval_native_empty_reject_cells_do_not_clear_image_marks(tmp_path, monkeypatch, observed, expected):
    result, requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval(), drawing=True, visual={"no_reject_mark": observed, "signed_dates": True})
    assert result["status"] == expected, result
    assert "no_reject_mark" in requests[0]
    assert len(requests) == (2 if observed else 1)


def _deviations(deadline, *, part="P123", revision="A"):
    return {
        "偏差认可汇总": [
            ["偏差认可汇总"],
            ["零件号", part],
            ["零件名称", "组件"],
            ["版本", revision],
            ["偏差项", "UAES评估确认", "是否需要偏差", "备注"],
            ["孔径", "同意", "是", f"临时偏差至{deadline}"],
        ]
    }


def test_conditional_approval_compares_frozen_related_source(tmp_path, monkeypatch):
    deadline = date.today() + timedelta(days=30)
    result, requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval(f"条件认可，截止{deadline}"), peers=[("frozen-deviation", "偏差认可汇总", _deviations(deadline))])
    assert result["status"] == "PASS", result
    assert set(result["evidence_file_version_ids"]) == {"frozen-original", "frozen-deviation"}
    assert not requests


def test_approval_item_column_is_not_a_project_identity_field(tmp_path, monkeypatch):
    deadline = date.today() + timedelta(days=30)
    result, _requests = _execute(
        tmp_path,
        monkeypatch,
        "U19",
        "U19:1",
        _approval(f"条件认可，截止{deadline}", item_header="项目"),
        peers=[("frozen-deviation", "偏差认可汇总", _deviations(deadline))],
    )
    assert result["status"] == "PASS", result


@pytest.mark.parametrize(("peer_project", "expected"), [("Project A", "PASS"), ("Project B", "UNABLE")])
def test_matrix_heading_does_not_hide_actual_project_identity(tmp_path, monkeypatch, peer_project, expected):
    deadline = date.today() + timedelta(days=30)
    cover = _approval(f"条件认可，截止{deadline}", item_header="项目")
    cover["封面"].insert(1, ["项目", "Project A"])
    related = _deviations(deadline)
    related["偏差认可汇总"].insert(1, ["项目", peer_project])
    result, _requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", cover, peers=[("frozen-deviation", "偏差认可汇总", related)])
    assert result["status"] == expected, result


@pytest.mark.parametrize(("part", "revision", "expected"), [("P999", "A", "UNABLE"), ("P123", "B", "UNABLE")])
def test_conditional_approval_cannot_borrow_other_part_or_revision(tmp_path, monkeypatch, part, revision, expected):
    deadline = date.today() + timedelta(days=30)
    result, _requests = _execute(
        tmp_path, monkeypatch, "U19", "U19:1", _approval(f"条件认可，截止{deadline}"), peers=[("frozen-deviation", "偏差认可汇总", _deviations(deadline, part=part, revision=revision))]
    )
    assert result["status"] == expected, result


def test_conditional_approval_deadlines_must_match(tmp_path, monkeypatch):
    deadline = date.today() + timedelta(days=30)
    result, _requests = _execute(tmp_path, monkeypatch, "U19", "U19:1", _approval(f"条件认可，截止{deadline}"), peers=[("frozen-deviation", "偏差认可汇总", _deviations(deadline + timedelta(days=1)))])
    assert result["status"] == "NG", result
    assert "不一致" in result["suggestions"]


def test_native_source_digest_must_match_frozen_identity(tmp_path, monkeypatch):
    result, requests = _execute(tmp_path, monkeypatch, "U15", "U15:1", _trial("☑通过 □未通过"), expected_sha="0" * 64)
    assert result["status"] == "ERR", result
    assert not requests
