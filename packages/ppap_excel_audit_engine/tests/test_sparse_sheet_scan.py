from io import BytesIO
from zipfile import ZipFile

from openpyxl import Workbook
from ppap_core.types import PpapDocumentType
from ppap_excel_audit_engine.rule_engine import RuleContext, Sheet, get_doc_resource_by_doc_type


def sheet_xml(workbook):
    output = BytesIO()
    workbook.save(output)
    with ZipFile(output) as archive:
        return archive.read("xl/worksheets/sheet1.xml")


def test_scanning_sparse_content_does_not_materialize_blank_rows():
    workbook = Workbook()
    ws = workbook.active
    ws.title = "过程流程图"
    ws["A1"] = "工序名称"
    ws["A200"] = "IQC"
    ws["T200"].number_format = "0"
    before = sheet_xml(workbook)
    ctx = RuleContext(None, workbook, get_doc_resource_by_doc_type(PpapDocumentType.PROCESS_FLOW_CHART))

    cells = Sheet(ctx, ws).scan_non_empty(row_min=1, row_max=200, col_min=1, col_max=20)

    assert [(cell.row_index, cell.col_index, cell.value()) for cell in cells] == [(1, 1, "工序名称"), (200, 1, "IQC")]
    assert sheet_xml(workbook) == before
    workbook.close()


def test_bounded_sparse_scan_keeps_overlapping_merged_anchors_in_scan_order():
    workbook = Workbook()
    ws = workbook.active
    ws["A1"] = "merged"
    ws.merge_cells("A1:C3")
    ws["D2"] = "second"
    ws["C4"] = "third"
    ws["A5"] = "outside"
    before = sheet_xml(workbook)
    ctx = RuleContext(None, workbook, get_doc_resource_by_doc_type(PpapDocumentType.PROCESS_FLOW_CHART))

    cells = Sheet(ctx, ws).scan_non_empty(row_min=2, row_max=4, col_min=2, col_max=4)

    assert [(cell.row_index, cell.col_index, cell.value()) for cell in cells] == [(1, 1, "merged"), (2, 4, "second"), (4, 3, "third")]
    assert sheet_xml(workbook) == before
    workbook.close()
