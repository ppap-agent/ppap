import unittest
from copy import deepcopy
from types import SimpleNamespace

import pytest
from audit.audits import RoundInput, findings_from_rule_execution
from audit.rule_coverage import COVERAGE_UNSUPPORTED, MODE_RULE_ITEMS
from product import builtin_rule_input


@pytest.mark.parametrize(("profile", "expected"), [(None, "v2"), ("v2", "v2"), ("v3", "v3")])
def test_frozen_rule_identity_profile_and_scope_reach_the_checker_without_changing_the_snapshot(profile, expected):
    body = {
        "kind": "engine",
        "type": "PFMEA",
        "rules": [{"id": "customer-rule", "category": "客户要求", "rule_to_check": "检查原件"}],
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    if profile is not None:
        body["rule_profile"] = profile
    snapshot = deepcopy(body)
    item = RoundInput("target", "file", "file-v2", "PFMEA.xlsx", "ready", rule_version_id="confirmed-rules-v4", rule_body=body)

    checker_input = builtin_rule_input(item)

    assert checker_input.model_dump(exclude={"rules"}) == {
        "rule_id": "confirmed-rules-v4",
        "rule_profile": expected,
        "preset_key": None,
        "appendix": [],
        "type": "PFMEA",
        "customers": ["客户甲", "客户乙"],
        "models": ["车型甲"],
        "parts": ["P-001", "P-002"],
    }
    assert checker_input.rules
    assert item.rule_body == snapshot


@pytest.mark.parametrize(("rule_version", "body"), [(None, {"kind": "engine", "type": "PFMEA"}), ("confirmed-rules-v4", None)])
def test_a_checker_input_requires_both_a_frozen_rule_version_and_its_snapshot(rule_version, body):
    item = RoundInput("target", "file", "file-v2", "PFMEA.xlsx", "ready", rule_version_id=rule_version, rule_body=body)

    with pytest.raises(ValueError, match="^rule_required$"):
        builtin_rule_input(item)


class BuiltinBindingTests(unittest.TestCase):
    def test_uploaded_text_does_not_change_checker_input(self):
        item = SimpleNamespace(
            rule_version_id="rule1",
            filename="PFMEA.xlsx",
            rule_body={"kind": "engine", "type": "PFMEA", "rule_profile": "v3", "rules": [{"id": "99", "category": "customer-only", "rule_to_check": "arbitrary customer requirement"}]},
            rule_execution=None,
        )
        first = builtin_rule_input(item)
        self.assertTrue(first.rules)
        self.assertNotIn("customer-only", str(first.rules))
        item.rule_body["rules"] = []
        self.assertEqual(first.rules, builtin_rule_input(item).rules)
        item.filename = "PFMEA.pdf"
        self.assertEqual(first.rules, builtin_rule_input(item).rules)

    def test_unsupported_document_type_does_not_execute_customer_rules(self):
        from audit.engine.checkers.rule_declarations import freeze_target_rule_execution

        body = {"kind": "engine", "type": "unknown", "rules": [{"id": "1", "category": "custom", "priority": None, "rule_to_check": "custom", "type_of_rule": "text"}]}
        frozen = freeze_target_rule_execution(body, "unknown.bin")
        self.assertEqual(frozen["mode"], MODE_RULE_ITEMS)
        self.assertEqual(frozen["items"][0]["coverage"], COVERAGE_UNSUPPORTED)
        projected = findings_from_rule_execution([], target=RoundInput("target", "f1", "v1", "unknown.bin", "ready", rule_body=body, rule_execution=frozen), execution=frozen)
        self.assertEqual(projected["findings"], [])
        self.assertTrue(any("custom" in row for row in projected["coverage"]["unchecked"]))
