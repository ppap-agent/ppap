from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cross_format import (
    classify_pair_rules,
    discover_v2_cross_format_pairs,
)
from cross_format_baseline import KNOWN_PAIR_DIVERGENCES
from execution_outcome import MISSING_OUTCOME
from v2_assets_shared import build_cases
from v2_observe import collect_v2_rule_outcomes


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
    def side(outcome: Any) -> str:
        extra = f"/{outcome.diagnostic_code}" if outcome.diagnostic_code else ""
        return f"{outcome.status}/{outcome.execution_class}{extra}"

    return f"rule {verdict.rule_index}: pdf={side(verdict.pdf)} excel={side(verdict.excel)}"


def test_v2_cross_format_pair(pair: Any) -> None:
    if pair is None:
        pytest.skip("V2 cross-format pairs run in the Corpus Pairs variant")

    rules = sorted(set(pair.excel.expected) | set(pair.pdf.expected))
    pdf_observed = collect_v2_rule_outcomes(pair.pdf)
    excel_observed = collect_v2_rule_outcomes(pair.excel)
    pdf_outcomes = {
        rule: pdf_observed[rule].actual if rule in pdf_observed else MISSING_OUTCOME for rule in rules
    }
    excel_outcomes = {
        rule: excel_observed[rule].actual if rule in excel_observed else MISSING_OUTCOME for rule in rules
    }
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
