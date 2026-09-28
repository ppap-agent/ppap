from __future__ import annotations

from pathlib import Path

import pytest

from tests.corpus.v2_assets.baseline_exceptions import ObservedRuleOutcome
from tests.corpus.v2_assets.cross_format import (
    EXECUTION_FAILURE,
    EXECUTION_RESULT,
    MISSING_RULE,
    D,
    FormatRuleOutcome,
    classify_pair_rule,
    classify_pair_rules,
    discover_v2_cross_format_pairs,
    pair_id_for_file_name,
    to_format_outcome,
)
from tests.corpus.v2_assets.cross_format_baseline import KNOWN_PAIR_DIVERGENCES
from tests.corpus.v2_assets.v2_assets_shared import FileCase, RuleExpectation


def _case(file_name: str, file_type: str, rules: list[int] | None = None) -> FileCase:
    expected = {
        index: RuleExpectation(status="PASS", rule_desc="r", manual_reason="")
        for index in (rules or [1])
    }
    return FileCase(
        file_name=file_name,
        file_path=Path(file_name),
        expected=expected,
        agent_cls=object,
        file_type=file_type,
    )


def test_pair_id_comes_from_oracle_file_stem() -> None:
    assert pair_id_for_file_name("8-过程流程图-巨一.xlsx") == "8-过程流程图-巨一"
    assert pair_id_for_file_name("8-过程流程图-巨一.xls") == "8-过程流程图-巨一"
    assert pair_id_for_file_name("8-过程流程图-巨一.pdf") == "8-过程流程图-巨一"
    assert pair_id_for_file_name("cafe\u0301.pdf") == pair_id_for_file_name("caf\u00e9.xlsx")


def test_discover_pairs_from_shared_stems() -> None:
    pairs = discover_v2_cross_format_pairs(
        [_case("doc.xls", "excel"), _case("other.xlsx", "excel")],
        [_case("doc.pdf", "pdf"), _case("lonely.pdf", "pdf")],
    )
    assert [pair.pair_id for pair in pairs] == ["doc"]
    assert pairs[0].excel.file_name == "doc.xls"
    assert pairs[0].pdf.file_name == "doc.pdf"


def test_ambiguous_pairing_fails_closed() -> None:
    with pytest.raises(AssertionError, match=r"V2 cross-format pairs ambiguous"):
        discover_v2_cross_format_pairs(
            [_case("doc.xlsx", "excel"), _case("doc.xlsm", "excel")],
            [_case("doc.pdf", "pdf")],
        )


def test_unpaired_files_are_not_pairs() -> None:
    with pytest.raises(AssertionError, match="No V2 PDF/Excel pairs discovered"):
        discover_v2_cross_format_pairs([_case("only.xlsx", "excel")], [_case("other.pdf", "pdf")])


def test_pass_pass_is_consistent() -> None:
    verdict = classify_pair_rule(
        "doc",
        1,
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=(),
    )
    assert verdict.kind == "consistent"


def test_exact_known_divergence_is_allowed() -> None:
    known = [D("doc", 1, "NG", "PASS")]
    verdict = classify_pair_rule(
        "doc",
        1,
        FormatRuleOutcome("NG", EXECUTION_RESULT),
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=known,
    )
    assert verdict.kind == "allowed"


def test_known_ng_becoming_err_is_changed() -> None:
    known = [D("doc", 1, "NG", "PASS")]
    verdict = classify_pair_rule(
        "doc",
        1,
        FormatRuleOutcome("ERR", EXECUTION_FAILURE),
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=known,
    )
    assert verdict.kind == "changed"


def test_new_pass_ng_divergence_fails() -> None:
    verdict = classify_pair_rule(
        "doc",
        1,
        FormatRuleOutcome("NG", EXECUTION_RESULT),
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=(),
    )
    assert verdict.kind == "new"


def test_healed_divergence_is_stale() -> None:
    known = [D("doc", 1, "NG", "PASS")]
    verdict = classify_pair_rule(
        "doc",
        1,
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=known,
    )
    assert verdict.kind == "stale"


def test_new_and_healed_are_not_offset_by_aggregate() -> None:
    known = [D("doc", 1, "NG", "PASS")]
    verdicts = classify_pair_rules(
        "doc",
        {
            1: FormatRuleOutcome("PASS", EXECUTION_RESULT),
            2: FormatRuleOutcome("NG", EXECUTION_RESULT),
        },
        {
            1: FormatRuleOutcome("PASS", EXECUTION_RESULT),
            2: FormatRuleOutcome("PASS", EXECUTION_RESULT),
        },
        known=known,
    )
    kinds = {item.rule_index: item.kind for item in verdicts}
    assert kinds[1] == "stale"
    assert kinds[2] == "new"


def test_execution_failure_is_not_ordinary_format_difference() -> None:
    observed = ObservedRuleOutcome(
        expected_status="PASS",
        actual_status="ERR",
        failure_class="raised:TypeError",
    )
    outcome = to_format_outcome(observed)
    assert outcome.execution_class == EXECUTION_FAILURE
    assert outcome.status == "ERR"
    assert outcome.failure_class is None
    verdict = classify_pair_rule(
        "doc",
        1,
        outcome,
        FormatRuleOutcome("PASS", EXECUTION_RESULT),
        known=(),
    )
    assert verdict.kind == "new"
    assert verdict.pdf.execution_class != EXECUTION_RESULT


def test_missing_rule_is_structured_not_pass() -> None:
    outcome = to_format_outcome(None)
    assert outcome.status == "MISSING"
    assert outcome.execution_class == MISSING_RULE


def test_comparison_does_not_use_suggestion_text() -> None:
    source = Path(__file__).with_name("cross_format.py").read_text()
    assert "suggestions" not in source
    assert "FormatRuleOutcome" in source


def test_known_divergences_are_unique_pair_rule_records() -> None:
    assert KNOWN_PAIR_DIVERGENCES
    seen: set[tuple[str, int]] = set()
    for item in KNOWN_PAIR_DIVERGENCES:
        assert item.location not in seen
        seen.add(item.location)
        assert (
            item.pdf_status,
            item.pdf_execution_class,
            item.pdf_failure_class,
        ) != (
            item.excel_status,
            item.excel_execution_class,
            item.excel_failure_class,
        )
