"""Native rule regression against the original corpus asset."""

from pathlib import Path

from audit.document_tools import snapshot
from audit.evidence import resolve
from audit.models import WordCell


def cell(data, table, row, column):
    return resolve(data, WordCell(kind="docx_cell", table=table, row=row, column=column))


def test_original_control_plan_nested_clause_retains_exact_source_text():
    path = Path(__file__).resolve().parents[3] / "assets/V2.0测试/规则V2.0/10 控制计划.docx"
    original = path.read_bytes()
    data = snapshot(path)
    observed = cell(data, 2, 1, 1)
    assert observed.known
    assert observed.values == (
        "识别表头区域是否有“控制计划类型”、“控制计划编号”、“联系人contact/电话phone”、“日期date（编制compi.）”、"
        "“日期date（修订update）”、“版本Rev.”、“页数”、“页码”、“零件号”、“零件名称”、“多功能小组”、“公司”、“公司代码”、"
        "“公司批准/日期”、“其它批准/日期（如需要）”、“顾客工程批准/日期(如需要）”、“顾客质量批准/日期(如需要)”、"
        "“其它批准/日期(如需要)”字段，如果没有则不通过，如果有则继续识别；当“控制计划编号”、“联系人contact/电话phone”、"
        "“日期date（编制compi.）”、“日期date（修订update）”、“版本Rev.”、“页数”、“页码”、“零件号”、“零件名称”、"
        "“多功能小组”、“公司”、“公司代码”、“公司批准/日期”、“其它批准/日期（如需要）”、“顾客工程批准/日期(如需要）”、"
        "“顾客质量批准/日期(如需要)”、“其它批准/日期(如需要)”字段对应的内容不为空，且“控制计划类型”的选项有且仅有一个被勾选则通过，否则不通过；",
    )
    assert data["tables"][1]["parent_cell"] == {"table": "table:1", "cell": "B7"}
    assert cell(data, 1, 7, 1).values == ("6",)
    assert cell(data, 1, 7, 2).values == ("",)
    assert path.read_bytes() == original
