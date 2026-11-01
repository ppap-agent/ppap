"""V2 Excel oracle through the Standard product round."""

from __future__ import annotations

from pathlib import Path

import pytest
from audit.rule_profile import RULE_PROFILE_V2
from corpus_contracts.baseline_exceptions import ObservedRuleOutcome, classify_v2_baseline_outcomes
from corpus_contracts.execution_outcome import (
    EXECUTION_FAILURE,
    EXECUTION_RESULT,
    MISSING_RULE,
    ExecutionOutcome,
)
from corpus_contracts.product_round import (
    MATCH,
    SETUP_FAILURE,
    STATUS_MISMATCH,
    RuleVerdict,
    bound_type_name,
    compare_file,
    execute_product_round,
    format_verdicts,
)
from corpus_contracts.product_round import (
    MISSING_RULE as VERDICT_MISSING,
)
from corpus_contracts.v2_assets_shared import FileCase, build_cases_from_long_feedback
from product import run_round_from_bytes

_REPO = Path(__file__).resolve().parents[3]


def _outcome(verdict: RuleVerdict) -> ExecutionOutcome:
    if verdict.kind == MATCH:
        return ExecutionOutcome(status=verdict.expected, execution_class=EXECUTION_RESULT)
    if verdict.kind == STATUS_MISMATCH:
        return ExecutionOutcome(status=verdict.actual or "ERR", execution_class=EXECUTION_RESULT)
    if verdict.kind == VERDICT_MISSING:
        return ExecutionOutcome(status="ERR", execution_class=MISSING_RULE)
    return ExecutionOutcome(status="ERR", execution_class=EXECUTION_FAILURE)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """One immutable oracle case per pytest item: xdist can distribute files."""
    if "product_case" not in metafunc.fixturenames:
        return
    file_type = metafunc.config.getoption("--ftype") or "excel"
    cases = build_cases_from_long_feedback(
        dataset_root=_REPO / "assets" / "v2.0plus",
        expected_csv=_REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv",
        ftype_filter=file_type,
    )
    if not cases:
        raise pytest.UsageError(f"No V2 Product Round oracle cases for {file_type}")
    metafunc.parametrize("product_case", cases, ids=lambda case: case.file_name)


def test_v2_product_round_respects_oracle_baseline(product_case: FileCase, request: pytest.FixtureRequest) -> None:
    """Check one file with the same oracle as the former serial file loop."""
    file_type = request.config.getoption("--ftype") or "excel"
    case = product_case
    doc_type = bound_type_name(case.agent_cls, case.file_name, RULE_PROFILE_V2)
    if doc_type is None:
        pytest.fail(format_verdicts([RuleVerdict(case.file_name, 0, SETUP_FAILURE, "", detail="no bound document type")]))
    result, versions, setup_error = execute_product_round(
        [(case.file_name, case.file_path, doc_type)],
        profile=RULE_PROFILE_V2,
        round_id=case.file_name,
        runner=run_round_from_bytes,
    )
    verdicts = compare_file(
        file_name=case.file_name,
        version_id=versions.get(case.file_name, ""),
        expected={index: item.status for index, item in case.expected.items()},
        result=result,
        setup_error=setup_error,
    )
    setup_failures = [item for item in verdicts if item.kind == SETUP_FAILURE]
    if setup_failures:
        pytest.fail(format_verdicts(setup_failures))
    observed = {item.rule_index: ObservedRuleOutcome(item.expected, _outcome(item)) for item in verdicts if item.rule_index}
    new_mismatches, drifted, stale = classify_v2_baseline_outcomes(case.file_name, file_type, observed)
    bad = new_mismatches | drifted | stale
    failures = [item for item in verdicts if item.rule_index in bad]
    if failures:
        pytest.fail(format_verdicts(failures))
