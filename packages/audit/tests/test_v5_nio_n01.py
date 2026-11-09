"""N01 completeness and exact numeric thresholds without model calls."""

from pathlib import Path
from runpy import run_path

import pytest

execute = run_path(str(Path(__file__).with_name("test_v5_nio_support.py")))["execute"]


def test_n01_cover_fields_use_the_selected_source_sheet(tmp_path, monkeypatch):
    rows = [["零件号", "P1"], ["零件名", "支架"], ["项目", "车型"], ["项目阶段", "SOP"], ["日期", "2026-01-01"]]
    assert execute(tmp_path, monkeypatch, "N01", "N01:1", {"CoverSheet": rows})["status"] == "PASS"


@pytest.mark.parametrize("kind,expected", [("CC", "PASS"), ("KPC", "NG"), ("=A1", "UNABLE")])
def test_n01_point_kind_literals_and_formula_unknown(tmp_path, monkeypatch, kind, expected):
    assert execute(tmp_path, monkeypatch, "N01", "N01:5", {"测量报告": [["测点号", "类型"], ["P1", kind]]})["status"] == expected


def test_n01_empty_measurement_table_cannot_pass(tmp_path, monkeypatch):
    assert execute(tmp_path, monkeypatch, "N01", "N01:5", {"测量报告": [["测点号", "类型"]]})["status"] == "UNABLE"


@pytest.mark.parametrize("coordinate,expected", [(0, "PASS"), (None, "NG"), ("=A1", "UNABLE")])
def test_n01_all_required_coordinate_values_are_checked(tmp_path, monkeypatch, coordinate, expected):
    rows = [["测点号", "坐标X", "坐标Y", "坐标Z", "上公差", "下公差", "主要控制方向"], ["P1", coordinate, 0, 0, 1, -1, "X"]]
    assert execute(tmp_path, monkeypatch, "N01", "N01:2", {"测量报告": rows})["status"] == expected


def test_n01_blank_kind_in_later_active_point_is_not_skipped(tmp_path, monkeypatch):
    rows = [["测点号", "类型"], ["P1", "CC"], ["P2", None]]
    assert execute(tmp_path, monkeypatch, "N01", "N01:5", {"测量报告": rows})["status"] == "NG"


@pytest.mark.parametrize("upper,expected", [("NA", "PASS"), (1, "NG"), ("=A1", "UNABLE")])
def test_n01_special_direction_needs_both_na_tolerances(tmp_path, monkeypatch, upper, expected):
    rows = [["主要控制方向", "上公差", "下公差"], ["Gap-", upper, "NA"]]
    assert execute(tmp_path, monkeypatch, "N01", "N01:3", {"测量报告": rows})["status"] == expected


@pytest.mark.parametrize("entry,column", [("N01:9", "Centering"), ("N01:11", "Range")])
@pytest.mark.parametrize("value,expected", [(0.66, "PASS"), (0.66001, "NG"), ("=A1", "UNABLE")])
def test_n01_threshold_has_no_business_sized_numeric_tolerance(tmp_path, monkeypatch, entry, column, value, expected):
    rows = [["测点号", column], ["P1", value]]
    assert execute(tmp_path, monkeypatch, "N01", entry, {"测量报告": rows})["status"] == expected


def test_n01_layout_shift_and_late_bad_row_are_not_ignored(tmp_path, monkeypatch):
    rows = [["说明"], [None, None, "类型", "测点号"], [None, None, "CC", "P1"], *[[None] for _ in range(45)], [None, None, "BAD", "P2"]]
    assert execute(tmp_path, monkeypatch, "N01", "N01:5", {"测量报告": rows})["status"] == "NG"


def test_n01_conflicting_measurement_tables_do_not_select_first(tmp_path, monkeypatch):
    sheets = {"Surface": [["测点号", "类型"], ["P1", "CC"]], "Trim": [["测点号", "类型"], ["P2", "BAD"]]}
    assert execute(tmp_path, monkeypatch, "N01", "N01:5", sheets)["status"] == "UNABLE"


def test_n01_multi_part_statistics_are_an_explicit_gap(tmp_path, monkeypatch):
    result = execute(tmp_path, monkeypatch, "N01", "N01:8", {"测量报告": [["测点号", "Centering"], ["P1", 0]]})
    assert result["status"] == "UNABLE"
    assert "全部样本列" in result["suggestions"]
