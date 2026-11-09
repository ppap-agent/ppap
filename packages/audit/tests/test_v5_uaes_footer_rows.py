"""A known footer row cannot hide later original business rows."""

from pathlib import Path
from runpy import run_path

import pytest

_execute = run_path(str(Path(__file__).with_name("test_v5_uaes_reports.py")))["_execute"]


def source(entry, state):
    if entry == "U01":
        measured = 5 if state == "valid" else 11 if state == "invalid" else "=1+1"
        normal = [["要求", "供应商实测值", "Sample2", "Sample3", "结果"], ["5±5", 5, 5, 5, "OK"]]
        sheets = {title: [*normal] for title in ("尺寸报告", "材料报告", "功能报告", "可靠性报告")}
        sheets["材料报告"].extend([["供应商负责人签字"], ["5±5", 5, measured, 5, "OK"]])
        return "U01:2", sheets
    if entry == "U05":
        name = "卡尺" if state == "valid" else None if state == "invalid" else "=1+1"
        return "U05:1-1", {"MSA计划表": [["序号", "量具名称", "量具编号", "备注"], [1, "卡尺", "G1"], ["编制/日期"], [2, name, "G2"]]}
    if entry == "U06":
        cpk = 1.5 if state == "valid" else 1.0 if state == "invalid" else "=1+1"
        return "U06:3", {"能力值汇总": [["序号", "CPK", "PPK", "CPK目标", "PPK目标", "反应计划"], [1, 1.5, 1.8, 1.33, 1.67], ["编制/日期"], [2, cpk, 1.8, 1.33, 1.67]]}
    value = "20±0.1" if state == "valid" else None if state == "invalid" else "=1+1"
    return "U12:2", {"特殊特性清单": [["编号", "产品特性", "特性及公差", "特性类别"], [1, "孔径", "10±0.1", "CC"], ["编制/日期"], [2, "高度", value, "SC"]]}


@pytest.mark.parametrize("entry", ["U01", "U05", "U06", "U12"])
@pytest.mark.parametrize("state,expected", [("valid", "PASS"), ("invalid", "NG"), ("unknown", "UNABLE")])
def test_footer_only_skips_itself_not_later_business_rows(tmp_path, monkeypatch, entry, state, expected):
    clause, sheets = source(entry, state)
    result = _execute(tmp_path, monkeypatch, entry, clause, sheets)
    assert result["status"] == expected, result
    if expected in {"PASS", "NG"}:
        assert result["evidence"]["sources"]
