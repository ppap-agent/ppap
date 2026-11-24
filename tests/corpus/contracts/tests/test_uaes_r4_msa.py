"""Measurement plan, report and fallback-list behavior through a frozen R4 U05 binding."""

import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from agent.execution.audit_runtime import run_audit
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook

LEAVES = Path(__file__).resolve().parents[4] / "assets/uaes/rulebook/v1.0/candidates/m4"
MSA = "测量系统分析评价"
PART = "F123456789"
OTHER = "F1234567890"


def book(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


def run(clause, target, *references):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes/U05")
    body = json.loads(suite.files["rules.json"])
    inputs, sources = [], {}
    for index, (original, kind) in enumerate([(target, MSA), *references]):
        key = str(index)
        sources[key] = original
        inputs.append(FrozenInput(key, hashlib.sha256(original).hexdigest(), "reference" if index else "target", f"study-{index}.xlsx", document_type=kind))
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    frozen = FrozenTarget("0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, body, binding)
    snapshot = ExecutionSnapshot("fixture", "candidate", tuple(inputs), (frozen,), "fixture", library_revision(), {})
    finding = run_audit(snapshot, sources, {digest: suite.program_files})["findings"][0]
    if finding["status"] == "PASS":
        assert finding["checks_performed"] > 0, finding
    return finding


def plan(*tail, part=PART):
    return [
        ["序号", "零件号", "测量参数", "量具名称", "量具编号", "计划分析类型", "备注"],
        [1, part, "槽宽", "卡尺", "gauge-01", "GR&R"],
        *tail,
    ]


def report(conclusion, grr="8%", part=PART, characteristic="槽宽", ndc=6):
    return [
        ["量具重复性与再现性报告"],
        ["零件号", part],
        ["量具名称", "卡尺"],
        ["测量参数", characteristic],
        ["%GRR", grr],
        ["ndc", ndc],
        ["Conclusion", conclusion],
        ["备注", PART],
    ]


@pytest.mark.parametrize(
    ("tail", "expected", "basis"),
    [
        ([[2], [3]], "PASS", "1个有效行"),
        ([[2, None, None, None, None, None, "已指定待测设备"]], "NG", "为空"),
        ([[2, PART, "轴径", "卡尺", "gauge-02", "/"]], "NG", "占位词"),
    ],
)
def test_effective_plan_rows(tail, expected, basis):
    cp = book({"控制计划": [["工序名称/操作描述", "评价/测量技术"], ["精加工", "卡尺"]]})
    result = run("MSA:1", book({"MSA计划": plan(*tail)}), (cp, "控制计划"))
    assert result["status"] == expected and basis in result["suggestions"]


@pytest.mark.parametrize(
    ("conclusion", "grr", "ndc", "expected"),
    [("OK", "10%", 5, "PASS"), (" 合格 ", "8%", 7, "PASS"), ("OK", "31%", 6, "NG"), ("合格", "8%", 4, "NG"), ("pending", "8%", 8, "UNABLE")],
)
def test_equivalent_grr_conclusion_and_indices(conclusion, grr, ndc, expected):
    result = run("MSA:2", book({"计划": plan(), "研究": report(conclusion, grr, ndc=ndc)}))
    assert result["status"] == expected
    locations = {json.loads(s["locator"]).get("cell") for s in result["evidence"]["sources"]}
    assert {"B4", "B5", "B6", "B7"} <= locations


def test_strict_report_identity_even_when_notes_name_the_plan_part():
    target = book({"计划": plan(), "研究": report("OK", part=OTHER)})
    result = run("MSA:2", target)
    assert result["status"] == "NG" and "没有零件号" in result["suggestions"]


def test_planless_set_reports_missing_fact_without_assigning_it_to_a_child():
    target = book({"研究": report("OK")})
    sibling = book({"研究": report("合格", characteristic="孔径")})
    result = run("MSA:2", target, (sibling, MSA))
    assert result["status"] == "UNABLE" and "归属" in result["suggestions"]


@pytest.mark.parametrize(("first", "expected"), [(PART, "PASS"), (PART.lower(), "UNABLE")])
def test_fallback_list_uses_only_explicit_matching_part_rows(first, expected):
    target = book({"计划": plan(), "研究": report("OK")})
    scl = book({"清单": [["零件号", "特殊特性名称", "特性值", "测量设备"], [first, "槽宽", "16±0.01", "卡尺"], [OTHER, "硬度", "40±2", "硬度仪"]]})
    result = run("MSA:6", target, (scl, "过程特殊特性清单"))
    assert result["status"] == expected
    if expected == "PASS":
        assert "全部1项" in result["suggestions"] and "硬度" not in result["suggestions"]


@pytest.mark.parametrize(("note", "expected"), [("客户批准:授权单 Q-872", "PASS"), ("客户批准:未批准", "NG"), ("客户批准:", "NG")])
def test_qualified_grr_acceptance_requires_an_actual_approval(note, expected):
    study = report("OK", "18%") + [[note]]
    result = run("MSA:2", book({"计划": plan(), "研究": study}))
    assert result["status"] == expected


def test_another_report_does_not_cover_a_wrong_part_in_the_current_study():
    study = book({"研究": report("OK", part=OTHER)})
    peer = book({"计划": plan(), "研究": report("OK")})
    result = run("MSA:2", study, (peer, MSA))
    assert result["status"] == "NG" and OTHER in result["suggestions"]


def test_local_plan_identifies_part_even_with_another_plan_in_batch():
    local = book({"计划": plan()})
    peer = book({"计划": plan(part=OTHER)})
    local_report = book({"研究": report("合格")})
    scl = book({"清单": [["零件号", "特殊特性名称", "特性值", "测量设备"], [PART, "槽宽", "16±0.01", "卡尺"], [OTHER, "硬度", "40±2", "硬度仪"]]})
    result = run("MSA:6", local, (peer, MSA), (local_report, MSA), (scl, "过程特殊特性清单"))
    assert result["status"] == "PASS" and "全部1项" in result["suggestions"], result
