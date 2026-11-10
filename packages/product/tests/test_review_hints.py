"""Positive contracts for advisory, deterministic result comparisons."""

from copy import deepcopy
from dataclasses import replace

import pytest
from product.review_hints import MAX_REVIEW_HINTS, HintResult, review_hints
from product.store.task_view import file_round_audit


def result(round_id="current", file_id="file", status="NG", clause="R1"):
    return HintResult(round_id, file_id, f"{file_id}:rule:{clause}", clause, "immutable-suite", "a" * 64, status, "control.xlsx", 1)


def test_same_frozen_content_comparison_explains_the_two_results():
    current, previous = result(), result("previous", status="PASS")
    (hint,) = review_hints([current], [previous], set(), {})
    assert hint == {
        "item_key": current.item_key,
        "reason": "same_file_changed",
        "compared_round_id": "previous",
        "compared_file_version_id": "file",
        "compared_item_key": previous.item_key,
        "clause_id": "R1",
        "suite_revision_id": "immutable-suite",
        "current_status": "NG",
        "compared_status": "PASS",
        "compared_filename": "control.xlsx",
        "result_attempt": 1,
        "compared_result_attempt": 1,
    }
    assert review_hints([current], [previous], set(), {current.item_key: hint}) == []
    (changed_attempt,) = review_hints([replace(current, result_attempt=2)], [previous], set(), {current.item_key: hint})
    assert changed_attempt["result_attempt"] == 2


def test_explicit_pdf_excel_pair_is_compared_with_a_small_round_limit():
    current = [result(file_id="pdf", clause=f"R{i}") for i in range(5)]
    paired = [replace(item, file_version_id="excel", item_key=f"excel:rule:{item.clause_id}", sha256="b" * 64, status="PASS") for item in current]
    hints = review_hints([*current, *paired], [], {frozenset({"pdf", "excel"})}, {})
    assert len(hints) == MAX_REVIEW_HINTS
    assert all(hint["reason"] == "cross_format_differs" and hint["compared_file_version_id"] == "excel" for hint in hints)


@pytest.mark.parametrize("difference", [{"suite_revision_id": "different-revision"}, {"sha256": "b" * 64}, {"clause_id": "other-clause"}, {"status": "ERR"}, {"status": "NG"}])
def test_changed_identity_or_nonbusiness_result_is_not_an_instability_signal(difference):
    previous = replace(result("previous", status="PASS"), **difference)
    assert review_hints([result()], [previous], set(), {}) == []


def test_matching_names_without_an_explicit_format_pair_do_not_produce_a_hint():
    pdf = replace(result(file_id="pdf"), filename="control.pdf")
    excel = replace(result(file_id="excel", status="PASS"), sha256="b" * 64, filename="control.xlsx")
    assert review_hints([pdf, excel], [], set(), {}) == []


def test_hint_projection_preserves_machine_and_report_semantics():
    rule = {"id": "rule", "title": "Requirement", "body": {"kind": "engine", "rules": [{"id": "R1", "category": "Check", "rule_to_check": "Check source"}]}}
    frozen = {"role": "target", "file_version_id": "file", "rule": rule}
    round = {
        "id": "current",
        "execution": "completed",
        "result_attempt": 1,
        "coverage": {},
        "inputs": [frozen],
        "findings": [{"item_key": "file:rule:R1", "status": "NG", "review": None, "locator": {"file_version_id": "file", "rule_item_id": "R1"}}],
    }
    original = deepcopy(round)
    before = file_round_audit(round, frozen)
    (hint,) = review_hints([result()], [result("previous", status="PASS")], set(), {})
    round["findings"][0]["review_hint"] = hint
    after = file_round_audit(round, frozen)
    assert after["checks"][0]["finding"]["review_hint"] == hint
    for field in ("conclusion", "counts", "report", "execution"):
        assert after[field] == before[field]
    assert after["checks"][0]["review"] is None
    assert original["findings"][0]["status"] == after["checks"][0]["finding"]["status"]
