from __future__ import annotations

from corpus_contracts.baseline_exceptions import (
    BASELINE_RULE_BEHAVIORS,
    B,
    ObservedRuleOutcome,
    behavior_matches,
    classify_v2_baseline_outcomes,
)
from corpus_contracts.execution_outcome import (
    EXECUTION_FAILURE,
    EXECUTION_RESULT,
    MISSING_RULE,
    ExecutionOutcome,
    O,
    outcome_from_agent_rule,
)


def _obs(expected: str, actual: ExecutionOutcome) -> ObservedRuleOutcome:
    return ObservedRuleOutcome(expected_status=expected, actual=actual)


def test_exact_known_mismatch_is_allowed() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _obs("PASS", O("NG"))}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset()


def test_same_location_different_actual_status_is_regression() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _obs("PASS", O("ERR"))}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset({2})
    assert stale == frozenset()


def test_ng_result_to_err_execution_failure_is_drift() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _obs("PASS", ExecutionOutcome("ERR", EXECUTION_FAILURE))}
    _, drifted, _ = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert drifted == frozenset({2})


def test_err_execution_failure_to_missing_rule_is_drift() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "ERR")})
    observed = {2: _obs("PASS", ExecutionOutcome("ERR", MISSING_RULE))}
    _, drifted, _ = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert drifted == frozenset({2})


def test_err_diagnostic_change_is_drift() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "ERR", "psw.pdf.conversion_failed")})
    observed = {2: _obs("PASS", O("ERR", EXECUTION_FAILURE, "psw.visual.runtime_error"))}
    _, drifted, _ = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert drifted == frozenset({2})


def test_python_exception_text_is_not_diagnostic_authority() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "ERR")})
    first = outcome_from_agent_rule(rule_index=2, agent_rule_count=5, raised=AttributeError("x"))
    second = outcome_from_agent_rule(rule_index=2, agent_rule_count=5, raised=TypeError("y"))
    assert first.semantic == second.semantic
    assert first.diagnostic_code is None
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", {2: _obs("PASS", second)}, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset()


def test_new_mismatch_on_a_baseline_file_fails() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _obs("PASS", O("NG")), 7: _obs("PASS", O("NG"))}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset({7})
    assert drifted == frozenset()
    assert stale == frozenset()


def test_healed_mismatch_is_stale() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG"), B("a.pdf", "pdf", 5, "PASS", "NG")})
    observed = {2: _obs("PASS", O("NG")), 5: _obs("PASS", O("PASS"))}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset({5})


def test_split_does_not_apply_another_file_or_type() -> None:
    known = frozenset(
        {
            B("a.pdf", "pdf", 2, "PASS", "NG"),
            B("a.pdf", "excel", 1, "PASS", "NG"),
            B("b.pdf", "pdf", 3, "PASS", "NG"),
        }
    )
    observed = {1: _obs("PASS", O("PASS"))}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset({2})


def test_behavior_matches_does_not_use_suggestion_text() -> None:
    known = B("a.pdf", "pdf", 2, "PASS", "NG")
    observed = _obs("PASS", O("NG"))
    assert behavior_matches(known, observed)
    assert "suggestions" not in known.__dataclass_fields__
    assert known.actual.execution_class == EXECUTION_RESULT


def test_recorded_behaviors_are_unique_locations() -> None:
    assert BASELINE_RULE_BEHAVIORS
    seen: set[tuple[str, str, int]] = set()
    for item in BASELINE_RULE_BEHAVIORS:
        assert item.location not in seen
        seen.add(item.location)
        assert item.expected_status != item.actual.status
