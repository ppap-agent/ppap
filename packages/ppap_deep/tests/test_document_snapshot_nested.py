"""Nested Word tables retain their own source identity and enclosing cell."""

import pytest
from docx import Document
from docx.oxml import OxmlElement
from ppap_deep.semantic.document_tools import ParserLimitError, ReadLimits, snapshot
from ppap_deep.semantic.evidence import resolve
from ppap_deep.semantic.models import WordCell


def cell(data, table, row, column):
    return resolve(data, WordCell(kind="docx_cell", table=table, row=row, column=column))


def test_xml_order_and_merged_parent_keep_nested_text_in_one_source(tmp_path):
    document = Document()
    outer = document.add_table(rows=2, cols=3)
    outer.cell(0, 0).text = "Rule"
    outer.cell(0, 1).text = "Body"
    outer.cell(1, 0).text = "7"
    parent = outer.cell(1, 1).merge(outer.cell(1, 2))
    parent.text = "Outer text"
    nested = parent.add_table(rows=1, cols=2)
    nested.cell(0, 0).text = "Nested id"
    nested.cell(0, 1).text = "Nested clause"
    deepest = nested.cell(0, 1).add_table(rows=1, cols=1)
    deepest.cell(0, 0).text = "Deep clause"
    following = document.add_table(rows=1, cols=1)
    following.cell(0, 0).text = "Following clause"
    path = tmp_path / "nested.docx"
    document.save(path)
    data = snapshot(path)
    assert [table["id"] for table in data["tables"]] == ["table:1", "table:2", "table:3", "table:4"]
    assert data["tables"][1]["parent_cell"] == {"table": "table:1", "cell": "B2"}
    assert data["tables"][2]["parent_cell"] == {"table": "table:2", "cell": "B1"}
    assert data["tables"][0]["cells"]["C2"]["anchor"] == "B2"
    assert cell(data, 1, 2, 2).values == cell(data, 1, 2, 3).values == ("Outer text",)
    assert cell(data, 2, 1, 2).values == ("Nested clause",)
    assert cell(data, 3, 1, 1).values == ("Deep clause",)
    assert cell(data, 4, 1, 1).values == ("Following clause",)
    values = [item["value"] for table in data["tables"] for address, item in table["cells"].items() if item["anchor"] == address]
    assert sum("Deep clause" in text for text in values) == 1
    assert sum("Nested clause" in text for text in values) == 1


def test_non_nested_table_and_merged_cell_locations_are_unchanged(tmp_path):
    document = Document()
    first = document.add_table(rows=1, cols=2)
    first.cell(0, 0).merge(first.cell(0, 1)).text = "Shared field"
    second = document.add_table(rows=1, cols=1)
    second.cell(0, 0).text = "Second table"
    path = tmp_path / "plain.docx"
    document.save(path)
    data = snapshot(path)
    assert [table["id"] for table in data["tables"]] == ["table:1", "table:2"]
    assert all("parent_cell" not in table for table in data["tables"])
    assert cell(data, 1, 1, 1).values == cell(data, 1, 1, 2).values == ("Shared field",)
    assert data["tables"][0]["cells"]["B1"]["anchor"] == "A1"
    assert cell(data, 2, 1, 1).values == ("Second table",)


def test_deleted_tables_and_nested_tables_are_unavailable_without_renumbering(tmp_path):
    document = Document()
    deleted = document.add_table(rows=1, cols=1)
    deleted.cell(0, 0).text = "Deleted outer clause"
    deleted.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = "Deleted descendant clause"
    active = document.add_table(rows=1, cols=1)
    active.cell(0, 0).text = "Active parent clause"
    deleted_nested = active.cell(0, 0).add_table(rows=1, cols=1)
    deleted_nested.cell(0, 0).text = "Deleted nested clause"
    for table in (deleted, deleted_nested):
        revision = OxmlElement("w:del")
        table._tbl.addprevious(revision)
        revision.append(table._tbl)
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Following active clause"
    path = tmp_path / "deleted-tables.docx"
    document.save(path)

    data = snapshot(path)
    assert [table["id"] for table in data["tables"]] == ["table:3", "table:5"]
    assert cell(data, 3, 1, 1).values == ("Active parent clause",)
    for table_number in (1, 2, 4):
        with pytest.raises(ValueError, match="Evidence table missing"):
            cell(data, table_number, 1, 1)
    assert cell(data, 5, 1, 1).values == ("Following active clause",)
    for budget, message in ((ReadLimits(tables=4), "tables"), (ReadLimits(cells=4), "cell visits")):
        with pytest.raises(ParserLimitError, match=message):
            snapshot(path, limits=budget)


@pytest.mark.parametrize("budget, message", [(ReadLimits(tables=2), "tables"), (ReadLimits(cells=2), "cell visits"), (ReadLimits(paragraphs=3), "paragraphs")])
def test_nested_sources_count_toward_existing_parser_limits(tmp_path, budget, message):
    document = Document()
    parent = document.add_table(rows=1, cols=1).cell(0, 0)
    parent.text = "Outer"
    parent.add_table(rows=1, cols=1).cell(0, 0).text = "Nested"
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Following"
    path = tmp_path / "bounded.docx"
    document.save(path)
    with pytest.raises(ParserLimitError, match=message):
        snapshot(path, limits=budget)
