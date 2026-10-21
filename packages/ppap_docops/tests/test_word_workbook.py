"""The public Word grid preserves source table structure in its workbook view."""

from docx import Document
from ppap_docops.word import extract_docx_structure, word_document_to_workbook


def test_word_tables_preserve_merges_paragraphs_empty_cells_and_order(tmp_path):
    path = tmp_path / "structure.docx"
    document = Document()
    first = document.add_table(rows=2, cols=3)
    first.cell(0, 0).merge(first.cell(0, 1)).text = "跨列"
    first.cell(0, 2).merge(first.cell(1, 2)).text = "跨行"
    first.cell(1, 0).text = "第一段"
    first.cell(1, 0).add_paragraph("第二段")
    first.cell(1, 1).text = ""
    document.add_table(rows=1, cols=1).cell(0, 0).text = "第二张表"
    document.save(path)

    workbook = word_document_to_workbook(extract_docx_structure(path))

    try:
        assert workbook.sheetnames == ["Word表1", "Word表2"]
        assert {str(item) for item in workbook["Word表1"].merged_cells.ranges} == {"A1:B1", "C1:C2"}
        assert workbook["Word表1"]["A1"].value == "跨列"
        assert workbook["Word表1"]["A2"].value == "第一段\n第二段"
        assert workbook["Word表1"]["B2"].value == ""
        assert workbook["Word表2"]["A1"].value == "第二张表"
    finally:
        workbook.close()
