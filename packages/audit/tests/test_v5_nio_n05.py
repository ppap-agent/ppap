"""N05 controlled source-clause boundaries."""

import json
from pathlib import Path
from runpy import run_path

import pytest

execute = run_path(str(Path(__file__).with_name("test_v5_nio_support.py")))["execute"]


@pytest.mark.parametrize("name,expected", [("IQC", "PASS"), ("来料检查", "NG"), ("=A1", "UNABLE")])
def test_n05_incoming_inspection_uses_actual_literal_and_unknown(tmp_path, monkeypatch, name, expected):
    result = execute(tmp_path, monkeypatch, "N05", "N05:6", {"过程流程图": [["工序名称"], [name]]})
    assert result["status"] == expected
    if expected == "PASS":
        assert result["evidence"]["sources"]
        assert all(json.loads(source["locator"])["file_version_id"] == "frozen-nio-source" for source in result["evidence"]["sources"])


@pytest.mark.parametrize("rows,expected", [([], "UNABLE"), ([["返工"], ["重检"]], "PASS"), ([["返工检验"], ["包装"]], "NG"), ([["返工"], [None], ["重检"]], "NG")])
def test_n05_rework_requires_next_physical_row(tmp_path, monkeypatch, rows, expected):
    assert execute(tmp_path, monkeypatch, "N05", "N05:8", {"过程流程图": [["工序名称"], *rows]})["status"] == expected


def test_n05_reads_late_rows_and_moved_headers(tmp_path, monkeypatch):
    rows = [["文件说明"], [None], [None, None, "工序名称", "操作描述"], [None, None, "IQC", "检验"], *[[None] for _ in range(45)], [None, None, "包装", None]]
    assert execute(tmp_path, monkeypatch, "N05", "N05:4", {"过程流程图": rows})["status"] == "NG"


def test_n05_footer_cannot_hide_late_business_rows(tmp_path, monkeypatch):
    rows = [["工序名称", "操作描述"], ["IQC", "检查"], ["编制/日期"], ["包装", None]]
    assert execute(tmp_path, monkeypatch, "N05", "N05:4", {"过程流程图": rows})["status"] == "NG"


def test_n05_conflicting_headers_are_unknown(tmp_path, monkeypatch):
    rows = [["工序名称", "操作描述"], ["成型", "成型"], [None], ["操作描述", "工序名称"], ["检查", "检查"]]
    assert execute(tmp_path, monkeypatch, "N05", "N05:4", {"过程流程图": rows})["status"] == "UNABLE"


def test_n05_preserves_source_third_clause_wording(tmp_path, monkeypatch):
    rows = [["产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别"], ["孔径", "CC", None, "SC"]]
    assert execute(tmp_path, monkeypatch, "N05", "N05:3", {"过程流程图": rows})["status"] == "PASS"


def test_n05_unimplemented_cross_file_clause_is_an_explicit_gap(tmp_path, monkeypatch):
    result = execute(tmp_path, monkeypatch, "N05", "N05:9", {"过程流程图": [["工序名称"], ["IQC"]]})
    assert result["status"] == "UNABLE"
    assert "特殊特性清单" in result["suggestions"]
