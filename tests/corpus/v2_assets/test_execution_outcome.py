from __future__ import annotations

from execution_outcome import (
    EXECUTION_FAILURE,
    EXECUTION_RESULT,
    MISSING_RULE,
    ExecutionOutcome,
    O,
    outcome_from_agent_rule,
)


class _Result:
    def __init__(self, status: str, diagnostic: str | None = None) -> None:
        self.status = status
        if diagnostic:
            self.evidence = type(
                "E",
                (),
                {"diagnostics": (type("D", (), {"code": diagnostic, "severity": "error"})(),)},
            )()
        else:
            self.evidence = None


def test_pass_result_is_same() -> None:
    assert O("PASS") == ExecutionOutcome("PASS", EXECUTION_RESULT)


def test_known_ng_result_exact_match() -> None:
    assert O("NG").semantic == ("NG", EXECUTION_RESULT, None)


def test_outcome_from_raised_exception_has_no_diagnostic() -> None:
    outcome = outcome_from_agent_rule(rule_index=1, agent_rule_count=3, raised=RuntimeError("trace"))
    assert outcome == ExecutionOutcome("ERR", EXECUTION_FAILURE)


def test_missing_agent_rule_is_missing_rule_class() -> None:
    outcome = outcome_from_agent_rule(rule_index=8, agent_rule_count=3)
    assert outcome == ExecutionOutcome("ERR", MISSING_RULE)


def test_stable_diagnostic_is_kept_on_err() -> None:
    outcome = outcome_from_agent_rule(
        rule_index=1,
        agent_rule_count=3,
        result=_Result("ERR", "psw.pdf.conversion_failed"),
    )
    assert outcome == ExecutionOutcome("ERR", EXECUTION_FAILURE, "psw.pdf.conversion_failed")


def test_pass_does_not_keep_diagnostic() -> None:
    outcome = outcome_from_agent_rule(
        rule_index=1,
        agent_rule_count=3,
        result=_Result("PASS", "psw.pdf.conversion_failed"),
    )
    assert outcome == ExecutionOutcome("PASS", EXECUTION_RESULT)
