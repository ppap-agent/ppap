from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.corpus.v2_assets.cross_format import (
    classify_pair_rules,
    discover_v2_cross_format_pairs,
    to_format_outcome,
)
from tests.corpus.v2_assets.cross_format_baseline import KNOWN_PAIR_DIVERGENCES
from tests.corpus.v2_assets.test_v2_assets import collect_v2_rule_outcomes
from tests.corpus.v2_assets.v2_assets_shared import build_cases


def pytest_generate_tests(metafunc: Any) -> None:
    if "pair" not in metafunc.fixturenames:
        return
    if not metafunc.config.getoption("--cross-format"):
        metafunc.parametrize("pair", [None], ids=["not-selected"])
        return

    dataset_root = Path(str(metafunc.config.getoption("--v2-dataset-root"))).expanduser().resolve()
    expected_csv = Path(str(metafunc.config.getoption("--v2-expected-csv"))).expanduser().resolve()
    excel_cases = build_cases(dataset_root=dataset_root, expected_csv=expected_csv, ftype_filter="excel")
    pdf_cases = build_cases(dataset_root=dataset_root, expected_csv=expected_csv, ftype_filter="pdf")
    pairs = discover_v2_cross_format_pairs(excel_cases, pdf_cases)
    metafunc.parametrize("pair", pairs, ids=lambda item: item.pair_id)


def _format_verdict(verdict: Any) -> str:
    return (
        f"rule {verdict.rule_index}: pdf={verdict.pdf.status}/{verdict.pdf.execution_class}"
        f"{f'/{verdict.pdf.failure_class}' if verdict.pdf.failure_class else ''}"
        f" excel={verdict.excel.status}/{verdict.excel.execution_class}"
        f"{f'/{verdict.excel.failure_class}' if verdict.excel.failure_class else ''}"
    )


def test_v2_cross_format_pair(pair: Any) -> None:
    if pair is None:
        pytest.skip("V2 cross-format pairs run in the Corpus Pairs variant")

    rules = sorted(set(pair.excel.expected) | set(pair.pdf.expected))
    pdf_observed = collect_v2_rule_outcomes(pair.pdf)
    excel_observed = collect_v2_rule_outcomes(pair.excel)
    pdf_outcomes = {rule: to_format_outcome(pdf_observed.get(rule)) for rule in rules}
    excel_outcomes = {rule: to_format_outcome(excel_observed.get(rule)) for rule in rules}
    verdicts = classify_pair_rules(pair.pair_id, pdf_outcomes, excel_outcomes, KNOWN_PAIR_DIVERGENCES)

    new = [item for item in verdicts if item.kind == "new"]
    changed = [item for item in verdicts if item.kind == "changed"]
    stale = [item for item in verdicts if item.kind == "stale"]
    allowed = sum(1 for item in verdicts if item.kind == "allowed")
    consistent = sum(1 for item in verdicts if item.kind == "consistent")
    print(
        f"pair={pair.pair_id} consistent={consistent} allowed={allowed} "
        f"new={len(new)} changed={len(changed)} stale={len(stale)}"
    )

    parts: list[str] = []
    if new:
        parts.append("new divergences:\n- " + "\n- ".join(_format_verdict(item) for item in new))
    if changed:
        parts.append("changed divergences:\n- " + "\n- ".join(_format_verdict(item) for item in changed))
    if stale:
        parts.append(
            "stale cross-format baseline:\n- "
            + "\n- ".join(f"rule {item.rule_index}" for item in stale)
        )
    if parts:
        pytest.fail(f"{pair.pair_id}:\n" + "\n".join(parts))
