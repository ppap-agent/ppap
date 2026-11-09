"""Incomplete original scope cannot establish a complete UAES business result."""

import io
import json
import sys
from types import ModuleType

import fitz
import pytest
from audit.contracts import AuditInput, GroupContextInput, RuleInput
from audit.standard_suites import load_predefined_checkers
from openpyxl import Workbook
from PIL import Image


def _factory(monkeypatch, entry):
    suite = next(item for item in load_predefined_checkers() if item.source_path == f"v5/UAES/{entry}")
    resources = ModuleType("resources")
    exec(suite.files["resources.py"], resources.__dict__)
    monkeypatch.setitem(sys.modules, "resources", resources)
    checker = ModuleType("uaes_scope_" + entry)
    exec(suite.files["checker.py"], checker.__dict__)
    rule = RuleInput(rule_id="frozen-rule", **json.loads(suite.files["rules.json"]))
    return checker.create_audit, rule


def test_special_characteristics_pdf_with_unread_second_page_cannot_pass(tmp_path, monkeypatch):
    with fitz.open() as document:
        page = document.new_page(width=600, height=300)
        for y in (20, 70, 120, 170):
            page.draw_line((20, y), (580, y))
        for x in (20, 580):
            page.draw_line((x, 20), (x, 170))
        for x in (110, 280, 440):
            page.draw_line((x, 70), (x, 170))
        cells = (
            (40, 50, "特殊特性清单"),
            (40, 100, "编号"),
            (130, 100, "产品特性"),
            (300, 100, "特性值"),
            (460, 100, "特性类别"),
            (40, 150, "1"),
            (130, 150, "孔径"),
            (300, 150, "10±0.1"),
            (460, 150, "CC"),
        )
        for x, y, text in cells:
            page.insert_text((x, y), text, fontname="china-s", fontsize=11)
        page = document.new_page(width=600, height=300)
        image = io.BytesIO()
        Image.new("RGB", (200, 100), "black").save(image, format="PNG")
        page.insert_image(fitz.Rect(20, 20, 580, 280), stream=image.getvalue())
        content = document.tobytes()
    path = tmp_path / "partly-scanned.pdf"
    path.write_bytes(content)
    factory, rule = _factory(monkeypatch, "U12")
    audit = factory(AuditInput(audit_id="original", file_url=str(path), customer="UAES"), rule)
    try:
        from audit.checker_lib.source import UnknownFact

        with pytest.raises(UnknownFact, match="未解析页面"):
            audit.native().tables()
        result = audit.evaluate("U12:2")
        assert result.status == "UNABLE", result
        assert result.checks_performed == 0
    finally:
        audit.close()


@pytest.mark.parametrize(("include_unclassified", "wrong_peer_hash", "expected"), [(False, False, "PASS"), (True, False, "UNABLE"), (False, True, "ERR")])
def test_unclassified_candidate_cannot_be_excluded_by_one_known_match(tmp_path, monkeypatch, include_unclassified, wrong_peer_hash, expected):
    factory, rule = _factory(monkeypatch, "U07")

    def original(identity, title, rows):
        path = tmp_path / (identity + ".xlsx")
        book = Workbook()
        book.active.title = title
        for row in rows:
            book.active.append(row)
        book.save(path)
        book.close()
        return AuditInput(audit_id=identity, file_url=str(path), customer="UAES", project_id="project")

    identity = [["项目", "项目A"], ["零件号", "P12345"], ["零件名称", "支架"], ["版本", "A"]]
    target = original("target", "PFMEA", [["PFMEA"], *identity, ["功能/要求", "特殊特性"], ["孔径", "CC"]])
    known = original("special", "特殊特性清单", [["特殊特性清单"], *identity, ["特性名称", "特性类别"], ["孔径", "CC"]])
    if wrong_peer_hash:
        known.file_metadata["source_sha256"] = "0" * 64
    inputs = [target, known]
    if include_unclassified:
        inputs.append(original("unclassified", "原件", [["无法确定原件类型"], ["资料内容不明"]]))
    group = GroupContextInput(by_doc_type={"特殊特性": [known]}, by_audit_id={item.audit_id: item for item in inputs})
    audit = factory(target, rule, group)
    try:
        result = audit.evaluate("U07:3")
        assert result.status == expected, result
        assert result.clause_id == "U07:3"
    finally:
        audit.close()


@pytest.mark.parametrize("observed", [{}, {"key_operation": True}, {"key_operation": "true", "characteristic_marks": True, "legend": True}])
def test_partial_or_malformed_visual_facts_cannot_pass_with_a_real_witness(tmp_path, monkeypatch, observed):
    from audit.checker_lib.source import SourceAudit

    path = tmp_path / "flow.xlsx"
    book = Workbook()
    book.active.append(["过程流程图"])
    book.save(path)
    book.close()

    def visual(self, fields, **options):
        self.witness({"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}, "原件图像已查看", visual=True)
        return observed

    monkeypatch.setattr(SourceAudit, "visual_pdf", visual)
    factory, rule = _factory(monkeypatch, "U16")
    audit = factory(AuditInput(audit_id="original", file_url=str(path), customer="UAES"), rule)
    try:
        assert audit.evaluate("U16:1").status == "UNABLE"
    finally:
        audit.close()
