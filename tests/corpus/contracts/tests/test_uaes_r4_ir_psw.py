"""Signed m4 IR/PSW candidates, through frozen execution. Visual responses substituted only at the reading boundary."""

import hashlib
import json
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _load_leaf, library_revision
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelPicture
from PIL import Image

BASE = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = BASE / "rulebook/v1.0/candidates/m4"
BATCH = next((BASE / "samples").glob("F01RD0YA40*/测试文件"))
RELEASE = next(BATCH.glob("17 */*.pdf"))
REPORT = next(BATCH.glob("01-1 */*.xls"))
PART = "F01RD0YA4001"
SUPPLIER = "宁波市法莱欣科技有限公司"
TITLES = ("尺寸报告", "材料报告", "功能报告", "可靠性报告")


def finding(leaf, clause, path, refs=(), reader=None):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes" / leaf)
    digest = checker_digest(suite.program_files)
    binding = RuleBinding(suite.suite_key, digest, {clause: suite.entrypoints[clause]})
    target = FrozenTarget("0", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    inputs, files = [], {}
    for i, (p, kind) in enumerate([(path, suite.document_type), *refs]):
        data = p.read_bytes()
        files[str(i)] = data
        inputs.append(FrozenInput(str(i), hashlib.sha256(data).hexdigest(), "reference" if i else "target", p.name, document_type=kind))
    snapshot = ExecutionSnapshot("test", "r4", tuple(inputs), (target,), "test", library_revision(), {})
    [answer] = run_audit(snapshot, files, {digest: suite.program_files}, runtime=reader)["findings"]
    return answer


def picture(kind):
    png = BytesIO()
    Image.new("RGB", (kind + 2, 5), "black").save(png, "PNG")
    png.seek(0)
    return png


def reports(tmp_path, *, signature=True, seal=None, seal_sheet=0, engineer=False):
    book = Workbook()
    book.remove(book.active)
    for i, title in enumerate(TITLES):
        ws = book.create_sheet(title)
        ws["A1"], ws["J1"] = "Supplier/供应商：" + SUPPLIER, "Part Number/零件号：" + PART
        ws["A2"], ws["B2"], ws["C2"], ws["F2"] = "No.", "Requirements/要求", "Actual-Value Supplier/供应商实测值", "Evaluation"
        ws.merge_cells("C2:E2")
        ws["A3"], ws["B3"], ws["C3"], ws["D3"], ws["E3"], ws["F3"] = 23, "10±0.1", 10.2, 10.0, 10.0, "OK"
        if engineer:
            ws["G2"], ws["G3"] = "顾客工程师", "王工"
        if i == 3:
            # Existing but empty report does not need a signature or count toward the seal.
            for row in ws["A3:F3"]:
                for cell in row:
                    cell.value = None
        ws["B6"], ws["J6"] = "供应商负责人", "客户负责人"
        if i < 3 and (i != 1 or signature):
            ws.add_image(ExcelPicture(picture(1)), "C6")
        if seal is not None and i == seal_sheet:
            ws.add_image(ExcelPicture(picture(seal)), "F6")
    out = tmp_path / "report.xlsx"
    book.save(out)
    return out


class Pictures(CheckerRuntime):
    def read_ocr_visual_facts(self, *, images, **_):
        # Synthetic image widths distinguish the injected observations, not production detection.
        width = Image.open(BytesIO(images[0])).width
        return {"handwritten_signature": width == 3, "ink_seal": width != 3, "issuer": SUPPLIER if width != 6 else "其他有限公司", "seal_type": "inspection" if width == 5 else "company"}


@pytest.mark.parametrize(("seal", "sheet", "signature", "status"), [(2, 0, True, "PASS"), (2, 3, True, "NG"), (2, 0, False, "NG"), (3, 1, True, "UNABLE"), (4, 0, True, "NG")])
def test_stamp_scope_owner_and_open_question(tmp_path, seal, sheet, signature, status):
    result = finding("U01", "IR:3", reports(tmp_path, seal=seal, seal_sheet=sheet, signature=signature), reader=Pictures())
    assert result["status"] == status, result["suggestions"]


def test_original_report_signatures_with_a_visual_substitute():
    class Signatures(CheckerRuntime):
        def read_ocr_visual_facts(self, **_):
            return {"handwritten_signature": True, "ink_seal": False, "issuer": None, "seal_type": None}

    result = finding("U01", "IR:3", REPORT, reader=Signatures())
    assert result["status"] == "NG" and "没有任何有效印章" in result["suggestions"]
    assert "可靠性" not in result["suggestions"]


def test_general_release_with_matching_part_and_supplier_still_not_a_waiver(tmp_path):
    result = finding("U01", "IR:1", reports(tmp_path), [(RELEASE, "E11")])
    assert result["status"] == "NG" and "仅笼统认可" in result["suggestions"], result["suggestions"]
    assert any("完全认可" in s["excerpt"] and json.loads(s["locator"]).get("file_version_id") == "1" for s in result["evidence"]["sources"])


def test_ambiguous_customer_initial_on_the_actual_exceedance(tmp_path):
    result = finding("U01", "IR:1", reports(tmp_path, engineer=True))
    assert result["status"] == "UNABLE", result["suggestions"]


class Form(CheckerRuntime):
    def __init__(self, comment, level):
        super().__init__()
        self.comment, self.level = comment, level

    def read_ocr_visual_facts(self, *, response_schema, **_):
        keys = response_schema["schema"]["properties"]
        out = {k: {"present": True, "value": "2026-08-01"} for k in keys}
        if "explanation" in keys:
            out.update(
                engineering_change_level={"present": True, "value": self.level},
                safety_government_regulation={"present": True, "options": [{"label": "No", "checked": True}]},
                weight={"present": True, "value": "0.25 kg"},
                explanation={"present": True, "value": self.comment},
            )
        if "reason_for_submission" in keys:
            out["reason_for_submission"] = {"present": True, "options": []}
        return out


@pytest.mark.parametrize(
    ("comment", "value", "level", "status"),
    [
        ("见《附表.xlsx》生产!B2", "生产节拍：20秒", "A", "PASS"),
        ("见《附表.xlsx》生产!B2", None, "A", "NG"),
        ("见《附表.xlsx》生产!B2", "待定", "A", "NG"),
        ("见《附表.xlsx》", "生产节拍：20秒", "A", "UNABLE"),
        ("见《缺件.xlsx》生产!B2", "生产节拍：20秒", "A", "NG"),
        ("见附件", None, "A", "NG"),
        ("生产节拍：20秒", None, "待定", "UNABLE"),
    ],
)
def test_psw_explanation_is_data_not_just_a_reference(tmp_path, comment, value, level, status):
    path = tmp_path / "form.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(path)
    appendix = tmp_path / "附表.xlsx"
    book = Workbook()
    book.active.title = "生产"
    book.active["B2"] = value
    book.active["B3"] = "生产节拍：50秒"  # Uncited data in another cell must not rescue an empty cited cell.
    book.save(appendix)
    result = finding("U17", "PSW:1", path, [(appendix, "附件")], Form(comment, level))
    assert result["status"] == status, result["suggestions"]
    if status == "PASS":
        assert any(s["excerpt"] == value and json.loads(s["locator"]).get("file_version_id") == "1" and json.loads(s["locator"]).get("cell") == "B2" for s in result["evidence"]["sources"])


@pytest.mark.parametrize(
    ("item", "supplier", "customer", "expected"),
    [("23", SUPPLIER, "联合汽车电子有限公司", "PASS"), ("123", SUPPLIER, "联合汽车电子有限公司", "NG"), ("23", "另一公司", "联合汽车电子有限公司", "NG"), ("23", SUPPLIER, "另一客户", "UNABLE")],
)
def test_specific_acceptance_has_customer_supplier_and_exact_item_scope(tmp_path, item, supplier, customer, expected):
    # A newly generated single-page release; no customer's signed document is modified.
    path = tmp_path / "release.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=620, height=620)
        grid = [
            ("C Sample Release Report", "", ""),
            (customer, "", ""),
            ("公司名称\nName：", supplier, ""),
            ("零件号\nPart No.", PART, ""),
            ("UAES Condition of approval / remark\n" + f"同意第{item}项尺寸偏差，要求：10±0.1，同意本次实测值", "", ""),
            ("批准：李工程师 2026-09-14\nApproved by：", "", ""),
        ]
        xs = [15, 330, 510, 600]
        for i, cells in enumerate(grid):
            for j, content in enumerate(cells):
                x, y = xs[j], 10 + i * 60
                page.draw_rect(fitz.Rect(x, y, xs[j + 1], y + 60))
                for k, line in enumerate(content.splitlines()):
                    page.insert_text((x + 3, y + 14 + k * 17), line, fontsize=9, fontname="china-s" if any(ord(v) > 127 for v in line) else "helv")
        doc.save(path)
    result = finding("U01", "IR:1", reports(tmp_path), [(path, "E11")])
    assert result["status"] == expected, result["suggestions"]
