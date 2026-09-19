from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest
from dotenv import load_dotenv

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


def test_v2_assets_cases(case: FileCase, verbose_check: bool, mismatch_only: bool) -> None:
    rule_count = max(case.expected.keys())
    audit_file = SimpleAuditFile(
        file_url=str(case.file_path),
        audit_id=case.file_name,
        file_metadata=None,
    )
    agent = case.agent_cls(audit_file, make_dummy_rule(rule_count))
    agent_rule_count = len(getattr(agent, "rule_checkers", []))

    mismatches: List[str] = []
    if verbose_check:
        print("")
        print("========================================")
        print(f"📁 文件: {case.file_name}")
        print(f"🧭 路径: {case.file_path}")
        print(f"🧪 Agent: {case.agent_cls.__name__}")
        print(f"🧾 规则数量: expected={rule_count}, agent={agent_rule_count}")
        print("========================================")
    for rule_index, expectation in sorted(case.expected.items()):
        expected_status = expectation.status
        if rule_index > agent_rule_count:
            mismatches.append(f"rule {rule_index}: agent has {agent_rule_count} rules")
            continue
        result = agent.check(rule_index - 1)
        actual = (getattr(result, "status", "") or "").upper()
        suggestion = getattr(result, "suggestions", "") or ""
        suggestion_short = " ".join(suggestion.strip().split())
        if len(suggestion_short) > 160:
            suggestion_short = suggestion_short[:157] + "..."

        is_mismatch = actual != expected_status
        if verbose_check and (not mismatch_only or is_mismatch):
            status_mark = "✓" if actual == expected_status else "✗"
            print(f"  [{status_mark}] Rule {rule_index}: expected={expected_status}, actual={actual}")
            if expectation.rule_desc:
                rule_desc_short = expectation.rule_desc
                if len(rule_desc_short) > 120:
                    rule_desc_short = rule_desc_short[:117] + "..."
                print(f"      📋 规则: {rule_desc_short}")
            if expectation.manual_reason:
                print(f"      📝 人工判定依据: {expectation.manual_reason}")
            if suggestion_short:
                print(f"      🤖 Agent建议: {suggestion_short}")

        if actual != expected_status:
            mismatches.append(f"rule {rule_index}: expected {expected_status}, got {actual}. {suggestion_short}")

    if verbose_check and mismatches:
        print("------ Mismatch Summary ------")
        for mismatch in mismatches:
            print(f"  - {mismatch}")
        print("------------------------------")

    if mismatches:
        pytest.fail(f"Mismatched rule results for {case.file_name}:\n- " + "\n- ".join(mismatches))
