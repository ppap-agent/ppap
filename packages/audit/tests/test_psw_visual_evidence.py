"""PSW disposition rules must use reported facts, not the model verdict."""

import unittest
from datetime import date

import pytest
from audit.engine.checkers.psw_visual_evidence import (
    PSW_RULE_FIELDS,
    decide_disposition_visual_rule,
    evaluate_psw_observation,
)

_TODAY = date(2026, 10, 2)


def test_only_visual_psw_rules_have_fact_requirements() -> None:
    assert set(PSW_RULE_FIELDS) == {3, 5, 7, 8, 9, 10}


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
        status, suggestions = decide_disposition_visual_rule(9, _observation(), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_rule_10_uses_the_same_selection_gate(self) -> None:
        status, suggestions = decide_disposition_visual_rule(10, _observation(), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_approved_only_skips_the_conditional_checks(self) -> None:
        payload = _observation(approved_checked=True)
        self.assertEqual(decide_disposition_visual_rule(9, payload, today=_TODAY)[0], "PASS")
        self.assertEqual(decide_disposition_visual_rule(10, payload, today=_TODAY)[0], "PASS")

    def test_rejected_only_skips_the_conditional_checks(self) -> None:
        payload = _observation(rejected_checked=True)
        self.assertEqual(decide_disposition_visual_rule(9, payload, today=_TODAY)[0], "PASS")
        self.assertEqual(decide_disposition_visual_rule(10, payload, today=_TODAY)[0], "PASS")

    def test_two_checked_options_are_ng(self) -> None:
        payload = _observation(approved_checked=True, conditional_checked=True, approval_date="2026-10-01")
        status, suggestions = decide_disposition_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("需且仅需勾选一个选项", suggestions)

    def test_missing_disposition_field_is_ng(self) -> None:
        status, suggestions = decide_disposition_visual_rule(9, _observation(disposition_visible=False), today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("未找到", suggestions)

    def test_conditional_date_inside_92_days_passes(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="2026年7月2日")
        self.assertEqual(decide_disposition_visual_rule(9, payload, today=_TODAY)[0], "PASS")

    def test_conditional_date_outside_92_days_is_ng(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="2026-07-01")
        status, suggestions = decide_disposition_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("超出3个月", suggestions)

    def test_conditional_without_a_date_is_ng(self) -> None:
        payload = _observation(conditional_checked=True, approval_date="")
        status, suggestions = decide_disposition_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("无法识别条件批准日期", suggestions)

    def test_conditional_comments_follow_the_same_gate(self) -> None:
        present = _observation(conditional_checked=True, comments_present=True)
        absent = _observation(conditional_checked=True, comments_present=False)
        self.assertEqual(decide_disposition_visual_rule(10, present, today=_TODAY)[0], "PASS")
        status, suggestions = decide_disposition_visual_rule(10, absent, today=_TODAY)
        self.assertEqual(status, "NG")
        self.assertIn("补充评论", suggestions)

    def test_unreadable_checkbox_is_unable_not_a_business_ng(self) -> None:
        payload = _observation()
        del payload["rejected_checked"]
        status, suggestions = decide_disposition_visual_rule(9, payload, today=_TODAY)
        self.assertEqual(status, "UNABLE")
        self.assertIn("无法判断", suggestions)

    def test_missing_conditional_date_observation_is_not_a_missing_document_date(self) -> None:
        payload = _observation(conditional_checked=True)
        del payload["approval_date"]
        self.assertEqual(decide_disposition_visual_rule(9, payload, today=_TODAY)[0], "UNABLE")
        self.assertEqual(decide_disposition_visual_rule(9, _observation(conditional_checked=True, approval_date=None), today=_TODAY)[0], "UNABLE")
        self.assertEqual(decide_disposition_visual_rule(9, _observation(conditional_checked=True), today=_TODAY)[0], "NG")

    def test_missing_conditional_comments_observation_is_not_an_empty_comment(self) -> None:
        payload = _observation(conditional_checked=True)
        del payload["comments_present"]
        self.assertEqual(decide_disposition_visual_rule(10, payload, today=_TODAY)[0], "UNABLE")
        self.assertEqual(decide_disposition_visual_rule(10, _observation(conditional_checked=True), today=_TODAY)[0], "NG")

    def test_known_nonconformity_is_decisive_without_unrelated_unknown_facts(self) -> None:
        for payload in (
            _observation(disposition_visible=False, approved_checked=None, conditional_checked=None, rejected_checked=None),
            _observation(approved_checked=True, conditional_checked=True, rejected_checked=None),
            _observation(approved_checked=True, dimensional_checked=False),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(decide_disposition_visual_rule(8, payload, today=_TODAY)[0], "NG")

    def test_unreadable_disposition_is_not_a_known_missing_field(self) -> None:
        self.assertEqual(decide_disposition_visual_rule(8, _observation(disposition_visible=None), today=_TODAY)[0], "UNABLE")
        self.assertEqual(decide_disposition_visual_rule(8, _observation(disposition_visible=False), today=_TODAY)[0], "NG")


class NativePaddleDecisionTests(unittest.TestCase):
    def test_rule_eight_rejects_verdict_without_facts(self):
        self.assertEqual(evaluate_psw_observation(8, {"status": "PASS"}).status, "UNABLE")

    def test_rule_eight_empty_disposition_remains_ng(self):
        self.assertEqual(evaluate_psw_observation(8, {"status": "PASS", **_observation()}).status, "NG")

    def test_rule_eight_requires_each_submission_checkbox(self):
        fields = ("dimensional_checked", "appearance_checked", "material_functional_checked", "statistical_checked", "requirements_yes_checked")
        complete = {**_observation(approved_checked=True), **dict.fromkeys(fields, True)}
        self.assertEqual(evaluate_psw_observation(8, {"status": "NG", **complete}).status, "PASS")
        for field in fields:
            with self.subTest(field=field):
                self.assertEqual(evaluate_psw_observation(8, {**complete, field: False}).status, "NG")
                self.assertEqual(evaluate_psw_observation(8, {**complete, field: None}).status, "UNABLE")

    def test_rule_seven_ignores_a_model_verdict(self):
        self.assertEqual(evaluate_psw_observation(7, {"status": "PASS"}).status, "UNABLE")
        self.assertEqual(evaluate_psw_observation(7, {"submission_other_present": True}).status, "PASS")
        self.assertEqual(evaluate_psw_observation(7, {"submission_other_present": False, "submission_any_checked": False}).status, "NG")


def test_department_seal_complete_text_cannot_pass_from_its_outer_company_name():
    result = evaluate_psw_observation(3, {"psw_stamp_present": True, "psw_stamp_ending_text": "专用章", "psw_stamp_ends_with_company": True, "authorization_table_present": False})
    assert result.status == "NG"


@pytest.mark.parametrize(("present", "text"), [(False, "公司"), (True, "")])
def test_conflicting_stamp_presence_and_complete_text_cannot_decide_a_business_result(present, text):
    observation = {"psw_stamp_present": present, "psw_stamp_ending_text": text, "authorized_signature_present": True, "authorization_table_present": False}
    assert evaluate_psw_observation(3, observation).status == "UNABLE"
    assert evaluate_psw_observation(5, observation).status == "UNABLE"


def test_unknown_complete_stamp_text_cannot_be_replaced_by_the_outer_company_name():
    assert evaluate_psw_observation(3, {"psw_stamp_present": True, "psw_stamp_ending_text": None, "psw_stamp_ends_with_company": True, "authorization_table_present": False}).status == "UNABLE"


def test_confirmed_missing_stamp_does_not_need_a_transcription_of_nonexistent_text():
    assert evaluate_psw_observation(3, {"psw_stamp_present": False, "psw_stamp_ending_text": None, "authorization_table_present": False}).status == "NG"


@pytest.mark.parametrize(
    ("facts", "status", "count"), [({"submission_any_checked": True}, "PASS", 1), ({"submission_any_checked": False, "submission_other_present": False}, "NG", 1), ({}, "UNABLE", 0)]
)
def test_visual_predicate_count_does_not_turn_unknown_facts_into_checks(facts, status, count):
    result = evaluate_psw_observation(7, facts)
    assert (result.status, result.checks_performed) == (status, count)


@pytest.mark.parametrize("method", ["rule_9", "_precheck_disposition"])
def test_unknown_native_disposition_is_not_an_untriggered_condition(monkeypatch, method):
    from types import SimpleNamespace

    from audit.checkers.v4.li_auto import psw

    checker = object.__new__(psw.LiAutoPswAgent)
    checker.temp_files = []
    checker._checked_controls = lambda sheet: []
    checker._resolve_warrant_disposition = lambda *args: ("unknown", [], 1)
    monkeypatch.setattr(psw, "Table", lambda ctx: SimpleNamespace(sheet=lambda name: object()))
    context = SimpleNamespace(record_execution=lambda: None)
    assert getattr(checker, method)(context)[0] == "UNABLE"
