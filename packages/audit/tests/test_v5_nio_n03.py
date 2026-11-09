"""N03 control-plan facts keep adjacent checkbox and column identities."""

import fitz
import pytest


@pytest.mark.parametrize("selection,expected", [("☑ 生产 □ 样件 □ 试生产", "PASS"), ("□ 生产 ☑ 样件 □ 试生产", "NG"), ("☑ SOP □ 生产", "PASS"), ("=A1", "UNABLE")])
def test_n03_stage_does_not_borrow_adjacent_checkbox(nio_execute, selection, expected):
    assert nio_execute("N03", "N03:1", {"控制计划": [[selection]]})["status"] == expected


@pytest.mark.parametrize("product,expected", [("孔径", "PASS"), (None, "NG"), ("=A1", "UNABLE")])
def test_n03_classification_needs_actual_product_or_process(nio_execute, product, expected):
    rows = [["特殊特性分类", "产品/工序规范/公差", "产品", "过程"], ["[CC]", "10±1", product, None]]
    assert nio_execute("N03", "N03:2", {"控制计划": rows})["status"] == expected


def test_n03_empty_table_cannot_be_nonapplicable_pass(nio_execute):
    assert nio_execute("N03", "N03:2", {"控制计划": [["特殊特性分类", "产品", "过程"]]})["status"] == "UNABLE"


def test_n03_moved_columns_and_late_missing_frequency_are_read(nio_execute):
    rows = [["说明"], [None, "工序名称/操作描述", "频次", "零件/工序号"], [None, "成型", "每件", 10], *[[None] for _ in range(45)], [None, "包装", None, 20]]
    assert nio_execute("N03", "N03:4", {"控制计划": rows})["status"] == "NG"


def test_n03_conflicting_column_layouts_are_unknown(nio_execute):
    rows = [["特殊特性分类", "产品", "过程"], ["CC", "孔径", None], ["产品", "过程", "特殊特性分类"], ["温度", None, "SC"]]
    assert nio_execute("N03", "N03:2", {"控制计划": rows})["status"] == "UNABLE"


@pytest.mark.parametrize("method,expected", [("量规", "PASS"), (None, "NG"), ("=A1", "UNABLE")])
def test_n03_critical_characteristic_requires_control(nio_execute, method, expected):
    rows = [["特殊特性分类", "控制方法"], ["[CC]", method]]
    assert nio_execute("N03", "N03:3", {"控制计划": rows})["status"] == expected


@pytest.mark.parametrize("frequency,expected", [("每件", "PASS"), (None, "NG"), ("=A1", "UNABLE")])
def test_n03_every_operation_needs_its_own_frequency(nio_execute, frequency, expected):
    rows = [["零件/工序号", "工序名称/操作描述", "频次"], [10, "成型", frequency]]
    assert nio_execute("N03", "N03:4", {"控制计划": rows})["status"] == expected


@pytest.mark.parametrize("stage,expected", [("☑ 生产 □ 样件", "PASS"), ("☑ 生产 ☑ 样件", "NG")])
def test_n03_all_header_values_and_exactly_one_stage(nio_execute, stage, expected):
    labels = [
        "控制计划编号",
        "联系人/电话",
        "日期（编制）",
        "日期（修订）",
        "版本",
        "页数",
        "页码",
        "零件号",
        "零件名称",
        "多功能小组",
        "公司",
        "公司代码",
        "公司批准/日期",
        "其它批准/日期（如需要）",
        "顾客工程批准/日期(如需要）",
        "顾客质量批准/日期(如需要)",
    ]
    rows = [[stage], *[[name, "实际内容"] for name in labels]]
    assert nio_execute("N03", "N03:5", {"控制计划": rows})["status"] == expected


def test_n03_pdf_tables_use_excel_preparation_and_original_pdf_location(nio_execute):
    import json

    with fitz.open() as document:
        page = document.new_page(width=460, height=200)
        for y in (20, 60, 100, 160):
            page.draw_line((20, y), (440, y))
        for x in (20, 160, 300, 440):
            page.draw_line((x, 20), (x, 160))
        for x, y, text in ((30, 45, "控制计划"), (30, 85, "特殊特性分类"), (180, 85, "产品"), (320, 85, "过程"), (30, 135, "[CC]"), (180, 135, "孔径")):
            page.insert_text((x, y), text, fontname="china-s", fontsize=11)
        content = document.tobytes()
    result = nio_execute("N03", "N03:2", content=content, suffix=".pdf")
    assert result["status"] == "PASS"
    assert result["evidence"]["source_format"] == "pdf"
    assert result["evidence"]["prepared_format"] == "xlsx"
    sources = [json.loads(source["locator"]) for source in result["evidence"]["sources"]]
    assert sources and all(source["kind"] == "pdf_cell" and source["page"] == 1 and source["bbox"] and source["prepared_sheet"] for source in sources)


def test_n03_unverified_related_originals_remain_explicitly_unknown(nio_execute):
    result = nio_execute("N03", "N03:6", {"控制计划": [["特殊特性分类", "产品", "过程"], ["CC", "孔径", None]]})
    assert result["status"] == "UNABLE"
    assert "过程流程图" in result["suggestions"]
