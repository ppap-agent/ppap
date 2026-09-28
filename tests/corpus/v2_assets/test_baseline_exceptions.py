from __future__ import annotations

from pathlib import Path

from tests.corpus.v2_assets.baseline_exceptions import (
    B,
    ObservedRuleOutcome,
    behavior_matches,
    classify_v2_baseline_outcomes,
)


def _outcome(expected: str, actual: str, failure_class: str | None = None) -> ObservedRuleOutcome:
    return ObservedRuleOutcome(expected_status=expected, actual_status=actual, failure_class=failure_class)


def test_exact_known_mismatch_is_allowed() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _outcome("PASS", "NG")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset()


def test_same_location_different_actual_status_is_regression() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _outcome("PASS", "ERR")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset({2})
    assert stale == frozenset()


def test_err_failure_class_change_is_regression_when_recorded() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "ERR", "conversion_failed")})
    observed = {2: _outcome("PASS", "ERR", "structure_insufficient")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert drifted == frozenset({2})
    assert new == frozenset()
    assert stale == frozenset()


def test_status_only_err_ignores_unstable_raised_class() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "ERR")})
    observed = {2: _outcome("PASS", "ERR", "raised:AttributeError")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset()


def test_new_mismatch_on_a_baseline_file_fails() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG")})
    observed = {2: _outcome("PASS", "NG"), 7: _outcome("PASS", "NG")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset({7})
    assert drifted == frozenset()
    assert stale == frozenset()


def test_healed_mismatch_is_stale() -> None:
    known = frozenset({B("a.pdf", "pdf", 2, "PASS", "NG"), B("a.pdf", "pdf", 5, "PASS", "NG")})
    observed = {2: _outcome("PASS", "NG"), 5: _outcome("PASS", "PASS")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset({5})


def test_split_does_not_apply_another_file_or_type() -> None:
    known = frozenset(
        {
            B("a.pdf", "pdf", 2, "PASS", "NG"),
            B("a.pdf", "scanned", 1, "PASS", "NG"),
            B("b.pdf", "pdf", 3, "PASS", "NG"),
        }
    )
    observed = {1: _outcome("PASS", "PASS")}
    new, drifted, stale = classify_v2_baseline_outcomes("a.pdf", "pdf", observed, known)
    assert new == frozenset()
    assert drifted == frozenset()
    assert stale == frozenset({2})


def test_behavior_matches_does_not_use_suggestion_text() -> None:
    known = B("a.pdf", "pdf", 2, "PASS", "NG")
    observed = _outcome("PASS", "NG")
    assert behavior_matches(known, observed)
    assert "suggestions" not in known.__dataclass_fields__


def test_corpus_conftest_does_not_blanket_xfail_files() -> None:
    source = (Path(__file__).resolve().parents[1] / "conftest.py").read_text()
    assert "BASELINE_XFAIL_IDS" not in source
    assert "pytest.mark.xfail" not in source
    exceptions = Path(__file__).with_name("baseline_exceptions.py").read_text()
    assert "BaselineRuleBehavior" in exceptions
    assert "actual_status" in exceptions
    assert "suggestions" not in exceptions
