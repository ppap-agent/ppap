"""Application rule sources and Deep observations share original Word locations."""

from pathlib import Path

from audit.document_tools import snapshot
from audit.evidence import resolve
from audit.models import WordCell
from docx import Document
from docx.oxml import OxmlElement
from worker.intake.rule_sources import rule_sources


def assert_source_matches(path, table, row, column):
    original = path.read_bytes()
    unit = next(unit for unit in rule_sources(path.name, original) if unit["id"] == f"docx:{table}:{row}:{column}")
    observed = resolve(snapshot(path), WordCell(kind="docx_cell", table=table, row=row, column=column))
    assert observed.known
    assert observed.values == (unit["excerpt"],)
    assert path.read_bytes() == original


def test_original_control_plan_nested_clause_matches_the_original_rule_source():
    path = Path(__file__).resolve().parents[3] / "assets/li/history/规则V2.0/10 控制计划.docx"
    assert_source_matches(path, 2, 1, 1)


def test_following_table_after_nested_and_merged_sources_has_the_same_identity(tmp_path):
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
    nested.cell(0, 1).add_table(rows=1, cols=1).cell(0, 0).text = "Deep clause"
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Following clause"
    path = tmp_path / "nested.docx"
    document.save(path)
    assert_source_matches(path, 4, 1, 1)


def test_following_table_after_deleted_sources_has_the_same_identity(tmp_path):
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
    assert_source_matches(path, 5, 1, 1)
