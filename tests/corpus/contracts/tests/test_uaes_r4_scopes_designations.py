"""Revision 4: independent table scopes and complete, current drawing designations."""

import hashlib
import json
from pathlib import Path

import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook

LEAVES = Path(__file__).resolve().parents[4] / "assets/uaes/rulebook/v1.0/candidates/m4"


def workbook(tmp, filename, title, rows):
    doc = Workbook()
    doc.active.title = title
    for row in rows:
        doc.active.append(row)
    path = tmp / filename
    doc.save(path)
    return path


def run(leaf, clause, target, companions=()):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    sources, inputs, targets, programs = {}, [], [], {}
    for i, (p, kind, role) in enumerate([(target, suite.document_type, "target"), *companions]):
        key = f"file{i}"
        sources[key] = p.read_bytes()
        inputs.append(FrozenInput(key, hashlib.sha256(sources[key]).hexdigest(), role, p.name, document_type=kind))
        if role == "target":
            entry, rule = (suite, clause) if not i else (_load_leaf(LEAVES, "uaes", LEAVES / "uaes/U16"), "PFD:1")
            digest = checker_digest(entry.program_files)
            binding = RuleBinding(entry.suite_key, digest, {rule: entry.entrypoints[rule]})
            targets.append(FrozenTarget(key, "sha256:" + entry.revision_digest, entry.suite_key, entry.revision_digest, json.loads(entry.files["rules.json"]), binding))
            programs[digest] = entry.program_files
    snap = ExecutionSnapshot("test", "candidate", tuple(inputs), tuple(targets), "test", library_revision(), {})
    return run_audit(snap, sources, programs, runtime=CheckerRuntime())["findings"][0]


@pytest.mark.parametrize("label", ["Code", "Part No.", "零件号"])
def test_material_scope_is_established_before_reading_values(tmp_path, label):
    setup = [[label, "序号", "特性名称", "规格/公差", "分类"], ["Qa-19", 1, "温度", "80±2", "/"], ["QA-19", 2, "孔径", None, "SC"], ["Qa-190", 3, "孔距", None, "SC"]]
    target = workbook(tmp_path, "master.xlsx", "特殊特性清单", setup)
    ambiguous = run("U12", "SCL:2", target)
    assert ambiguous["status"] == "UNABLE" and "不能确定本件号" in ambiguous["suggestions"], ambiguous
    source = workbook(tmp_path, "flow.xlsx", "过程流程图", [["Part Number", "Qa-19"]])
    scoped = run("U12", "SCL:2", target, [(source, "PFD", "target")])
    assert scoped["status"] == "PASS" and "参与判定1行" in scoped["suggestions"] and "普通行" in scoped["suggestions"], scoped
    source = workbook(tmp_path, "flow.xlsx", "过程流程图", [["Part Number", "QA-19"]])
    exact = run("U12", "SCL:2", target, [(source, "PFD", "target")])
    assert exact["status"] == "NG" and "参与判定1行" in exact["suggestions"] and "特性及公差" in exact["suggestions"], exact
    unresolved = workbook(tmp_path, "incomplete.xlsx", "特殊特性清单", [r[:-1] for r in setup])
    assert run("U12", "SCL:2", unresolved)["status"] == "UNABLE"


def material_list(tmp, name):
    return workbook(
        tmp,
        "materials.xlsx",
        "材料及分供方清单",
        [["UAES项目编号：\nUAES Project No.:", "P-629"], ["零件名称:\nPart Name:", name], ["零件号:\nPart No:", "Qa-19"], ["序号", "零件可视图", "材料名称和材料牌号"], [1, "", "AL"]],
    )


def drawing(tmp, fn, title, state, number="Qa-19", project="P-629"):
    return (workbook(tmp, fn, "技术图", [["工程图"], ["图号", number], ["项目编号", project], ["名称", title], ["图纸状态", state]]), "图纸", "reference")


@pytest.mark.parametrize("listed,expected", [("端盖", "PASS"), ("End Cover", "PASS"), ("Cover", "NG"), ("盖", "NG"), ("End cover", "NG")])
def test_complete_name_of_either_language(tmp_path, listed, expected):
    source = drawing(tmp_path, "release.xlsx", "端盖\nEnd Cover", "在用")
    report = run("U04", "MSL:3", material_list(tmp_path, listed), [source])
    assert report["status"] == expected, report
    assert listed in report["suggestions"] and "端盖\nEnd Cover" in report["suggestions"]


def test_parallel_current_drawings_and_replaced_names(tmp_path):
    target = material_list(tmp_path, "Front Cover")
    refs = [drawing(tmp_path, "a.xlsx", "后盖 / Rear Cover", "current"), drawing(tmp_path, "b.xlsx", "前盖 / Front Cover", "current")]
    result = run("U04", "MSL:3", target, refs)
    assert result["status"] == "PASS" and "b.xlsx" in result["suggestions"], result
    refs[1] = drawing(tmp_path, "b.xlsx", "前盖 / Front Cover", "superseded")
    assert run("U04", "MSL:3", target, refs)["status"] == "NG"
    refs.append(drawing(tmp_path, "c.xlsx", "前盖 / Front Cover", "current", number="Qa-190"))
    refs.append(drawing(tmp_path, "d.xlsx", "前盖 / Front Cover", "current", project="P-630"))
    assert run("U04", "MSL:3", target, refs)["status"] == "NG"


def test_material_header_selects_full_literal_code(tmp_path):
    target = workbook(
        tmp_path, "sheet.xlsx", "特殊特性清单", [["Part No.", "HUB/A"], [], ["序号", "Code", "特性名称", "规格/公差", "分类"], [1, "HUB/A", "孔径", "9±0.01", "SC"], [2, "HUB", "直径", None, "SC"]]
    )
    result = run("U12", "SCL:2", target)
    assert result["status"] == "PASS" and "本件号HUB/A过滤" in result["suggestions"] and "参与判定1行" in result["suggestions"], result


def test_parallel_sources_without_a_chosen_part_remain_unresolved(tmp_path):
    target = workbook(tmp_path, "parts.xlsx", "特殊特性清单", [["序号", "Code", "特性名称", "规格/公差", "分类"], [1, "AX-32", "深度", "50±1", "CC"], [1, "AX-33", "深度", "60±1", "SC"]])
    sheets = [(workbook(tmp_path, f"flow{i}.xlsx", "过程流程图", [["Part Number", part]]), "PFD", "target") for i, part in enumerate(("AX-32", "AX-33"))]
    result = run("U12", "SCL:2", target, sheets)
    assert result["status"] == "UNABLE" and "AX-32" in result["suggestions"] and "AX-33" in result["suggestions"], result


@pytest.mark.parametrize("field", ["后轮毂   Rear Hub", "Rear Hub（后轮毂）", "后轮毂／Rear Hub"])
def test_bilingual_drawing_layouts(tmp_path, field):
    source = drawing(tmp_path, "bearing.xlsx", field, "在用")
    result = run("U04", "MSL:3", material_list(tmp_path, "Rear Hub"), [source])
    assert result["status"] == "PASS" and "Rear Hub" in result["suggestions"], result
