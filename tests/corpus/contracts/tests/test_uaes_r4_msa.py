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
        suffix = "pdf" if original.startswith(b"%PDF") else "xlsx"
        inputs.append(FrozenInput(key, hashlib.sha256(original).hexdigest(), "reference" if index else "target", f"study-{index}.{suffix}", document_type=kind))
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


def test_same_characteristic_across_independently_stated_units():
    # New vendor form: nominal in millimetres, both deviations in micrometres.
    gauge_plan = plan()
    gauge_plan[1][2] = "12.345–12.390 mm"
    study = report("可接受", characteristic="12.36 mm") + [["参数规格", "30/-15 μm"]]
    result = run("MSA:2", book({"新款设备计划": gauge_plan, "重新排版的量具研究": study}))
    assert result["status"] == "PASS", result
    locations = {json.loads(s["locator"]).get("cell") for s in result["evidence"]["sources"]}
    assert {"B4", "B9"} <= locations


def test_supplier_part_numbers_are_matched_verbatim_across_a_mixed_list():
    part = "NX-024_B"
    target = book({"计划": plan(part=part), "研究": report("OK", part=part)})
    scl = book({"清单": [["零件号", "特殊特性名称", "特性值", "测量设备"], [part, "槽宽", "16±0.01", "卡尺"], ["NX-024_C", "入料硬度", "40±2", "硬度仪"]]})
    result = run("MSA:6", target, (scl, "过程特殊特性清单"))
    assert result["status"] == "PASS" and part in result["suggestions"], result


def observe_statements(monkeypatch, expected):
    """Replace only the transport; the frozen checker must find and bind every field itself."""
    from documents.ai import AIResult

    calls = []

    def read(task, *, prompt, **_):
        assert task == "uaes_r4_msa_statements"
        fields = json.loads(prompt)
        calls.append(list(fields.values()))
        assert set(fields.values()) == set(expected)
        return AIResult({key: {"state": expected[value], "quote": value} for key, value in fields.items()})

    monkeypatch.setattr("documents.ai.run_json", read)
    return calls


def test_current_study_conclusions_share_one_scoped_read(monkeypatch):
    grr = "本次研究符合要求，不需整改。"
    kappa = "Marginal in the prior study; this repeat study is satisfactory."
    data = plan([2, PART, "纹路", "目视", "visual-1", "Kappa"])
    attribute = [["Kappa研究"], ["零件号", PART], ["量具名称", "目视"], ["测量参数", "纹路"], ["Kappa", 0.93], ["Conclusion", kappa]]
    calls = observe_statements(monkeypatch, {grr: "accept", kappa: "accept"})
    result = run("MSA:2", book({"计划": data, "量具研究": report(grr), "目视研究": attribute, "量具研究副页": report(grr)}))
    assert result["status"] == "PASS", result["suggestions"]
    assert len(calls) == 1 and len(calls[0]) == 2
    assert {grr, kappa} <= {e["excerpt"] for e in result["evidence"]["sources"]}


def test_native_pdf_binds_rectification_and_tolerance_waiver_to_current_study(monkeypatch):
    import fitz

    correction, waiver = "已更换量具夹紧机构并完成复测", "本次依公差带评估准予豁免"
    calls = observe_statements(monkeypatch, {correction: "documented", waiver: "documented"})
    with fitz.open() as doc:
        page = doc.new_page()
        # Chinese field labels, independently placed values, and inline fields on a native page.
        for row, (label, value) in enumerate(
            [
                ("重复性及再现性分析报告", ""),
                ("零件号", PART),
                ("特性", "槽宽"),
                ("量具名称", "卡尺"),
                ("结论", "合格"),
                ("%GRR=", "17%"),
                ("NDC=", "4"),
                ("整改说明", correction),
                ("公差带豁免说明", waiver),
            ]
        ):
            y = 60 + 24 * row
            page.insert_text((45, y), label, fontname="china-s" if not label.isascii() else "helv")
            if value:
                page.insert_text((180, y), value, fontname="china-s" if not value.isascii() else "helv")
        # Ruled form with a native grid: the frozen PDF table preparer can inspect it as well.
        for r in range(10):
            page.draw_line((40, 44 + 24 * r), (430, 44 + 24 * r))
        for x in (40, 172, 430):
            page.draw_line((x, 44), (x, 260))
        result = run("MSA:2", doc.tobytes(), (book({"计划": plan()}), MSA))
    assert result["status"] == "PASS", result["suggestions"]
    assert len(calls) == 1
    observed = {e["excerpt"] for e in result["evidence"]["sources"] if (loc := json.loads(e["locator"])).get("page") == 1 and loc.get("bbox") and loc.get("file_version_id") == "0"}
    assert {correction, waiver, "17%", "4", "合格"} <= observed
