"""Rows that inherit a column written once, and reads of a related workbook under one resource."""

from audit.engine.rule_engine import RuleContext, Table
from audit.engine.rule_engine.contracts import ColumnModel, RuleResourceModel, SectionModel, SheetModel, index_rule_resource
from openpyxl import Workbook


def _resource(*, carry=()):
    return index_rule_resource(
        RuleResourceModel(
            sheets=(SheetModel(canonical_name="计划", include_keywords=("cp",)),),
            sections=(SectionModel(canonical_name="行", sheet_name="计划", header_anchors=("工序",), row_anchors=("方法",), row_stop_on_first_blank=False, row_carry_columns=carry),),
            columns=(ColumnModel(canonical_name="工序", section_name="行", aliases=("工序名称",)), ColumnModel(canonical_name="方法", section_name="行", aliases=("评价测量技术",))),
        )
    )


def _plan(title="CP"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in (["工序名称", "评价测量技术"], ["出货检验", "卡尺"], [None, "目视"], [None, "目测"], ["包装", "目视"]):
        sheet.append(row)
    return workbook


def _rows(ctx):
    section = Table(ctx).sheet("计划").section("行")
    return [(row.row_index, row.value("工序"), row.value("方法")) for row in section.rows("工序", "方法")]


def test_a_column_written_once_carries_to_the_rows_below_it():
    plan = _plan()
    ctx = RuleContext(None, plan, _resource(carry=("工序",)), file_version_id="cp")
    ctx.bind_workbook(plan, "cp")
    assert _rows(ctx) == [(2, "出货检验", "卡尺"), (3, "出货检验", "目视"), (4, "出货检验", "目测"), (5, "包装", "目视")]
    # The evidence is the cell that holds the value, not the blank one.
    assert ("cp", "CP", 3, 1) not in ctx.cell_reads() and ("cp", "CP", 2, 1) in ctx.cell_reads()


def test_without_carry_a_blank_cell_stays_blank():
    ctx = RuleContext(None, _plan(), _resource(), file_version_id="cp")
    assert [operation for _row, operation, _method in _rows(ctx)] == ["出货检验", None, None, "包装"]


def test_a_related_workbook_is_read_with_the_same_resource_and_its_own_version():
    target, related = Workbook(), _plan()
    ctx = RuleContext(None, target, _resource(carry=("工序",)), file_version_id="msa")
    with ctx.reading(related, "cp-file"):
        assert _rows(ctx)[0] == (2, "出货检验", "卡尺")
    assert ctx.workbook is target
    assert ("cp-file", "CP", 2, 2) in ctx.cell_reads()
