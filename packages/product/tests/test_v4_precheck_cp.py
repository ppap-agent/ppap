"""Independent original CP inputs separate preparation defects from engineering verdicts."""

from __future__ import annotations

import pytest
from audit.contracts import AuditInput
from audit.precheck_cp import cp_precheck
from openpyxl import Workbook


def original(tmp_path, *, uaes=False, capacity="10%", frequency="2h", specification="[9,11]", equipment="机床", measurement="卡尺", symbol="CC", empty_header=False, formula=False, reverse=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    headers = (
        ["供应商名称", "供应商代码", "编制人", "版本号", "受控日期", "零件名称", "零件号"]
        if uaes
        else [
            "控制计划类型",
            "控制计划编号",
            "联系人",
            "电话",
            "编制日期",
            "修订日期",
            "版本",
            "页数",
            "页码",
            "零件号",
            "零件名称",
            "核心小组",
            "公司名称",
            "公司代码",
            "公司批准",
            "顾客工程批准",
            "顾客质量批准",
            "其它批准",
        ]
    )
    for label in headers:
        value = "" if empty_header and label == "零件名称" else "已填事实"
        if label == "核心小组":
            value = "制造、质量"
        sheet.append([label, value])
    if not uaes:
        sheet.append(["批准日期", "2025-03-01"])
    columns = ["工序号", "工序名称", "生产设备", "特性分类", "产品特性", "过程特性", "规格/公差", "评价测量技术", "样本容量", "频次", "控制方法", "反应计划", "特性编号"]
    if uaes:
        columns = [name for name in columns if name not in {"产品特性", "过程特性", "频次", "控制方法"}]
    sheet.append(columns)
    for index in range(3):
        data = {
            "工序号": (3 - index if reverse else index + 1) * 10,
            "工序名称": "加工" + str(index),
            "生产设备": equipment,
            "特性分类": symbol,
            "产品特性": "尺寸",
            "过程特性": "参数",
            "规格/公差": specification,
            "评价测量技术": measurement,
            "样本容量": "=1/0" if formula else capacity,
            "频次": frequency,
            "控制方法": "SPC",
            "反应计划": "停机报告",
            "特性编号": 1,
        }
        sheet.append([data[name] for name in columns])
    path = tmp_path / "control-plan.xlsx"
    book.save(path)
    book.close()
    return AuditInput(audit_id="frozen-cp-version", file_url=str(path))


def checked(audit, entry="N03"):
    return {row["id"].split(":", 1)[1]: row for row in cp_precheck(audit, entry)}


def test_uaes_seven_headers_do_not_gain_eleven_other_customer_requirements(tmp_path):
    result = checked(original(tmp_path, uaes=True), "U08")
    assert result["M-01"]["status"] == "satisfied"
    assert result["M-04"]["status"] == "satisfied"
    assert result["M-02"]["status"] == "satisfied"
    assert result["C-11"]["code"] == "S02"
    assert result["C-09"]["code"] == "S02"
    assert result["P-05"]["code"] == "S01"


def test_structure_and_empty_header_are_separate_facts(tmp_path):
    result = checked(original(tmp_path, empty_header=True))
    assert result["M-01"]["status"] == "satisfied"
    assert result["M-02"]["status"] == "defect" and result["M-02"]["code"] == "W02"
    assert result["M-14"]["code"] == "S02"


@pytest.mark.parametrize("capacity", ["101%", "0%", "0件"])
def test_definite_capacity_domain_is_a_preparation_fact(tmp_path, capacity):
    result = checked(original(tmp_path, capacity=capacity))
    assert result["C-01"]["status"] == "defect" and result["C-01"]["code"] == "B05"
    assert result["C-01"]["certified"] is False
    assert result["C-01"]["evidence"]["sources"]


def test_clear_unit_swaps_and_interval_math_are_not_engineering_ng(tmp_path):
    result = checked(original(tmp_path, capacity="2h", frequency="10件", specification="[11,9]"))
    assert result["C-02"]["status"] == "defect" and result["C-02"]["code"] == "B05"
    assert result["C-03"]["status"] == "defect" and result["C-03"]["code"] == "B06"


def test_uncached_formula_is_unknown_and_never_means_zero_capacity(tmp_path):
    result = checked(original(tmp_path, formula=True))
    assert result["C-01"]["status"] == "unknown" and result["C-01"]["code"] is None
    assert result["M-04"]["status"] == "unknown"


def test_equipment_and_measurement_fill_rates_keep_distinct_severity(tmp_path):
    result = checked(original(tmp_path, equipment="", measurement=""))
    assert result["M-06"]["code"] == "B05" and result["M-06"]["critical"] is True
    assert result["M-07"]["code"] == "W02" and result["M-07"]["critical"] is False


def test_process_order_and_empty_classification_are_independent(tmp_path):
    result = checked(original(tmp_path, reverse=True, symbol=""))
    assert result["C-18"]["code"] == "W03"
    assert result["M-05"]["code"] == "B05"
