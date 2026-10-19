import io
import json

import pytest
from docx import Document
from openpyxl import Workbook
from ppap_application.rule_sources import rule_sources, validate_registration_sources


def test_word_sources_use_actual_paragraph_and_physical_table_coordinates():
    doc = Document()
    doc.add_paragraph("")
    doc.add_paragraph("适用范围")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "编号"
    table.cell(0, 1).text = "条款"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "必须填写零件号\n并填写版本"
    out = io.BytesIO()
    doc.save(out)
    units = rule_sources("规则.docx", out.getvalue())
    assert {unit["location"]: unit["excerpt"] for unit in units} == {
        "段落 2": "适用范围",
        "表 1 · 行 1 · 列 1": "编号",
        "表 1 · 行 1 · 列 2": "条款",
        "表 1 · 行 2 · 列 1": "1",
        "表 1 · 行 2 · 列 2": "必须填写零件号\n并填写版本",
    }


def test_excel_json_markdown_and_pdf_sources_are_read_from_bytes():
    workbook = Workbook()
    workbook.active["B4"] = "填写版本"
    out = io.BytesIO()
    workbook.save(out)
    assert rule_sources("规则.xlsx", out.getvalue())[0] == {"id": "xlsx:Sheet:B4", "location": "Sheet · B4", "excerpt": "填写版本"}
    assert rule_sources("规则.json", json.dumps({"rules": [{"id": "A", "rule_to_check": "填写版本"}]}).encode())[0]["location"] == "rules[0].rule_to_check"
    assert rule_sources("规则.md", "# 标题\n\n填写版本".encode())[-1]["location"] == "第 3 行"
    import fitz

    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Check revision")
        assert rule_sources("rules.pdf", doc.tobytes())[0]["location"] == "第 1 页 · 区块 1"


def test_registration_rejects_fabricated_location_and_foreign_file_version():
    units = [{"id": "p:1", "location": "段落 1", "excerpt": "必须填写零件号"}]
    clause = {
        "id": "1",
        "title": "零件号",
        "text": "必须填写零件号",
        "source": "规则.docx",
        "original": "必须填写零件号",
        "location": "段落 1",
        "checked": True,
        "provenance": {**units[0], "fileVersionId": "v1"},
    }
    body = {"rules": [{"id": "1", "category": "零件号", "rule_to_check": "必须填写零件号"}]}

    def context():
        return {"registration": json.dumps({"basis": {"source": {"filename": "规则.docx"}, "clauses": [clause]}})}

    validate_registration_sources(context(), body, "v1", "规则.docx", units)
    for patch in ({"location": "已核对"}, {"provenance": {**units[0], "fileVersionId": "v2"}}, {"original": "编造原文"}, {"checked": False}):
        changed = {**clause, **patch}
        with pytest.raises(ValueError, match="rule_source_unverified"):
            validate_registration_sources({"registration": json.dumps({"basis": {"source": {"filename": "规则.docx"}, "clauses": [changed]}})}, body, "v1", "规则.docx", units)
    validate_registration_sources({}, body, "v1", "规则.docx", units)
    excluded = {**clause, "id": "2", "applicability": "exclude", "reason": "不适用于本次资料"}
    validate_registration_sources({"registration": json.dumps({"basis": {"source": {"filename": "规则.docx"}, "clauses": [clause, excluded]}})}, body, "v1", "规则.docx", units)
