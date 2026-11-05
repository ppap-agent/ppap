"""Source clauses bind by their ID, never their list position."""

import pytest
from audit.rule_coverage import freeze_rule_execution

SOURCE = {"id": "U12:2", "category": "必填列", "rule_to_check": "编号、特性名称和特性类别列必须存在。", "priority": "P0"}
BINDINGS = {SOURCE["id"]: "required_columns"}


def test_explicit_binding_survives_clause_and_checker_reordering():
    unknown = {"id": "other", "category": "人工判断", "rule_to_check": "工艺是否合理。"}
    body = {"kind": "engine", "rules": [unknown, SOURCE]}
    frozen = freeze_rule_execution(body, [("business_values", "取值"), ("required_columns", "结构")], source_bindings=BINDINGS)
    assert frozen["items"][0]["coverage"] == "unsupported"
    assert frozen["items"][1]["checker_method"] == "required_columns"
    assert frozen["items"][1]["checker_index"] == 1
    reordered = freeze_rule_execution({**body, "rules": [SOURCE, unknown]}, [("required_columns", "结构"), ("business_values", "取值")], source_bindings=BINDINGS)
    assert reordered["items"][0]["checker_method"] == "required_columns"
    assert reordered["items"][0]["checker_index"] == 0
    assert reordered["items"][1]["coverage"] == "unsupported"


def test_clause_text_is_display_only_and_an_unbound_id_is_not_checked():
    changed = {**SOURCE, "rule_to_check": "所有特性值必须满足公差。"}
    frozen = freeze_rule_execution({"kind": "engine", "rules": [changed]}, [("required_columns", "结构")], source_bindings=BINDINGS)
    assert frozen["items"][0]["checker_method"] == "required_columns"
    assert frozen["items"][0]["rule_to_check"] == changed["rule_to_check"]
    no_binding = freeze_rule_execution({"kind": "engine", "rules": [SOURCE]}, [("required_columns", "结构")], source_bindings={})
    assert no_binding["items"][0]["coverage"] == "unsupported"


def test_removed_method_or_duplicate_source_ids_fail_closed():
    with pytest.raises(ValueError, match="invalid_source_binding"):
        freeze_rule_execution({"kind": "engine", "rules": [SOURCE]}, [("different_method", "结构")], source_bindings=BINDINGS)
    with pytest.raises(ValueError, match="duplicate_rule_id"):
        freeze_rule_execution({"kind": "engine", "rules": [SOURCE, SOURCE]}, [("required_columns", "结构")], source_bindings=BINDINGS)
