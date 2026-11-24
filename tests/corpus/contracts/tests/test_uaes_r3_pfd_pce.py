"""UAES revision 3 flow and capability leaves, frozen originals and bindings; visual reads are offline fixtures."""

import hashlib
import json
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _CHECKERS_ROOT, _load_leaf, library_revision
from openpyxl import Workbook

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = _CHECKERS_ROOT


def check(path, leaf, clause, peers=None, read=None):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    files = [(path, suite.document_type), *((p, typ) for typ, paths in (peers or {}).items() for p in paths)]
    sources, inputs = {}, []
    for i, (p, typ) in enumerate(files):
        key = f"f{i}"
        sources[key] = p.read_bytes()
        inputs.append(FrozenInput(key, hashlib.sha256(sources[key]).hexdigest(), "reference" if i else "target", p.name, document_type=typ))
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    target = FrozenTarget("f0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    snapshot = ExecutionSnapshot("test", "candidate", tuple(inputs), (target,), "test", library_revision(), {})
    rt = CheckerRuntime()
    if read:
        rt.read_ocr_visual_facts = read
    return run_audit(snapshot, sources, {digest: suite.program_files}, runtime=rt)["findings"][0]


def book(path, rows, title):
    wb = Workbook()
    wb.active.title = title
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    return path


def chart(path, ops):
    return book(path, [["STP工序号", "加工", "检验", "OPERATION DESCRIPTION过程描述"], [None, "◇", "□"], *ops], "过程流程图")


@pytest.mark.parametrize(
    ("name", "param", "method", "want"),
    [("气密", "压力3 bar", "首件检测", "PASS"), ("气检", "压力3 bar", "首件检测", "UNABLE"), ("气密", "/", "首件检测", "UNABLE"), ("气密", "压力3 bar", None, "UNABLE")],
)
def test_integrated_station_requires_same_step_name_parameter_and_method(tmp_path, name, param, method, want):
    p = chart(tmp_path / "flow.xlsx", [[110, "◇", None, "气密"]])
    f = book(tmp_path / "f.xlsx", [["工序号", "工序/功能/要求", "潜在失效模式"], [110, "气密", "漏气"]], "PFMEA")
    c = book(tmp_path / "c.xlsx", [["工序号", "工序名称", "测试参数", "控制方法"], [110, name, param, method], [120, "气密", "10bar", "巡检"]], "CP")
    result = check(p, "U16", "PFD:2", {"控制计划": [c], "潜在过程失效模式及后果分析": [f]})
    assert result["status"] == want, result
    assert name in result["suggestions"] and "第2行" in result["suggestions"]


def test_no_borrowed_failure_mode_or_unsubmitted_peer(tmp_path):
    p = chart(tmp_path / "flow.xlsx", [[110, "◇", None, "气密"]])
    f = book(tmp_path / "f.xlsx", [["工序号", "工序/功能/要求", "潜在失效模式"], [110, None, "气密"]], "PFMEA")
    result = check(p, "U16", "PFD:2", {"潜在过程失效模式及后果分析": [f]})
    assert result["status"] == "UNABLE" and "工序名“空白”" in result["suggestions"] and "控制计划同批未提交" in result["suggestions"]


@pytest.mark.parametrize(("action", "want"), [("分选/成品检验", "UNABLE"), ("不合格品隔离", "PASS")])
def test_sorting_not_equivalent_to_disposition(tmp_path, action, want):
    p = chart(tmp_path / "flow.xlsx", [[10, None, "□", "进货检验"], [20, None, "□", "出货检验"], [30, "◇", None, action], [40, "◇", None, "成品入库"]])
    result = check(p, "U16", "PFD:4")
    assert result["status"] == want and action in result["suggestions"]


def pdf(path, pages):
    with fitz.open() as doc:
        for text in pages:
            p = doc.new_page()
            if text:
                p.insert_text((40, 60), text)
        doc.save(path)
    return path


NO_SEAL = {"fully_examined": True, "seal_present": False, "controlled_stamp": False, "bbox": []}


@pytest.mark.parametrize(
    ("pages", "facts", "want", "calls"),
    [
        (["native"], [NO_SEAL], "NG", 1),
        (["native", None], [NO_SEAL], "UNABLE", 1),
        (
            ["native"] * 7,
            [NO_SEAL] * 6 + [{"fully_examined": True, "seal_present": True, "controlled_stamp": True, "bbox": [20, 25, 200, 300]}, {"seal_present": True, "legible": True, "text": "受控"}],
            "PASS",
            8,
        ),
        ([None, "native"], [{"fully_examined": True, "seal_present": True, "controlled_stamp": True, "bbox": [20, 25, 200, 300]}, {"seal_present": True, "legible": True, "text": "受控"}], "PASS", 2),
        (["native"], [{"fully_examined": True, "seal_present": True, "controlled_stamp": None, "bbox": [20, 25, 200, 300]}, {"seal_present": True, "legible": False, "text": ""}], "NG", 2),
    ],
)
def test_stamp_location_and_complete_allowed_coverage(tmp_path, pages, facts, want, calls):
    used = []

    def read(**kwargs):
        used.append(kwargs["prompt"])
        return facts[len(used) - 1]

    result = check(pdf(tmp_path / "pfd.pdf", pages), "U16", "PFD:3", read=read)
    assert result["status"] == want and len(used) == calls, (result, used)
    if want == "PASS" or (want == "NG" and calls == 2):
        loc = json.loads(result["evidence"]["sources"][-1]["locator"])
        assert loc["page"] == len(pages) and loc["bbox"][2] < 200


@pytest.mark.parametrize(("ppk", "count", "want"), [(1.67, 125, "PASS"), (1.67, 50, "UNABLE"), (1.66, 50, "NG")])
def test_ppk_and_its_own_explicit_plan_are_distinct(tmp_path, ppk, count, want):
    p = book(tmp_path / "study.xlsx", [["规定样本数", 140], ["DESCRIPTIVE   STATISTICS", "VALUES"], ["Number of readings", count], ["Performance index (Ppk)", ppk]], "能力认证报告")
    result = check(p, "U06", "PCE:2")
    assert result["status"] == want, result
    assert "规定样本数140" in result["suggestions"] and f"本表实际读数{count}" in result["suggestions"]


def test_real_integrated_station_keeps_failure_mode_out_of_operation_name():
    batch = next((UAES / "samples").glob("*776*")) / "测试文件"
    out = check(next(batch.glob("08*/*")), "U16", "PFD:2", {"控制计划": list(batch.glob("10*/*")), "潜在过程失效模式及后果分析": list(batch.glob("09*/*"))})
    assert out["status"] == "UNABLE" and all(x in out["suggestions"] for x in ["工序110", "PFMEA", "第391行", "工序名“空白”", "第139行", "气检", "测试绝对压力", "首件"])


def test_split_flow_sheets_declare_or_identify_all_four_nodes(tmp_path):
    wb = Workbook()
    wb.active.title = "前段"
    wb.create_sheet("后段")
    for sheet, ops in zip(wb.worksheets, ([[10, None, "□", "进货检验"], [20, "◇", None, "不合格品隔离"]], [[30, None, "□", "出货检验"], [40, "◇", None, "成品包装"]])):
        sheet.append(["STP工序号", "加工", "检验", "OPERATION DESCRIPTION过程描述"])
        for row in ops:
            sheet.append(row)
    path = tmp_path / "split.xlsx"
    wb.save(path)
    result = check(path, "U16", "PFD:4")
    assert result["status"] == "PASS" and all(t in result["suggestions"] for t in ("前段", "后段"))


def test_real_magnet_sorting_has_no_documented_disposition():
    path = next((UAES / "samples").rglob("5、Flow chart磁性罩.pdf"))
    result = check(path, "U16", "PFD:4")
    assert result["status"] == "UNABLE" and "第1页“分选/成品检验”" in result["suggestions"]


def test_real_study_index_is_not_a_second_sample_count():
    path = next((UAES / "samples").rglob("574-PPK (1) (1).pdf"))
    result = check(path, "U06", "PCE:2")
    assert result["status"] == "UNABLE" and all(t in result["suggestions"] for t in ("Ppk均不低于1.67", "最多70", "少于125", "是否同时影响PCE:2"))
