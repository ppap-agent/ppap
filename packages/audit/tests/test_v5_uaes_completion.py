"""UAES remaining clauses through actual leaf factories and frozen Core.

These cases supply original workbook bytes and controlled visual observations.
They prove business execution and provenance, not live model accuracy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from audit.checker_core import FrozenInput, FrozenTarget, RuleBinding, checker_digest, validate_results
from audit.checker_lib.source import SourceAudit
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_library import check_factory
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook


def _execute(tmp_path, monkeypatch, entry, clause, sheets, *, peers=(), visual=None, error=None, version="original"):
    """One local harness owns staging, actual factory execution and Core checks."""
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"v5/UAES/{entry}")
    resources = ModuleType("resources")
    exec(compile(suite.files["resources.py"], f"{suite.source_path}/resources.py", "exec"), resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    checker = ModuleType(f"uaes_completion_{entry}")
    monkeypatch.setitem(sys.modules, checker.__name__, checker)
    exec(compile(suite.files["checker.py"], f"{suite.source_path}/checker.py", "exec"), checker.__dict__)
    body = json.loads(suite.files["rules.json"])
    body["rules"] = [row for row in body["rules"] if row["id"] == clause]
    body["checker_binding"] = {clause: suite.entrypoints[clause]}
    assert len(body["rules"]) == 1

    def stage(identity, filename, contents, role):
        book = Workbook()
        book.remove(book.active)
        for title, rows in contents.items():
            sheet = book.create_sheet(title)
            for row in rows:
                sheet.append(row)
        path = tmp_path / (filename + ".xlsx")
        book.save(path)
        book.close()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        audit = AuditInput(audit_id=identity, file_url=str(path), customer="UAES", project_id="project", file_metadata={"source_sha256": digest})
        return audit, FrozenInput(identity, digest, role, path.name)

    audit, original = stage(version, entry, sheets, "target")
    frozen, by_type = [original], {}
    for identity, kind, contents in peers:
        peer, source = stage(identity, identity, contents, "reference")
        by_type.setdefault(kind, []).append(peer)
        frozen.append(source)
    group = GroupContextInput(by_doc_type=by_type, by_audit_id={peer.audit_id: peer for rows in by_type.values() for peer in rows})
    rule = RuleInput(rule_id="frozen-rule", **body)
    binding = RuleBinding(suite.suite_key, checker_digest(suite.program_files), body["checker_binding"])
    target = FrozenTarget(version, "revision", suite.suite_key, suite.revision_digest, body, binding)
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
        observed["evidence_file_version_ids"] = sorted({version, *(json.loads(source["locator"])["file_version_id"] for source in sources)})
        checked = validate_results([observed], target=target, inputs=frozen)
        assert checked["coverage"] == [{"file_version_id": version, "clause_id": clause, "coverage": "mapped"}]
        result = checked["findings"][0]
        assert result["clause_id"] == clause
        if result["status"] == "PASS":
            assert result["evidence"]["sources"], result
        return result, requests
    finally:
        for owner in reversed(opened):
            owner.close()


def _report(*args, **kwargs):
    return _execute(*args, **kwargs)[0]


def _visual(*args, **kwargs):
    return _execute(*args, version="frozen-original", **kwargs)


def _native(*args, **kwargs):
    return _execute(*args, **kwargs)


@pytest.fixture(autouse=True)
def no_live_model_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Live provider calls are outside this controlled acceptance")

    monkeypatch.setattr("audit.engine.checkers.client.AIClient", forbidden)
    monkeypatch.setattr("documents.ocr.client.AsyncOpenAI", forbidden)


def _identity(*, part="P12345", revision="A", name="支架"):
    return [["项目", "项目A"], ["零件号", part], ["零件名称", name], ["版本", revision]]


def _sheet(title, rows=(), **identity):
    return {title: [[title], *_identity(**identity), *rows]}


def _versions(result, expected):
    assert set(result["evidence_file_version_ids"]) == set(expected), result
    assert {json.loads(source["locator"])["file_version_id"] for source in result["evidence"]["sources"]} >= set(expected), result


_FLOW_FACTS = {
    "U16:1": {"key_operation": True, "characteristic_marks": True, "legend": True},
    "U16:2": {"standard_shapes": True, "complete_legend": True, "consistent": True, "inspection_shapes": True},
    "U16:3": {"controlled_stamp": True},
}


@pytest.mark.parametrize("clause", list(_FLOW_FACTS))
@pytest.mark.parametrize(("state", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_flow_clause_uses_complete_controlled_original_facts(tmp_path, monkeypatch, clause, state, expected):
    facts = dict(_FLOW_FACTS[clause])
    facts[next(reversed(facts))] = state
    sheets = {"过程流程图": [["过程流程图", "K表示关键工序"], ["检验符号", "受控章"]]}
    result, requests = _visual(tmp_path, monkeypatch, "U16", clause, sheets, visual=facts)
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == set(facts)
    if expected == "PASS":
        _versions(result, {"frozen-original"})
        assert any(source["kind"] == "visual" for source in result["evidence"]["sources"])


def test_flow_provider_failure_is_err_not_missing_graphics(tmp_path, monkeypatch):
    result, requests = _visual(tmp_path, monkeypatch, "U16", "U16:2", {"过程流程图": [["过程流程图"]]}, error=TimeoutError("controlled timeout"))
    assert result["status"] == "ERR", result
    assert result["checks_performed"] == 0 and len(requests) == 1


@pytest.mark.parametrize(("samples", "expected"), [(3, "PASS"), (6, "PASS"), (2, "NG")])
def test_inspection_sample_column_count_uses_actual_headers(tmp_path, monkeypatch, samples, expected):
    headers = ["要求", "供应商实测值", *[f"Sample{number}" for number in range(2, samples + 1)]]
    result = _report(tmp_path, monkeypatch, "U01", "U01:1-1", {"尺寸报告": [headers, ["10±1", *[10] * samples]]})
    assert result["status"] == expected, result
    _versions(result, {"original"})


def test_inspection_sample_header_uncertainty_cannot_become_column_count_pass(tmp_path, monkeypatch):
    result = _report(tmp_path, monkeypatch, "U01", "U01:1-1", {"尺寸报告": [["要求", "=unknown_header()", "Sample2", "Sample3"], ["10±1", 10, 10, 10]]})
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_inspection_four_report_signatures_use_original_fact(tmp_path, monkeypatch, observed, expected):
    sheets = {title: [["要求", "供应商实测值", "Sample2", "Sample3"], ["10±1", 10, 10.1, 9.9]] for title in ("尺寸报告", "材料报告", "功能报告", "可靠性报告")}
    result, requests = _visual(tmp_path, monkeypatch, "U01", "U01:3", sheets, visual={"four_signatures": observed})
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == {"four_signatures"}


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_materials_three_party_signatures_are_not_native_printed_names(tmp_path, monkeypatch, observed, expected):
    sheets = {"材料清单": [["供应商负责工程师签字", "打印姓名"], ["UAES NE工程师签字", "打印姓名"], ["UAES SQD/SQE工程师签字", "打印姓名"]]}
    result, requests = _visual(tmp_path, monkeypatch, "U04", "U04:2", sheets, visual={"signatures": observed})
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == {"signatures"}


@pytest.mark.parametrize(
    ("part", "revision", "name", "expected"), [("P12345", "A", "支架", "PASS"), ("P12345", "A", "另一零件", "NG"), ("P99999", "A", "支架", "UNABLE"), ("P12345", "B", "支架", "UNABLE")]
)
def test_materials_independent_drawing_matches_frozen_identity(tmp_path, monkeypatch, part, revision, name, expected):
    target = _sheet("材料及分供方清单")
    drawing = ("drawing", "图纸", _sheet("图纸", part=part, revision=revision, name=name))
    result = _report(tmp_path, monkeypatch, "U04", "U04:3", target, peers=[drawing])
    assert result["status"] == expected, result
    if expected == "PASS":
        _versions(result, {"original", "drawing"})


def test_materials_missing_independent_drawing_is_ng(tmp_path, monkeypatch):
    result = _report(tmp_path, monkeypatch, "U04", "U04:3", _sheet("材料及分供方清单"))
    assert result["status"] == "NG", result


@pytest.mark.parametrize(("gauge", "report_title", "accepted"), [("通止规", "Kappa报告", "Acceptable"), ("卡尺", "GRR报告", "测量系统可以接受")])
@pytest.mark.parametrize(("state", "expected"), [("accepted", "PASS"), ("rejected", "NG"), ("unknown", "UNABLE")])
def test_msa_plan_matches_actual_gauge_type_number_and_report_conclusion(tmp_path, monkeypatch, gauge, report_title, accepted, state, expected):
    conclusion = accepted if state == "accepted" else "不可接受" if state == "rejected" else "=unknown_conclusion()"
    sheets = {"MSA计划表": [["量具名称", "量具编号"], [gauge, "G1"]], report_title: [["量具编号", "G1"], ["结论", conclusion]]}
    result = _report(tmp_path, monkeypatch, "U05", "U05:1-2", sheets)
    assert result["status"] == expected, result


def test_msa_report_for_another_gauge_cannot_satisfy_plan(tmp_path, monkeypatch):
    sheets = {"MSA计划表": [["量具名称", "量具编号"], ["通止规", "G1"]], "Kappa报告": [["量具编号", "G2"], ["结论", "Acceptable"]]}
    result = _report(tmp_path, monkeypatch, "U05", "U05:1-2", sheets)
    assert result["status"] == "UNABLE", result


@pytest.mark.parametrize(("conclusion", "expected"), [("Acceptable", "PASS"), ("Not Acceptable", "NG"), ("=unknown_conclusion()", "UNABLE")])
def test_msa_pgqp_counting_gauge_uses_same_number_kappa(tmp_path, monkeypatch, conclusion, expected):
    target = _sheet("Kappa报告", [["量具编号", "G1"], ["结论", conclusion]])
    plan = ("plan", "采购件质量计划", _sheet("PGQP1", [["重要特性", "测量设备", "量具编号"], ["CC", "通止规", "G1"]]))
    result, requests = _native(tmp_path, monkeypatch, "U05", "U05:2", target, peers=[plan])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "plan"})


@pytest.mark.parametrize(("conclusion", "expected"), [("Acceptable", "PASS"), ("Not Acceptable", "NG"), ("=unknown_conclusion()", "UNABLE")])
def test_msa_outgoing_manual_inspection_uses_same_name_kappa(tmp_path, monkeypatch, conclusion, expected):
    target = _sheet("Kappa报告", [["量具名称", "目视量规"], ["结论", conclusion]])
    plan = ("cp", "控制计划", _sheet("控制计划", [["工序名称", "评价测量技术", "量具名称"], ["出货检验", "人工目视", "目视量规"]]))
    result, requests = _native(tmp_path, monkeypatch, "U05", "U05:3", target, peers=[plan])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "cp"})


@pytest.mark.parametrize("clause", ["U06:4", "U06:5"])
@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("unknown_name", "UNABLE"), ("missing_peer", "NG")])
def test_capability_directory_covers_frozen_pgqp_applicable_characteristics(tmp_path, monkeypatch, clause, state, expected):
    target = _sheet("目录", [["序号", "特性名称"], [1, "孔径"]])
    name = "孔径" if state != "unknown_name" else "=unknown_name()"
    plan = ("plan", "采购件质量计划", _sheet("PGQP1", [["特性名称", "特殊特性标识", "特性类别"], [name, "CC", "过程特性"]]))
    result, requests = _native(tmp_path, monkeypatch, "U06", clause, target, peers=[] if state == "missing_peer" else [plan])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "plan"})


@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("missing_characteristic", "UNABLE"), ("missing_peer", "NG")])
def test_pfmea_pgqp_characteristic_coverage_uses_actual_peer(tmp_path, monkeypatch, state, expected):
    target = _sheet("PFMEA", [["功能/要求", "特殊特性"], ["孔径" if state != "missing_characteristic" else "长度", "CC"]])
    plan = ("plan", "采购件质量计划", _sheet("PGQP1", [["特性名称", "特性类别"], ["孔径", "CC"]]))
    result, requests = _native(tmp_path, monkeypatch, "U07", "U07:4", target, peers=[] if state == "missing_peer" else [plan])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "plan"})


@pytest.mark.parametrize(("observed", "expected"), [(True, "PASS"), (False, "NG"), (None, "UNABLE")])
def test_control_plan_production_type_and_actual_controlled_stamp(tmp_path, monkeypatch, observed, expected):
    result, requests = _visual(tmp_path, monkeypatch, "U08", "U08:3", {"控制计划": [["控制计划类型", "量产"], ["受控章"]]}, visual={"controlled_stamp": observed})
    assert result["status"] == expected, result
    assert len(requests) == 1 and set(requests[0]) == {"controlled_stamp"}


def _process_peers(*, changed=False):
    return [
        ("flow", "过程流程图", _sheet("过程流程图", [["工序号", "工序名称"], [10, "压装"]])),
        ("pfmea", "PFMEA", _sheet("PFMEA", [["过程步骤"], ["焊接" if changed else "压装"]])),
        ("plan", "采购件质量计划", _sheet("PGQP1", [["工艺"], ["压装"]])),
    ]


@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("mismatched_step", "UNABLE"), ("missing_peer", "NG")])
def test_control_plan_all_three_independent_process_sources_are_covered(tmp_path, monkeypatch, state, expected):
    peers = _process_peers(changed=state == "mismatched_step")
    target = _sheet("控制计划", [["工序名称"], ["压装"]])
    result, requests = _native(tmp_path, monkeypatch, "U08", "U08:8", target, peers=peers[:-1] if state == "missing_peer" else peers)
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "flow", "pfmea", "plan"})


def _characteristic_peers():
    return [
        ("flow", "过程流程图", _sheet("过程流程图", [["产品特性", "特性类别"], ["孔径", "CC"]])),
        ("pfmea", "PFMEA", _sheet("PFMEA", [["功能/要求", "特殊特性"], ["孔径", "CC"]])),
        ("plan", "采购件质量计划", _sheet("PGQP1", [["特性名称", "特殊特性"], ["孔径", "CC"]])),
    ]


@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("missing_characteristic", "UNABLE"), ("missing_peer", "NG")])
def test_control_plan_three_source_special_characteristics_are_covered(tmp_path, monkeypatch, state, expected):
    peers = _characteristic_peers()
    target = _sheet("控制计划", [["工序名称", "特性名称"], ["压装", "长度" if state == "missing_characteristic" else "孔径"]])
    result, requests = _native(tmp_path, monkeypatch, "U08", "U08:9", target, peers=peers[:-1] if state == "missing_peer" else peers)
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "flow", "pfmea", "plan"})


@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("missing_trial", "UNABLE"), ("missing_peer", "NG")])
def test_control_plan_pgqp_type_trial_has_source_and_target_match(tmp_path, monkeypatch, state, expected):
    target = _sheet("控制计划", [["工序名称", "特性名称"], ["出货检验", "冲击" if state == "missing_trial" else "耐久"]])
    plan = ("plan", "采购件质量计划", _sheet("PGQP1", [["型式试验"], ["耐久"]]))
    result, requests = _native(tmp_path, monkeypatch, "U08", "U08:11", target, peers=[] if state == "missing_peer" else [plan])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "plan"})


@pytest.mark.parametrize(("state", "expected"), [("covered", "PASS"), ("missing_dimension", "UNABLE"), ("missing_peer", "NG")])
def test_control_plan_special_drawing_dimension_uses_frozen_drawing(tmp_path, monkeypatch, state, expected):
    target = _sheet("控制计划", [["工序名称", "特性名称"], ["磨削", "长度" if state == "missing_dimension" else "孔径"]])
    drawing = ("drawing", "图纸", _sheet("图纸", [["尺寸", "特殊特性"], ["孔径", "CC"]]))
    result, requests = _native(tmp_path, monkeypatch, "U08", "U08:13", target, peers=[] if state == "missing_peer" else [drawing])
    assert result["status"] == expected, result
    assert not requests
    if expected == "PASS":
        _versions(result, {"original", "drawing"})
