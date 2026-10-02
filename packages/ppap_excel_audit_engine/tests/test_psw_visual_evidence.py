"""Conditional PSW visual rules must use reported facts, not the model verdict."""

import json
import unittest
from datetime import date

from ppap_excel_audit_engine.checkers.psw_visual_evidence import (
    PswVisualRuleEvaluator,
    decide_conditional_visual_rule,
)

_TODAY = date(2026, 10, 2)


def _observation(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "disposition_visible": True,
        "approved_checked": False,
        "conditional_checked": False,
        "rejected_checked": False,
        "approval_date": "",
        "comments_present": False,
    }
    payload.update(overrides)
    return payload


class ConditionalVisualDecisionTests(unittest.TestCase):
    def test_no_checked_option_is_ng_before_the_date_branch(self) -> None:
        status, suggestions = decide_conditional_visual_rule(9, _observation(), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_rule_10_uses_the_same_selection_gate(self) -> None:
        status, suggestions = decide_conditional_visual_rule(10, _observation(), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_approved_only_skips_the_conditional_checks(self) -> None:
        payload = _observation(approved_checked=True)
        self.assertEqual(decide_conditional_visual_rule(9, payload, today=_TODAY)[0], "PASS")
        self.assertEqual(decide_conditional_visual_rule(10, payload, today=_TODAY)[0], "PASS")

    def test_rejected_only_skips_the_conditional_checks(self) -> None:
        payload = _observation(rejected_checked=True)
        self.assertEqual(decide_conditional_visual_rule(9, payload, today=_TODAY)[0], "PASS")
        self.assertEqual(decide_conditional_visual_rule(10, payload, today=_TODAY)[0], "PASS")

    def test_two_checked_options_are_ng(self) -> None:
        payload = _observation(approved_checked=True, conditional_checked=True, approval_date="2026-10-01")
        status, suggestions = decide_conditional_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_missing_disposition_field_is_ng(self) -> None:
        status, suggestions = decide_conditional_visual_rule(9, _observation(disposition_visible=False), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("未找到", suggestions)

    def test_conditional_date_inside_92_days_passes(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="2026年7月2日")
        self.assertEqual(decide_conditional_visual_rule(9, payload, today=_TODAY)[0], "PASS")

    def test_conditional_date_outside_92_days_is_ng(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="2026-07-01")
        status, suggestions = decide_conditional_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("超出3个月", suggestions)

    def test_conditional_without_a_date_is_ng(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="")
        status, suggestions = decide_conditional_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("无法识别条件批准日期", suggestions)

    def test_conditional_comments_follow_the_same_gate(self) -> None:
        present = _observation(conditional_checked=True, comments_present=True)
        absent = _observation(conditional_checked=True, comments_present=False)
        self.assertEqual(decide_conditional_visual_rule(10, present, today=_TODAY)[0], "PASS")
        status, suggestions = decide_conditional_visual_rule(10, absent, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("补充评论", suggestions)

    def test_unreadable_checkbox_fails_closed(self) -> None:
        payload = _observation()
        del payload["rejected_checked"]
        status, suggestions = decide_conditional_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("无法验证", suggestions)


class ConditionalVisualEvaluatorTests(unittest.TestCase):
    def test_model_pass_without_facts_does_not_pass(self) -> None:
        def analyze(**_kwargs: object) -> str:
            return json.dumps({"status": "PASS", "suggestions": "未勾选条件批准，所以通过"})

        result = PswVisualRuleEvaluator(analyze).evaluate(9, ["image"], today=_TODAY)
        self.assertEqual(result.status, "NG")
        self.assertIn("无法验证", result.suggestions)

    def test_model_pass_with_empty_checks_is_overridden(self) -> None:
        seen: dict[str, str] = {}

        def analyze(**kwargs: object) -> str:
            seen["prompt"] = str(kwargs["user_prompt"])
            return json.dumps(
                {
                    "status": "PASS",
                    "suggestions": "未勾选条件批准，所以通过",
                    **_observation(),
                }
            )

        result = PswVisualRuleEvaluator(analyze).evaluate(9, ["image"], today=_TODAY)
        self.assertEqual(result.status, "NG")
        self.assertIn("需且仅需勾选一个选项", result.suggestions)
        self.assertIn("不要判断通过或不通过", seen["prompt"])
        self.assertNotIn('"status"', seen["prompt"])

    def test_other_visual_rules_still_accept_the_model_status(self) -> None:
        def analyze(**_kwargs: object) -> str:
            return json.dumps(
                {
                    "classification": "提交原因检查",
                    "item": "提交原因检查",
                    "priority": "P0",
                    "status": "Pass",
                    "suggestions": "",
                }
            )

        result = PswVisualRuleEvaluator(analyze).evaluate(7, ["image"], today=_TODAY)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.suggestions, "规则7检查通过")


if __name__ == "__main__":
    unittest.main()
