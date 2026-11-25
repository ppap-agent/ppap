"""R4 package and trace checks through the frozen runner. Visual facts below are explicit test seams."""

import hashlib
import json
from pathlib import Path

import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.checker_lib.uaes.native import UaesSourceAgent
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
CANDIDATES = UAES / "rulebook/v1.0/candidates/m4"


def run(tmp_path, monkeypatch, leaf, book, observed):
    path = tmp_path / "记录.xlsx"
    book.save(path)
    return frozen(monkeypatch, leaf, path, observed)


def frozen(monkeypatch, leaf, path, observed, runtime=None):
    def visual(_self, fields, **_):
        return {key: observed[key] for key in fields if key in observed}, set()

    monkeypatch.setattr(UaesSourceAgent, "visual_observation", visual)
    suite = _load_leaf(CANDIDATES, "uaes", CANDIDATES / "uaes" / leaf)
    clause = next(iter(suite.entrypoints))
    digest = checker_digest(suite.program_files)
    target = FrozenTarget(
        "f0",
        "sha256:" + suite.revision_digest,
        suite.suite_key,
        suite.revision_digest,
        json.loads(suite.files["rules.json"]),
        RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]}),
    )
    data = path.read_bytes()
    obj = FrozenInput("f0", hashlib.sha256(data).hexdigest(), "target", path.name, document_type=suite.document_type)
    snap = ExecutionSnapshot("test", "candidate", (obj,), (target,), "test", library_revision(), {})
    return run_audit(snap, {"f0": data}, {digest: suite.program_files}, runtime=runtime or CheckerRuntime())["findings"][0]


PKG_SIGN = {"sign_0": True, "sign_1": True, "sign_2": True, "seal": True}
TRACE_SIGN = {"supplier_signature": True, "supplier_seal": True}


def package():
    book = Workbook()
    sheet = book.active
    for row in [
        ["备件包装方案（修订B）"],
        ["产品资料"],
        ["零件号", "Q-17"],
        ["供方概况"],
        ["供应商名称", "海湾材料"],
        ["包装设计通用原则"],
        ["容器应防潮、可堆码"],
        ["零件特殊要求"],
        ["端口必须加保护帽"],
        ["装载配置"],
        ["每箱数量", 24],
        ["耗材构成"],
        ["包材名称", "瓦楞隔板"],
        ["编制", "审核", "批准"],
        ["UAES审批"],
    ]:
        sheet.append(row)
    return book


@pytest.mark.parametrize(
    "change,expected,reason",
    [
        ("", "PASS", "六个内容块"),
        ("wrong_title", "NG", "标题"),
        ("section_is_not_title", "NG", "标题"),
        ("no_material", "NG", "包装材料清单"),
        ("unsigned", "NG", "审核"),
        ("no_seal", "NG", "印章"),
        ("missing_observation", "UNABLE", "核实"),
    ],
)
def test_package_blocks_and_supplier_signatures(monkeypatch, tmp_path, change, expected, reason):
    book, marks = package(), PKG_SIGN.copy()
    if change in {"wrong_title", "section_is_not_title"}:
        book.active["A1"] = "装箱工艺"
        book.active["C2"] = "见包装方案" if change == "wrong_title" else "包装方案信息"
    elif change == "no_material":
        book.active["A12"] = "包装材料清单"
        book.active["B13"] = None
    elif change == "unsigned":
        marks["sign_1"] = False
    elif change == "no_seal":
        marks["seal"] = False
    elif change == "missing_observation":
        del marks["seal"]
    out = run(tmp_path, monkeypatch, "U09", book, marks)
    assert (out["status"], reason in out["suggestions"]) == (expected, True), out


def test_packaging_reads_equivalent_body_statement_without_a_template_heading(tmp_path, monkeypatch):
    from documents.ai import AIResult

    book = package()
    statement = "本件的运输防护条件：阀口只能用无硫隔离纸包裹"
    book.active["A8"], book.active["A9"] = statement, None
    path = tmp_path / "设计文件.xlsx"
    book.save(path)
    called = []

    def observe(task, *, prompt, **_):
        assert task == "uaes_packaging_body_facts"
        sources = json.loads(prompt)
        assert "备件包装方案" not in prompt and "海湾材料" not in prompt
        called.append(task)
        key = next(k for k, v in sources.items() if statement in v)
        return AIResult({"coverage": "complete", "facts": [{"kind": "part_specific_protection", "source": key, "value": "阀口只能用无硫隔离纸包裹"}]})

    monkeypatch.setattr("documents.ai.run_json", observe)
    result = frozen(monkeypatch, "U09", path, PKG_SIGN, runtime=CheckerRuntime(allow_model_tools=True))
    assert (result["status"], len(called)) == ("PASS", 1), result["suggestions"]
    assert any(s["excerpt"] == statement and json.loads(s["locator"])["cell"] == "A8" for s in result["evidence"]["sources"])


def trace():
    book = Workbook()
    sheet = book.active
    for row in [
        ["供应商零件追溯"],
        ["基本信息"],
        ["零件号码", "P-42"],
        ["分供方追溯"],
        ["子零件名称", "子零件供应商", "追溯风险评估"],
        ["防护帽", "北方材料", "低风险"],
        ["卡环", "西部五金", "低风险"],
        [],
        [],
        ["生产过程追溯"],
        ["工序名称", "追溯方法"],
        ["切削", "序列号"],
        ["交付追溯"],
        ["订单号A32"],
        ["追溯状态总结"],
        ["优点：批号贯通"],
    ]:
        sheet.append(row)
    return book


@pytest.mark.parametrize(
    "change,expected,reason",
    [
        ("tail", "PASS", "均有实质内容"),
        ("labelled_tail", "PASS", "均有实质内容"),
        ("midrow", "NG", "第7行"),
        ("midcolumn", "NG", "第2列"),
        ("block", "NG", "区域整块缺失1处"),
        ("mixed", "NG", "填占位词1处"),
        ("unsigned", "NG", "手写签字"),
        ("no_seal", "NG", "有效印章"),
    ],
)
def test_trace_only_between_used_entries(monkeypatch, tmp_path, change, expected, reason):
    book, marks = trace(), TRACE_SIGN.copy()
    ws = book.active
    if change == "labelled_tail":
        ws["A9"] = "子零件3"
    elif change == "midrow":
        ws.insert_rows(7)
    elif change == "midcolumn":
        ws["B6"] = ws["B7"] = None
    elif change in {"block", "mixed"}:
        ws["A12"] = ws["B12"] = None
        if change == "mixed":
            ws["A12"] = "TBD"
            ws.insert_rows(7)
    elif change == "unsigned":
        marks["supplier_signature"] = False
    elif change == "no_seal":
        marks["supplier_seal"] = False
    out = run(tmp_path, monkeypatch, "U18", book, marks)
    assert (out["status"], reason in out["suggestions"]) == (expected, True), out
    if change == "mixed":
        assert "区域整块缺失1处" in out["suggestions"] and "局部空洞1处" in out["suggestions"], out


def test_trace_supplier_source_does_not_have_middle_voids(monkeypatch):
    path = next((UAES / "samples").glob("*/测试文件/*零件追溯说明/*追溯*.xlsx"))
    out = frozen(monkeypatch, "U18", path, dict.fromkeys(TRACE_SIGN, False))
    assert out["status"] == "NG" and all(x in out["suggestions"] for x in ("手写签字", "有效印章")), out
    assert "局部空洞" not in out["suggestions"] and "区域整块缺失" not in out["suggestions"], out


def test_word_package_body_title_not_a_later_table_mention(monkeypatch, tmp_path):
    from docx import Document

    for label, expected in [("装箱工艺", "NG"), ("售后件包装方案 B", "PASS")]:
        doc = Document()
        doc.add_heading(label, 0)
        for row in list(package().active.values)[1:]:
            doc.add_paragraph("：".join(str(v) for v in row if v is not None))
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "另见厂内包装方案"
        path = tmp_path / "提交资料.docx"
        doc.save(path)
        out = frozen(monkeypatch, "U09", path, PKG_SIGN)
        assert out["status"] == expected, out


def test_missing_summary_not_filled_by_signoff(monkeypatch, tmp_path):
    book = trace()
    book.active["A16"] = "优点：弱点：后续计划："
    book.active.append(["编制", "审核", "批准"])
    book.active.append(["李工", "赵工", "王工"])
    out = run(tmp_path, monkeypatch, "U18", book, TRACE_SIGN)
    assert out["status"] == "NG" and "区域整块缺失1处" in out["suggestions"], out


def test_wrong_business_field_cannot_supply_materials_section(monkeypatch, tmp_path):
    book = package()
    book.active["A12"] = "包装材料清单"
    book.active["A13"], book.active["B13"] = "零件号", "Q-17"
    out = run(tmp_path, monkeypatch, "U09", book, PKG_SIGN)
    assert out["status"] == "NG" and "包装材料清单" in out["suggestions"], out


@pytest.mark.parametrize(("interior_gap", "expected"), [(False, "PASS"), (True, "NG")])
def test_word_trace_uses_only_the_intervals_between_read_entries(monkeypatch, tmp_path, interior_gap, expected):
    from docx import Document

    doc = Document()
    doc.add_heading("供应商零件追溯")
    table = doc.add_table(rows=0, cols=3)
    areas = ("基本信息", "分供方追溯", "生产过程追溯", "交付追溯", "追溯状态总结")
    samples = ("华东试制工厂", "检查记录17", "生产记录21", "第18批配送", "关联台账可查询")
    for index, (name, sample) in enumerate(zip(areas, samples, strict=True)):
        title = table.add_row().cells
        title[0].merge(title[2]).text = name
        table.add_row().cells[0].text = sample
        if index == 1 and interior_gap:
            table.add_row()
            table.add_row().cells[0].text = "检查记录18"
        table.add_row()  # A trailing spare row is not an interior hole.
    path = tmp_path / "追溯说明.docx"
    doc.save(path)
    result = frozen(monkeypatch, "U18", path, TRACE_SIGN)
    assert result["status"] == expected, result
    if interior_gap:
        assert "局部空洞1处" in result["suggestions"], result


def test_trace_finds_bilingual_title_after_cover_information(monkeypatch, tmp_path):
    book = trace()
    sheet = book.active
    sheet.title = "交付附件"
    sheet.insert_rows(1, 12)
    sheet["A13"] = "Supplier component traceability\n供应商零件追溯表"
    result = run(tmp_path, monkeypatch, "U18", book, TRACE_SIGN)
    assert result["status"] == "PASS" and "均有实质内容" in result["suggestions"], result


@pytest.mark.parametrize(
    "observed,expected,reason",
    [
        # Every worksheet read and none shows any handwriting or imprint: the original lacks both.
        ({"any_handwriting": False, "any_imprint": False}, "NG", "缺少手写签字、供应商有效印章：已逐表看过原件全部2个工作表"),
        ({"any_handwriting": False, "any_imprint": True, **TRACE_SIGN}, "NG", "缺少手写签字："),
        # Seen somewhere: whether it is the supplier's signing block is read as before.
        ({"any_handwriting": True, "any_imprint": True, **TRACE_SIGN}, "PASS", "有手写签字和供应商印章"),
        # A worksheet the reading could not settle keeps the fact open.
        ({"any_handwriting": None, "any_imprint": None}, "UNABLE", "尚未能从原件核实"),
    ],
)
def test_trace_signing_absent_only_after_every_worksheet_is_read(monkeypatch, tmp_path, observed, expected, reason):
    book = trace()
    book.create_sheet("说明").append(["本表由质量部维护"])
    out = run(tmp_path, monkeypatch, "U18", book, observed)
    assert (out["status"], reason in out["suggestions"]) == (expected, True), out
