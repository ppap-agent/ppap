from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest
from dotenv import load_dotenv

from .baseline_exceptions import (
    ObservedRuleOutcome,
    classify_v2_baseline_outcomes,
)
from .v2_assets_shared import (
    APP_ROOT,
    FileCase,
    SimpleAuditFile,
    build_cases,
    make_dummy_rule,
)

# Load environment variables from .env file (API keys, etc.)
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
    """Fixture to access verbose-check option."""
    return request.config.getoption("--verbose-check", default=False)


@pytest.fixture
def mismatch_only(request: Any) -> Any:
    """Fixture to access mismatch-only option."""
    return request.config.getoption("--mismatch-only", default=False)


def _failure_class_from_result(result: Any) -> str | None:
    status = (getattr(result, "status", "") or "").upper()
    if status != "ERR":
        return None
    evidence = getattr(result, "evidence", None)
    diagnostics = getattr(evidence, "diagnostics", ()) or ()
    codes = [
        str(getattr(item, "code", "") or "")
        for item in diagnostics
        if str(getattr(item, "severity", "") or "") == "error" and str(getattr(item, "code", "") or "")
    ]
    return codes[0] if codes else None


def observe_v2_rule_outcome(case: FileCase, rule_index: int, agent: Any, agent_rule_count: int) -> ObservedRuleOutcome:
    expected_status = (case.expected[rule_index].status or "").upper()
    if rule_index > agent_rule_count:
        return ObservedRuleOutcome(
            expected_status=expected_status,
            actual_status="ERR",
            failure_class="missing_agent_rule",
        )
    try:
        result = agent.check(rule_index - 1)
    except Exception as exc:
        return ObservedRuleOutcome(
            expected_status=expected_status,
            actual_status="ERR",
            failure_class=f"raised:{type(exc).__name__}",
        )
    actual_status = (getattr(result, "status", "") or "").upper() or "ERR"
    return ObservedRuleOutcome(
        expected_status=expected_status,
        actual_status=actual_status,
        failure_class=_failure_class_from_result(result),
    )


def collect_v2_rule_outcomes(case: FileCase, verbose_check: bool = False, mismatch_only: bool = False) -> dict[int, ObservedRuleOutcome]:
    rule_count = max(case.expected.keys())
    audit_file = SimpleAuditFile(
        file_url=str(case.file_path),
        audit_id=case.file_name,
        file_metadata={},
    )
    agent = case.agent_cls(audit_file, make_dummy_rule(rule_count))
    agent_rule_count = len(getattr(agent, "rule_checkers", []))

    outcomes: dict[int, ObservedRuleOutcome] = {}
    if verbose_check:
        print("")
        print("========================================")
        print(f"📁 文件: {case.file_name}")
        print(f"🧭 路径: {case.file_path}")
        print(f"🧪 Agent: {case.agent_cls.__name__}")
        print(f"🧾 规则数量: expected={rule_count}, agent={agent_rule_count}")
        print("========================================")
    for rule_index, expectation in sorted(case.expected.items()):
        outcome = observe_v2_rule_outcome(case, rule_index, agent, agent_rule_count)
        outcomes[rule_index] = outcome
        if verbose_check and (not mismatch_only or outcome.is_mismatch):
            status_mark = "✓" if not outcome.is_mismatch else "✗"
            print(
                f"  [{status_mark}] Rule {rule_index}: expected={outcome.expected_status}, "
                f"actual={outcome.actual_status}"
            )
            if expectation.rule_desc:
                rule_desc_short = expectation.rule_desc
                if len(rule_desc_short) > 120:
                    rule_desc_short = rule_desc_short[:117] + "..."
                print(f"      📋 规则: {rule_desc_short}")
            if expectation.manual_reason:
                print(f"      📝 人工判定依据: {expectation.manual_reason}")
            if outcome.failure_class:
                print(f"      ⚙ failure_class: {outcome.failure_class}")

    if verbose_check:
        mismatches = [rule for rule, outcome in outcomes.items() if outcome.is_mismatch]
        if mismatches:
            print("------ Mismatch Summary ------")
            for rule in mismatches:
                outcome = outcomes[rule]
                extra = f" class={outcome.failure_class}" if outcome.failure_class else ""
                print(
                    f"  - rule {rule}: expected {outcome.expected_status}, "
                    f"got {outcome.actual_status}{extra}"
                )
            print("------------------------------")
    return outcomes


def collect_v2_rule_mismatches(case: FileCase, verbose_check: bool = False, mismatch_only: bool = False) -> dict[int, str]:
    outcomes = collect_v2_rule_outcomes(case, verbose_check=verbose_check, mismatch_only=mismatch_only)
    return {
        rule: (
            f"rule {rule}: expected {outcome.expected_status}, got {outcome.actual_status}"
            + (f" class={outcome.failure_class}" if outcome.failure_class else "")
        )
        for rule, outcome in outcomes.items()
        if outcome.is_mismatch
    }


def test_v2_assets_cases(case: FileCase, verbose_check: bool, mismatch_only: bool) -> None:
    outcomes = collect_v2_rule_outcomes(case, verbose_check=verbose_check, mismatch_only=mismatch_only)
    new_mismatches, drifted, stale = classify_v2_baseline_outcomes(case.file_name, case.file_type, outcomes)
    parts: List[str] = []
    if new_mismatches:
        parts.append(
            "new mismatches:\n- "
            + "\n- ".join(
                f"rule {rule}: expected {outcomes[rule].expected_status}, got {outcomes[rule].actual_status}"
                + (f" class={outcomes[rule].failure_class}" if outcomes[rule].failure_class else "")
                for rule in sorted(new_mismatches)
            )
        )
    if drifted:
        parts.append(
            "baseline behavior changed:\n- "
            + "\n- ".join(
                f"rule {rule}: expected {outcomes[rule].expected_status}, got {outcomes[rule].actual_status}"
                + (f" class={outcomes[rule].failure_class}" if outcomes[rule].failure_class else "")
                for rule in sorted(drifted)
            )
        )
    if stale:
        parts.append(
            "stale baseline exceptions (no longer mismatch):\n- "
            + "\n- ".join(f"rule {rule}" for rule in sorted(stale))
        )
    if parts:
        pytest.fail(f"{case.file_type} {case.file_name}:\n" + "\n".join(parts))
