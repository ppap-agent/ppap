"""N02 header and revision-history native source boundaries."""

import pytest


def pfmea(*, begin="2026-01-01", revised="2026-02-01", address="上海"):
    return [
        ["公司名称", "供应商"],
        ["车型项目", "项目"],
        ["零件名称", "支架"],
        ["零件号", "P1"],
        ["制造地址", address],
        ["PFMEA开始日期", begin],
        ["PFMEA修订日期", revised],
        ["跨职能团队", "质量/制造"],
    ]


@pytest.mark.parametrize("address,expected", [("上海", "PASS"), (None, "UNABLE"), ("=A1", "UNABLE")])
def test_n02_actual_header_values_and_unknowns(nio_execute, address, expected):
    assert nio_execute("N02", "N02:1", {"PFMEA": pfmea(address=address)})["status"] == expected


@pytest.mark.parametrize("begin,expected", [("2026-01-01", "PASS"), ("2026-01-02", "NG"), ("=A1", "UNABLE")])
def test_n02_revision_dates_compare_first_and_last_physical_history_rows(nio_execute, begin, expected):
    sheets = {"PFMEA": pfmea(begin=begin), "变更履历": [["修订日期"], ["2026-01-01"], [None], ["2026-02-01"]]}
    assert nio_execute("N02", "N02:2", sheets)["status"] == expected


def test_n02_empty_history_cannot_pass(nio_execute):
    assert nio_execute("N02", "N02:2", {"PFMEA": pfmea(), "变更履历": [["修订日期"]]})["status"] == "UNABLE"


def test_n02_changed_layout_keeps_label_value_identity(nio_execute):
    rows = [[None, None, label, value] for label, value in pfmea()]
    assert nio_execute("N02", "N02:1", {"PFMEA": rows})["status"] == "PASS"


def test_n02_conflicting_header_values_are_unknown(nio_execute):
    rows = [*pfmea(), ["零件号", "P2"]]
    assert nio_execute("N02", "N02:1", {"PFMEA": rows})["status"] == "UNABLE"


def test_n02_semantic_score_rule_is_not_fabricated_from_labels(nio_execute):
    result = nio_execute("N02", "N02:4", {"PFMEA": pfmea()})
    assert result["status"] == "UNABLE"
    assert "严重度" in result["suggestions"]
