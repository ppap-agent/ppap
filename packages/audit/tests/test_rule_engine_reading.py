"""Reads of a related workbook under one resource."""

from audit.engine.rule_engine import RuleContext, Table
from audit.engine.rule_engine.contracts import ColumnModel, RuleResourceModel, SectionModel, SheetModel, index_rule_resource
from openpyxl import Workbook


def _resource():
    return index_rule_resource(
        RuleResourceModel(
            sheets=(SheetModel(canonical_name="计划", include_keywords=("cp",)),),
            sections=(SectionModel(canonical_name="行", sheet_name="计划", header_anchors=("工序",), row_anchors=("方法",), row_stop_on_first_blank=False),),
            columns=(ColumnModel(canonical_name="工序", section_name="行", aliases=("工序名称",)), ColumnModel(canonical_name="方法", section_name="行", aliases=("评价测量技术",))),
        )
    )


def _plan(title="CP"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    for row in (["工序名称", "评价测量技术"], ["出货检验", "卡尺"], [None, "目视"]):
        sheet.append(row)
    return workbook


def _rows(ctx):
    section = Table(ctx).sheet("计划").section("行")
    return [(row.row_index, row.value("工序"), row.value("方法")) for row in section.rows("工序", "方法")]


def test_a_related_workbook_is_read_with_the_same_resource_and_its_own_version():
    target, related = Workbook(), _plan()
    ctx = RuleContext(None, target, _resource(), file_version_id="msa")
    with ctx.reading(related, "cp-file"):
        assert _rows(ctx)[0] == (2, "出货检验", "卡尺")
    assert ctx.workbook is target
    assert ("cp-file", "CP", 2, 2) in ctx.cell_reads()
