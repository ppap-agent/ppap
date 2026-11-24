"""Revision-3 U12. Only the drawing transcription boundary is substituted."""

import json
import re
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import _load_program, _restore_modules
from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_suites import _CHECKERS_ROOT, _load_leaf
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = _CHECKERS_ROOT
LEAF = LEAVES / "uaes/U12"
BATCH = next((ROOT / "samples").glob("*776*"))
PART = "F01R10Y776"


@pytest.fixture
def leaf(tmp_path):
    modules, saved = _load_program(_load_leaf(LEAVES, "uaes", LEAF).program_files, tmp_path / "program")
    yield modules["checker"]
    _restore_modules(saved)


def workbook(tmp, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "特殊特性清单"
    sheet.append(["序号", "产品名称/零件号", "特殊特性名称", "图纸区域", "特性值", "特性类别"])
    for i, (name, spec, mark, pos) in enumerate(rows, 1):
        sheet.append([i, PART + "机加壳体", name, pos, spec, mark])
    out = tmp / "scl.xlsx"
    book.save(out)
    return out


def drawing(tmp, pages=1, number=PART, scan=False):
    out = tmp / "drawing.pdf"
    with fitz.open() as doc:
        for i in range(pages):
            p = doc.new_page()
            if not scan or i != pages - 1:
                p.insert_text((50, 50), f"GENERAL TOLERANCES\nDWG No: {number}")
        doc.save(out)
    return out


def mark(name="位置度", spec="位置度Φ0.1 A-B G", code="5", view="Y-Y", bbox=(410, 200, 440, 218), frame=(270, 200, 410, 218)):
    return dict(name=name, dimension=spec, mark=code, view=view, bbox=list(bbox), frame=list(frame))


def run(leaf, target, refs):
    def file(p, i, role):
        return AuditInput(audit_id=str(i), file_url=str(p), file_metadata={"role": role, "file_version_id": str(i)})

    target = file(target, 0, "target")
    related = [(file(p, i, role), kind) for i, (p, kind, role) in enumerate(refs, 1)]
    types = {}
    for f, kind in related:
        types.setdefault(kind, []).append(f)
    group = GroupContextInput(by_doc_type=types, by_audit_id={f.audit_id: f for f in (target, *(f for f, _ in related))})
    audit = leaf.create_audit(target, RuleInput.of(json.loads((LEAF / "rules.json").read_text()), rule_id="scl"), group)
    try:
        return audit.evaluate("SCL:3")
    finally:
        audit.close()


def test_a4_drawing_same_point_and_equivalent_grade(leaf, monkeypatch, tmp_path):
    monkeypatch.setattr(UaesSpecialCharacteristicsAgent, "transcribe", lambda *a: {"items": [mark(code="SC/1")], "complete": True})
    target = workbook(tmp_path, [("32#位置度", "位置度Φ0.1 A-B G", "1", "Y-Y")])
    result = run(leaf, target, [(drawing(tmp_path), "unknown", "reference")])
    assert result.status == "PASS", result.suggestions
    assert "双向覆盖" in result.suggestions and "同级" in result.suggestions
    observed = [json.loads(s.locator) for s in result.evidence.sources]
    assert {(p["reading"], p["page"]) for p in observed if p.get("fact") == "drawing_special_mark"} == {(1, 1), (2, 1)}


@pytest.mark.parametrize("filename,row,level,expected", [("特殊特性清单 (1).xlsx", 22, "5", "UNABLE"), ("特殊特性清单.xlsx", 20, "3,5", "NG")])
def test_776_actual_y_y_datum_and_both_lists(leaf, monkeypatch, filename, row, level, expected):
    target = next(BATCH.glob(f"测试文件/11 */{filename}"))
    sheet = load_workbook(target, data_only=True).active
    assert str(sheet[f"F{row}"].value) == "位置度Φ0.1 A-B G" and str(sheet[f"L{row}"].value) == level
    pdf = next(BATCH.glob("比对文件/图纸/Z_*.pdf"))
    with fitz.open(pdf) as doc:
        page = doc[0].rect
    # The reviewed Y-Y callout on the real drawing in PDF points. Only that feature is replayed:
    # incomplete coverage is intentional, not a reference judgment about the remainder of the sheet.
    frame = (2029, 432, 2162, 452)
    flag = (2168, 425, 2191, 453)
    nc, nr = round(page.width / 850), round(page.height / 800)
    w, h = page.width / nc, page.height / nr
    pad = 0.06 * max(w, h)

    def transcribe(_self, _image, _prompt, schema, subject):
        if "views" in schema["properties"]:
            return {"views": True, "heading": None}
        if "drawing_number" in schema["properties"]:
            return {"drawing_number": "F 01R 10Y 776", "designation": "MACHINED HOUSING", "version": "01"}
        tile = int(re.search(r"块(\d+)读取", subject)[1]) - 1
        r, c = divmod(tile, nc)
        area = fitz.Rect(c * w - pad, r * h - pad, (c + 1) * w + pad, (r + 1) * h + pad) & page

        def coords(b):
            return [1000 * (x - (area.x0 if i % 2 == 0 else area.y0)) / (area.width if i % 2 == 0 else area.height) for i, x in enumerate(b)]

        facts = [mark(bbox=coords(flag), frame=coords(frame))] if area.contains(fitz.Rect(frame)) and area.contains(fitz.Rect(flag)) else []
        return {"items": facts, "complete": False}

    monkeypatch.setattr(UaesSpecialCharacteristicsAgent, "transcribe", transcribe)
    refs = [
        (pdf, "unknown", "reference"),
        (next(BATCH.glob("比对文件/图纸/ST*")), "unknown", "reference"),
        (next(BATCH.glob("比对文件/*/*PGQP*.xlsx")), "E12", "reference"),
        (next(BATCH.glob("测试文件/08 */流程图*")), "PFD", "target"),
    ]
    result = run(leaf, target, refs)
    reason = result.suggestions
    assert result.status == expected, reason
    assert "Y-Y" in reason and f"C{row}/F{row}/L{row}" in reason and "F01RD0Y776" in reason, reason
    assert ("类别符号不同" in reason) == (level != "5")
    observed = [json.loads(s.locator) for s in result.evidence.sources]
    assert {p["cell"] for p in observed if p.get("sheet") == sheet.title} >= {f"F{row}", f"L{row}"}
    assert any(p.get("fact") == "drawing_special_mark" and p["bbox"] == list(flag) for p in observed)


def test_three_forms_same_characteristic_with_different_serials(leaf, monkeypatch, tmp_path):
    book = Workbook()
    plan = book.active
    plan.title = "PGQP1"
    plan.append(["Part No/零件号:", PART])
    plan.append(["Items/项目", "SC/特殊特性", "Measuring Equipment\n测量设备", "规格公差", "图纸区域"])
    plan.append(["11#位置度", "3", "CMM", "位置度 Φ0.1 A-B G", "Y-Y"])
    related = tmp_path / "plan.xlsx"
    book.save(related)
    target = workbook(tmp_path, [("32#位置度", "位置度 Φ0.1 A-B G", "3", "Y-Y")])
    monkeypatch.setattr(UaesSpecialCharacteristicsAgent, "transcribe", lambda *a: {"items": [mark(code="3")], "complete": True})
    result = run(leaf, target, [(drawing(tmp_path), "unknown", "reference"), (related, "E12", "reference")])
    assert result.status == "PASS", result.suggestions
    assert "同号图纸1份、PGQP1 1份" in result.suggestions and "双向覆盖" in result.suggestions
