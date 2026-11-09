"""N04 source title identifiers and complete characteristic records."""

import pytest

FIELDS = ["零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数"]
VALUES = ["P1", "支架", "产品", "孔径", "10±1", "CC", "关键", "量规", "专用工装", "每件", 1]


@pytest.mark.parametrize("title,expected", [("P1 特殊特性清单", "PASS"), ("特殊特性清单", "NG"), ("Version 3 特殊特性清单", "UNABLE"), ("=A2", "UNABLE")])
def test_n04_title_identifier_is_not_a_version_or_formula(nio_execute, title, expected):
    assert nio_execute("N04", "N04:1", {"特殊特性清单": [[title]]})["status"] == expected


def test_n04_conflicting_product_title_identifiers_are_unknown(nio_execute):
    rows = [["P1 特殊特性清单"], ["P2 特殊特性清单"]]
    assert nio_execute("N04", "N04:1", {"特殊特性清单": rows})["status"] == "UNABLE"


@pytest.mark.parametrize("changed,expected", [(None, "PASS"), ((1, None), "NG"), ((6, "=A1"), "UNABLE")])
def test_n04_every_active_row_needs_all_eleven_original_fields(nio_execute, changed, expected):
    values = VALUES.copy()
    if changed:
        index, value = changed
        values[index] = value
    assert nio_execute("N04", "N04:2", {"特殊特性清单": [FIELDS, values]})["status"] == expected


def test_n04_empty_table_does_not_pass(nio_execute):
    assert nio_execute("N04", "N04:2", {"特殊特性清单": [FIELDS]})["status"] == "UNABLE"


def test_n04_moved_columns_and_late_missing_value_are_checked(nio_execute):
    bad = VALUES.copy()
    bad[4] = None
    rows = [["说明"], [None, None, *reversed(FIELDS)], [None, None, *reversed(VALUES)], *[[None] for _ in range(45)], [None, None, *reversed(bad)]]
    assert nio_execute("N04", "N04:2", {"特殊特性清单": rows})["status"] == "NG"


def test_n04_footer_does_not_hide_a_later_invalid_business_row(nio_execute):
    bad = VALUES.copy()
    bad[4] = None
    rows = [FIELDS, VALUES, ["备注：以下仍有业务行"], bad]
    assert nio_execute("N04", "N04:2", {"特殊特性清单": rows})["status"] == "NG"
