"""Historical sources retain clauses and do not invent bindings or validation."""

import importlib
import json

import pytest
from audit.engine.checkers.rule_declarations import discover_declared_rules
from audit.suite_migration import generic_standard_suites, migrated_builtin_suite, user_rule_files


def test_historical_carriers_share_one_suite_and_explicit_bindings():
    assert len(generic_standard_suites()) == 17
    assert len(generic_standard_suites(include_history=True)) == 34
    assert {s.suite_key for s in generic_standard_suites(include_history=True)} == {s.suite_key for s in generic_standard_suites()}
    for suite in generic_standard_suites(include_history=True):
        rules = json.loads(suite.files["rules.json"])
        assert "rule_profile" not in rules
        source = {}
        exec(suite.program_files["checker.py"], source)
        resources = source["RESOURCES"]
        for implementation in resources["carriers"].values():
            module, name = implementation.split(":")
            cls = getattr(importlib.import_module(module), name)
            cls = getattr(cls, "TARGET_EXCEL_AGENT_CLASS", None) or cls
            declarations = discover_declared_rules(cls)
            assert resources["methods"] == {str(index): method for index, (method, _) in enumerate(declarations, 1)}
            assert [row["rule_to_check"] for row in rules["rules"]] == [desc for _, desc in declarations]
        assert set(suite.entrypoints) == {r["id"] for r in rules["rules"]}
        assert not any("verified" in str(row) for row in json.loads(suite.files["tests.json"]))


def test_user_rules_do_not_acquire_builtin_or_positional_bindings():
    body = {"kind": "engine", "type": "控制计划", "rule_profile": "v3", "rules": [{"id": "custom-4", "rule_to_check": "用户条款"}]}
    files = user_rule_files(body, appendix=[{"instruction": "用户附件"}])
    rules = json.loads(files["rules.json"])
    assert rules["rules"] == body["rules"]
    assert rules["checker_binding"] == {}
    assert "rule_profile" not in rules
    assert json.loads(files["appendix.json"]) == [{"instruction": "用户附件"}]
    assert files["tests.json"] == b"[]"
    assert body["rule_profile"] == "v3"


def test_user_artifact_requires_explicit_matching_ids():
    body = {"rules": [{"id": "u"}]}
    with pytest.raises(ValueError, match="without_artifact"):
        user_rule_files(body, entrypoints={"u": "checker:check"})
    with pytest.raises(ValueError, match="without_artifact"):
        user_rule_files(body, checker_files={"checker.py": b"pass\n"}, entrypoints={"other": "checker:check"})
    files = user_rule_files(body, checker_files={"checker.py": b"def check(ctx): return None\n"}, entrypoints={"u": "checker:check"})
    assert json.loads(files["rules.json"])["checker_binding"] == {"u": "checker:check"}


def test_unknown_builtin_does_not_fall_back():
    with pytest.raises(ValueError, match="unknown_builtin"):
        migrated_builtin_suite("v99", "控制计划")
    with pytest.raises(ValueError, match="unknown_builtin"):
        migrated_builtin_suite("v3", "新文档类型")
