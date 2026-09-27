from __future__ import annotations

from pathlib import Path

from tests.corpus.v2_assets.baseline_exceptions import split_v2_baseline_mismatches


def test_split_allows_only_listed_rule_mismatches() -> None:
    known = frozenset({("a.pdf", "pdf", 2), ("a.pdf", "pdf", 5), ("b.pdf", "scanned", 1)})
    unexpected, stale = split_v2_baseline_mismatches("a.pdf", "pdf", {2, 5}, known)
    assert unexpected == frozenset()
    assert stale == frozenset()


def test_split_fails_new_mismatch_on_a_baseline_file() -> None:
    known = frozenset({("a.pdf", "pdf", 2)})
    unexpected, stale = split_v2_baseline_mismatches("a.pdf", "pdf", {2, 7}, known)
    assert unexpected == frozenset({7})
    assert stale == frozenset()


def test_split_fails_stale_baseline_exception() -> None:
    known = frozenset({("a.pdf", "pdf", 2), ("a.pdf", "pdf", 5)})
    unexpected, stale = split_v2_baseline_mismatches("a.pdf", "pdf", {2}, known)
    assert unexpected == frozenset()
    assert stale == frozenset({5})


def test_split_does_not_apply_another_file_or_type() -> None:
    known = frozenset({("a.pdf", "pdf", 2), ("a.pdf", "scanned", 1), ("b.pdf", "pdf", 3)})
    unexpected, stale = split_v2_baseline_mismatches("a.pdf", "pdf", set(), known)
    assert unexpected == frozenset()
    assert stale == frozenset({2})


def test_corpus_conftest_does_not_blanket_xfail_files() -> None:
    source = (Path(__file__).resolve().parents[1] / "conftest.py").read_text()
    assert "BASELINE_XFAIL_IDS" not in source
    assert "pytest.mark.xfail" not in source
