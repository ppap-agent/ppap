import io
import json

import pytest
from docx import Document
from openpyxl import Workbook
from worker.intake.rule_sources import rule_sources, validate_registration_sources


def test_word_source_includes_insertions_and_excludes_deleted_revision_text():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "产品、"
    for tag, text_tag, text in (("ins", "t", "过程"), ("del", "delText", "旧字段")):
        change, run, value = OxmlElement("w:" + tag), OxmlElement("w:r"), OxmlElement("w:" + text_tag)
        change.set(qn("w:id"), "1" if tag == "ins" else "2")
        value.text = text
        run.append(value)
        change.append(run)
        cell.paragraphs[0]._p.append(change)
    out = io.BytesIO()
    doc.save(out)
    assert rule_sources("rules.docx", out.getvalue())[0]["excerpt"] == "产品、过程"
    from documents.extractors.word import WordExtractor

    text = WordExtractor().extract_text(out.getvalue())
    assert "产品、过程" in text
    assert "旧字段" not in text


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


def test_deleted_tables_and_nested_tables_preserve_following_source_ids():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    deleted = doc.add_table(rows=1, cols=1)
    deleted.cell(0, 0).text = "已删除的表格条款"
    deleted.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "已删除表格中的嵌套条款"
    active = doc.add_table(rows=1, cols=1)
    active.cell(0, 0).text = "有效条款"
    deleted_nested = active.cell(0, 0).add_table(rows=1, cols=1)
    deleted_nested.cell(0, 0).text = "有效表格中已删除的嵌套条款"
    for revision_id, table in enumerate((deleted, deleted_nested), 1):
        revision = OxmlElement("w:del")
        revision.set(qn("w:id"), str(revision_id))
        table._tbl.addprevious(revision)
        revision.append(table._tbl)
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "后续有效条款"
    out = io.BytesIO()
    doc.save(out)

    assert rule_sources("rules.docx", out.getvalue()) == [
        {"id": "docx:3:1:1", "location": "表 3 · 行 1 · 列 1", "excerpt": "有效条款"},
        {"id": "docx:5:1:1", "location": "表 5 · 行 1 · 列 1", "excerpt": "后续有效条款"},
    ]


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
