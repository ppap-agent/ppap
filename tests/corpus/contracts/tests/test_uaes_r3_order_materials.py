"""Revision-3 MSL:3 / ORD:1, frozen candidate entry point. Visual transcriptions here are fixtures, not live model tests."""

import hashlib
import json
import re
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import load_workbook

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = UAES / "rulebook/v1.0/candidates"
PART = next((UAES / "samples").glob("F01R10Y776*"))
MSL = next(PART.rglob("7.*776.xlsx"))
DRAWING = next(PART.rglob("Z_F01R10Y776.pdf"))
BOM = next(PART.rglob("ST_F01R10Y776.pdf"))
ORDER = next((UAES / "samples").rglob("BV_F01RL0Y033*.pdf"))


def run(leaf, clause, target, refs=(), reader=None):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    sources, inputs = {}, []
    for i, p in enumerate([target, *refs]):
        key = f"f{i}"
        sources[key] = p.read_bytes()
        inputs.append(FrozenInput(key, hashlib.sha256(sources[key]).hexdigest(), "target" if not i else "reference", p.name, document_type=suite.document_type if not i else "图纸"))
    digest = checker_digest(suite.program_files)
    bind = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    obj = FrozenTarget("f0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), bind)
    snap = ExecutionSnapshot("test", "candidate", tuple(inputs), (obj,), "test", library_revision(), {})
    rt = CheckerRuntime()
    if reader:
        rt.read_ocr_visual_facts = reader
    return run_audit(snap, sources, {digest: suite.program_files}, runtime=rt)["findings"][0]


def sources(result):
    return [(json.loads(s["locator"]), s["excerpt"]) for s in result["evidence"]["sources"]]


def visual_title(**_):
    # Manually transcribed visual seam. Bounding boxes are relative to the real lower-right title crop.
    return {
        "kind": "drawing",
        "number": {"label": "图号 / DWG No.", "value": "F 01R 10Y 776", "bbox": [0.817, 0.865, 0.920, 0.928]},
        "name": {"label": "名称 / DESIGNATION", "value": "机加工壳体\nMACHINED HOUSING", "bbox": [0.817, 0.800, 0.957, 0.865]},
        "project": None,
        "status": None,
    }


def test_real_materials_number_mismatch_not_blocked_by_parts_list():
    out = run("U04", "MSL:3", MSL, [BOM, DRAWING], visual_title)
    assert out["status"] == "NG", out
    assert all(s in out["suggestions"] for s in ("比对源错配", "F01RD 0Y77601", "F 01R 10Y 776", "未比较零件名称"))
    assert {(loc.get("file_version_id"), loc.get("fact"), excerpt) for loc, excerpt in sources(out)} >= {
        ("f1", "清单表头编号", "F01R10Y776"),
        ("f1", "所属图纸", "F01R10Y776"),
        ("f2", "number", "图号 / DWG No.: F 01R 10Y 776"),
    }
    assert not any("F01RL0Y033" in e for _, e in sources(out))


def test_parts_list_is_not_an_independent_part_drawing():
    out = run("U04", "MSL:3", MSL, [BOM])
    assert out["status"] == "NG" and "没有图纸独立文件" in out["suggestions"]


def native_drawing(path, number="F01RD 0Y77601", designation="Housing", extra=()):
    with fitz.open() as d:
        p = d.new_page()
        for y, t in enumerate(["Drawing", "DWG No.: " + number, "Designation: " + designation, "Project: P123", "Related part: F01RD 0Y77601", *extra], 1):
            p.insert_text((48, y * 35), t)
        d.save(path)
    return path


@pytest.mark.parametrize(
    "number,designation,status,reason",
    [
        ("F01RD0Y77601", "Housing", "PASS", "一致"),
        ("F01Rd0Y77601", "Housing", "NG", "比对源错配"),
        ("F01RD0Y776", "Housing", "NG", "比对源错配"),
        ("F01RD0Y77601", "Other", "NG", "零件名称不一致"),
    ],
)
def test_native_title_exact_number_then_content(tmp_path, number, designation, status, reason):
    w = load_workbook(MSL)
    w.active["C3"], w.active["F3"] = "P123", "Housing"
    path = tmp_path / "list.xlsx"
    w.save(path)
    out = run("U04", "MSL:3", path, [BOM, native_drawing(tmp_path / "r.pdf", number, designation)])
    assert (out["status"], reason in out["suggestions"]) == (status, True), out


def test_two_active_drawings_may_use_different_designations(tmp_path):
    w = load_workbook(MSL)
    w.active["C3"], w.active["F3"] = "P123", "Housing"
    path = tmp_path / "list.xlsx"
    w.save(path)
    refs = [native_drawing(tmp_path / f"{i}.pdf", designation=name, extra=["Drawing Status: Active"]) for i, name in enumerate(["Other", "Housing"])]
    out = run("U04", "MSL:3", path, refs)
    assert out["status"] == "PASS" and "1.pdf" in out["suggestions"], out


def role(kind, label, state, box):
    return {"kind": kind, "label": label, "state": state, "bbox": box}


STAMP = role("controlled_stamp", "EN 受控 EN CONTROL", "visible", [0.33, 0.56, 0.88, 0.76])
SUP = role("supplier_slot", "Supplier signature", "signed", [0.08, 0.15, 0.80, 0.27])
CUS = role("customer_slot", "Customer signature", "signed", [0.08, 0.28, 0.80, 0.40])


def probe(log, page_objects, incomplete=(), local=True):
    def read(**kw):
        if kw["response_schema"]["name"] != "order_fields":
            return {"visible": local}
        pages = [int(n) for n in re.search(r"原页号：([\d、]+)", kw["prompt"])[1].split("、")]
        assert len(pages) == len(kw["images"])
        log.extend(pages)
        return {"pages": [{"page": n, "slots_complete": n not in incomplete, "seals_complete": n not in incomplete, "objects": page_objects.get(n, [])} for n in pages]}

    return read


def order_doc(path, signed):
    with fitz.open() as d, fitz.open(ORDER) as original:
        p = d.new_page(width=400, height=500)
        p.insert_text((42, 112), "Supplier signature:")
        p.insert_text((42, 174), "Customer signature:")
        for y, present in [(111, signed), (172, True)]:
            p.draw_line((150, y + 6), (260, y + 6))
            if present:
                p.draw_bezier((155, y), (180, y - 40), (150, y + 18), (195, y - 9))
                p.draw_bezier((195, y - 9), (176, y + 15), (212, y - 5), (226, y - 4))
        # Real controlled imprint from the BV original, not a printed placeholder that says "stamp".
        p.show_pdf_page(fitz.Rect(132, 280, 352, 380), original, 0, clip=fitz.Rect(197, 477, 521, 642))
        d.new_page(width=400, height=500).insert_text((40, 100), "Notes")
        d.save(path)
    return path


def test_real_order_whole_document_and_native_revision_facts():
    log = []
    result = run("U14", "ORD:1", ORDER, reader=probe(log, {n: [STAMP] for n in range(1, 18)}))
    assert log == list(range(1, 18)), (log, result)
    assert result["status"] == "NG", result
    assert "未见设栏，不是已设栏空白" in result["suggestions"] and "EC10000417" in result["suggestions"]
    assert any("编制：李晓" in e and "校对：-" in e and "批准：-" in e and loc.get("page") == 1 for loc, e in sources(result))


@pytest.mark.parametrize("signed", [True, False])
def test_order_declared_roles_and_seal(tmp_path, signed):
    # Valid business examples, one signed by both sides and one with a genuinely unfilled supplier line.
    doc = order_doc(tmp_path / "order.pdf", signed=signed)
    sup = SUP if signed else {**SUP, "state": "blank"}
    out = run("U14", "ORD:1", doc, reader=probe([], {1: [sup, CUS, STAMP]}))
    assert out["status"] == ("PASS" if signed else "NG"), out
    assert ("全部2页" if signed else "供方责任签署栏空白未签") in out["suggestions"]


@pytest.mark.parametrize(
    "grade,standard,equal",
    [
        ("11SMn30", "DIN EN 10277:2018-09", True),
        ("11SMn300", "DIN EN 10277:2018-09", False),
        ("11SMn30", "DIN EN 10277:2018-08", False),
    ],
)
def test_material_standard_pairs_real_bosch_spec_with_drawing_field(tmp_path, grade, standard, equal):
    spec = next((UAES / "samples").rglob("*N28_AS0715*.pdf"))
    drawing = tmp_path / "drawing.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        for i, line in enumerate(["Engineering drawing", f"Material Grade: {grade}", f"Material Standard: {standard}", "See also 11SMn30 DIN EN 10277:2018-09"]):
            page.insert_text((50, 50 + 20 * i), line)
        doc.save(drawing)
    finding = run("U10", "MSPEC:1", spec, [drawing])
    assert "11SMn30" in finding["suggestions"] and "DIN EN 10277:2018-09" in finding["suggestions"], finding
    evidence = {(r.get("file_version_id"), r.get("page"), r.get("fact"), e) for r, e in sources(finding)}
    assert {("f0", 2, "grade", "11SMn30"), ("f0", 2, "standard", "DIN EN 10277:2018-09"), ("f1", 1, "grade", grade), ("f1", 1, "standard", standard)} <= evidence
    if equal:
        assert "的材料字段一致" in finding["suggestions"]  # Stamp is a separate, unmodified sub-check.
    else:
        assert finding["status"] == "NG" and "不一致" in finding["suggestions"], finding
