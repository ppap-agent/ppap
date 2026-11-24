"""PSW:3 through the frozen installed entry point. The visual transport below is an offline test double, not a model rerun."""

import hashlib
import json
from collections import Counter
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from agent.execution.audit_runtime import run_audit
from agent.execution.checker_runtime import CheckerRuntime
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.standard_suites import _CHECKERS_ROOT, _load_leaf, library_revision
from PIL import Image, ImageDraw

UAES = Path(__file__).resolve().parents[4] / "assets/uaes"
LEAVES = _CHECKERS_ROOT
BOSCH = next((UAES / "samples").rglob("PSW_Bosch signed.pdf"))
A40 = next((UAES / "samples").glob("F01RD0YA40*"))


def finding(path, reader):
    suite = _load_leaf(LEAVES, "uaes", LEAVES / "uaes/U17")
    digest = checker_digest(suite.program_files)
    raw = path.read_bytes()
    original = FrozenInput("warrant", hashlib.sha256(raw).hexdigest(), "target", path.name, document_type=suite.document_type)
    binding = RuleBinding(suite.suite_key, digest, {"PSW:3": suite.entrypoints["PSW:3"]})
    target = FrozenTarget("warrant", "sha256:" + suite.revision_digest, suite.suite_key, suite.revision_digest, json.loads(suite.files["rules.json"]), binding)
    snapshot = ExecutionSnapshot("test", "r3", (original,), (target,), "test", library_revision(), {})
    [result] = run_audit(snapshot, {"warrant": raw}, {digest: suite.program_files}, runtime=reader)["findings"]
    return result


def sources(result, fact):
    return [(loc, s["excerpt"]) for s in result["evidence"]["sources"] if (loc := json.loads(s["locator"])).get("fact") == fact]


class LocalReadings(CheckerRuntime):
    def __init__(self, *, panels, crops=(), mark="handwriting", label="Supplier Authorized Signature", title="Quality Engineer", role="Quality Engineer"):
        super().__init__()
        self.panels, self.crops = iter(panels), iter(crops)
        self.mark, self.label, self.title, self.role = mark, label, title, role
        self.calls = Counter()

    def read_ocr_visual_facts(self, *, images, prompt, response_schema):
        keys = response_schema["schema"]["properties"]
        if "title" in keys:
            self.calls["fields"] += 1
            # Harmless contact placeholders isolate signature/seal behavior. Legacy global true is deliberately noisy.
            fields = {k: {"present": True, "value": v} for k, v in {"title": self.title, "phone": "123456789", "print_name": "A Tester", "signed_date": "23.6.2023", "email": "a@example.com"}.items()}
            fields["title"]["role_as_written"] = self.role
            return {**fields, "supplier_authorized_signature": {"present": True, "handwritten": True, "field_box": [35, 748, 715, 783]}, "ink_seal": True}
        if "entire_field" in keys:
            self.calls["signature"] += 1
            return {"entire_field": True, "label": self.label, "mark": self.mark, "detail": "签字线处的笔迹或数字签署字样"}
        if "fully_examined" in keys:
            self.calls["panels"] += 1
            return next(self.panels)
        self.calls["crops"] += 1
        return next(self.crops)


CLEAR = {"fully_examined": True, "candidates": []}
LOGO = [206, 15, 351, 94]  # Header of the upper view, Bosch form.
DIGITAL = [168, 704, 338, 748]  # Customer signature in lower view.
IMPRINT = {"kind": "imprint", "detail": "独立椭圆印迹内印有 QC 文字"}
LOGO_READ = {"kind": "printed_logo", "detail": "Ford 印刷商标"}


def test_bosch_logo_and_digital_signature_are_not_an_imprint():
    reader = LocalReadings(
        panels=[{**CLEAR, "candidates": [LOGO]}, {**CLEAR, "candidates": [DIGITAL]}], crops=[LOGO_READ, {"kind": "digital_signature", "detail": "数字签名文本框"}], title="Swoboda CZ", role=None
    )
    result = finding(BOSCH, reader)
    assert result["status"] == "NG" and "未找到印章" in result["suggestions"]
    stamps = sources(result, "ink_seal")
    assert len([b for b, text in stamps if "完整查看" in text]) == 2
    assert all(b["kind"] == "pdf_region" and b["source_sha256"] == hashlib.sha256(BOSCH.read_bytes()).hexdigest() for b, _ in stamps)
    assert any(b["bbox"][1] < 25 and b["bbox"][2] < 240 and "Ford" in text for b, text in stamps)
    assert any(b["bbox"][1] > 680 and "数字签名" in text for b, text in stamps)
    assert any(b["bbox"][1] > 600 for b, _ in sources(result, "supplier_authorized_signature"))
    assert reader.calls == {"fields": 1, "signature": 1, "panels": 2, "crops": 2}


def dummy_warrant(tmp_path):
    # Separate grayscale positive control, with an actual painted QC test imprint far above the signature.
    source = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(source)
    draw.ellipse((105, 110, 225, 181), outline="black", width=4)
    draw.text((148, 140), "QC", fill="black")
    draw.text((40, 755), "Supplier Authorized Signature", fill="black")
    draw.line([(460, 770), (465, 750), (470, 770), (490, 760)], fill="black", width=3)
    out = BytesIO()
    source.save(out, format="PNG")
    path = tmp_path / "synthetic.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=500)
        page.insert_image(page.rect, stream=out.getvalue())
        doc.save(path)
    return path


def test_local_imprint_outside_signature_in_grayscale_warrant(tmp_path):
    reader = LocalReadings(panels=[{**CLEAR, "candidates": [[96, 185, 235, 346]]}], crops=[IMPRINT])
    result = finding(dummy_warrant(tmp_path), reader)
    assert result["status"] == "PASS", result["suggestions"]
    [(loc, excerpt)] = sources(result, "ink_seal")
    assert "QC" in excerpt and 45 <= loc["bbox"][0] < 52 and loc["bbox"][3] < 100
    assert reader.calls["panels"] == 1  # No full-file negative search needed after a verified witness.


@pytest.mark.parametrize("normal", [True, False])
def test_xls_native_drawing_bindings_and_inserted_handwriting(normal):
    class Pictures(CheckerRuntime):
        def read_ocr_visual_facts(self, *, images, **unused):
            # Crops are the original BIFF shapes, independently inspected: logo, round red imprint, black handwriting.
            with Image.open(BytesIO(images[0])) as img:
                size = img.size
            kind, detail = {(109, 40): ("printed_logo", "UAES 印刷标志"), (348, 324): ("imprint", "圆章中心五角星，周边为公司名称"), (100, 106): ("handwriting", "独立手写笔迹图片")}[size]
            return {"kind": kind, "detail": detail}

    path = next(A40.rglob("*Warrant_V03.xls" if normal else "*无签字盖章.xls"))
    result = finding(path, Pictures())
    assert result["status"] == ("UNABLE" if normal else "NG"), result["suggestions"]
    if normal:
        assert "嵌入" in result["suggestions"]
        assert any("五角星" in s["excerpt"] and json.loads(s["locator"])["start"] == "K57" for s in result["evidence"]["sources"])
    else:
        assert "手写签名" in result["suggestions"] and "未找到印章" in result["suggestions"]
        sheet_names = {loc["sheet"] for s in result["evidence"]["sources"] if (loc := json.loads(s["locator"])).get("scope") == "worksheet_drawing_inventory"}
        assert sheet_names == {"360Qex", "history", "PSW (with explanation)"}
