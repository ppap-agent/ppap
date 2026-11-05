from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest
from corpus_contracts.baseline_exceptions import classify_v2_baseline_outcomes
from corpus_contracts.poc_oracle import deferred_full_scan_reason
from corpus_contracts.v2_assets_shared import APP_ROOT, FileCase, build_cases
from corpus_contracts.v2_observe import collect_v2_rule_outcomes, format_v2_rule_outcome
from dotenv import load_dotenv

load_dotenv(APP_ROOT / ".env")


def _resolve_required_v2_inputs(config: Any) -> tuple[Path, Path]:
    dataset_root_raw = config.getoption("--v2-dataset-root", default=None)
    expected_csv_raw = config.getoption("--v2-expected-csv", default=None)

    if not dataset_root_raw or not expected_csv_raw:
        raise pytest.UsageError("v2_assets tests require explicit inputs: --v2-dataset-root <path> --v2-expected-csv <path>")

    dataset_root = Path(str(dataset_root_raw)).expanduser().resolve()
    expected_csv = Path(str(expected_csv_raw)).expanduser().resolve()

    if not dataset_root.exists():
        raise pytest.UsageError(f"Missing dataset root: {dataset_root}")
    if not expected_csv.exists():
        raise pytest.UsageError(f"Missing expected CSV: {expected_csv}")

    return dataset_root, expected_csv


def pytest_generate_tests(metafunc: Any) -> None:
    """Generate test cases with ftype filter from command line."""
    if "case" in metafunc.fixturenames:
        ftype_filter = metafunc.config.getoption("--ftype", default=None)
        dataset_root, expected_csv = _resolve_required_v2_inputs(metafunc.config)
        cases = build_cases(
            dataset_root=dataset_root,
            expected_csv=expected_csv,
            ftype_filter=ftype_filter,
            include_unlabeled=False,
        )
        metafunc.parametrize("case", cases, ids=lambda c: c.file_name)


@pytest.fixture
def verbose_check(request: Any) -> Any:
    return request.config.getoption("--verbose-check", default=False)


@pytest.fixture
def mismatch_only(request: Any) -> Any:
    return request.config.getoption("--mismatch-only", default=False)


def collect_v2_rule_mismatches(case: FileCase, verbose_check: bool = False, mismatch_only: bool = False) -> dict[int, str]:
    outcomes = collect_v2_rule_outcomes(case, verbose_check=verbose_check, mismatch_only=mismatch_only)
    return {rule: f"rule {rule}: {format_v2_rule_outcome(outcome)}" for rule, outcome in outcomes.items() if outcome.is_mismatch}


def test_v2_assets_cases(case: FileCase, verbose_check: bool, mismatch_only: bool) -> None:
    reason = deferred_full_scan_reason(case.file_path)
    if reason:
        pytest.skip(reason)
    outcomes = collect_v2_rule_outcomes(case, verbose_check=verbose_check, mismatch_only=mismatch_only)
    new_mismatches, drifted, stale = classify_v2_baseline_outcomes(case.file_name, case.file_type, outcomes)
    parts: List[str] = []
    if new_mismatches:
        parts.append("new mismatches:\n- " + "\n- ".join(f"rule {rule}: {format_v2_rule_outcome(outcomes[rule])}" for rule in sorted(new_mismatches)))
    if drifted:
        parts.append("baseline behavior changed:\n- " + "\n- ".join(f"rule {rule}: {format_v2_rule_outcome(outcomes[rule])}" for rule in sorted(drifted)))
    if stale:
        parts.append("stale baseline exceptions (no longer mismatch):\n- " + "\n- ".join(f"rule {rule}" for rule in sorted(stale)))
    if parts:
        pytest.fail(f"{case.file_type} {case.file_name}:\n" + "\n".join(parts))
