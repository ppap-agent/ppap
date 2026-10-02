"""V2 Excel oracle through the Standard product round."""

from __future__ import annotations

from pathlib import Path

import pytest
from ppap_application import run_round_from_bytes
from ppap_core.rule_profile import RULE_PROFILE_V2
from ppap_corpus_contracts.baseline_exceptions import ObservedRuleOutcome, classify_v2_baseline_outcomes
from ppap_corpus_contracts.execution_outcome import (
    EXECUTION_FAILURE,
    EXECUTION_RESULT,
    MISSING_RULE,
    ExecutionOutcome,
)
from ppap_corpus_contracts.product_round import (
    MATCH,
    SETUP_FAILURE,
    STATUS_MISMATCH,
    RuleVerdict,
    bound_type_name,
    compare_file,
    execute_product_round,
    format_verdicts,
)
from ppap_corpus_contracts.product_round import (
    MISSING_RULE as VERDICT_MISSING,
)
from ppap_corpus_contracts.v2_assets_shared import build_cases_from_long_feedback

_REPO = Path(__file__).resolve().parents[3]


def _outcome(verdict: RuleVerdict) -> ExecutionOutcome:
    if verdict.kind == MATCH:
        return ExecutionOutcome(status=verdict.expected, execution_class=EXECUTION_RESULT)
    if verdict.kind == STATUS_MISMATCH:
        return ExecutionOutcome(status=verdict.actual or "ERR", execution_class=EXECUTION_RESULT)
    if verdict.kind == VERDICT_MISSING:
        return ExecutionOutcome(status="ERR", execution_class=MISSING_RULE)
    return ExecutionOutcome(status="ERR", execution_class=EXECUTION_FAILURE)


def test_v2_product_round_respects_oracle_baseline(request: pytest.FixtureRequest) -> None:
    """Follow the Corpus matrix. Each --ftype job runs one slice."""
    file_type = request.config.getoption("--ftype") or "excel"
    cases = build_cases_from_long_feedback(
        dataset_root=_REPO / "assets" / "v2.0plus",
        expected_csv=_REPO / "assets" / "全量测试 - 4.8文件测试反馈（云端） - 4.8（云端）.csv",
        ftype_filter=file_type,
    )
    failures: list[RuleVerdict] = []
    for case in cases:
        doc_type = bound_type_name(case.agent_cls, case.file_name, RULE_PROFILE_V2)
        if doc_type is None:
            failures.append(RuleVerdict(case.file_name, 0, SETUP_FAILURE, "", detail="no bound document type"))
            continue
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
        if any(item.kind == SETUP_FAILURE for item in verdicts):
            failures.extend(item for item in verdicts if item.kind == SETUP_FAILURE)
            continue
        observed = {item.rule_index: ObservedRuleOutcome(item.expected, _outcome(item)) for item in verdicts if item.rule_index}
        new_mismatches, drifted, stale = classify_v2_baseline_outcomes(case.file_name, file_type, observed)
        bad = new_mismatches | drifted | stale
        failures.extend(item for item in verdicts if item.rule_index in bad)
    if failures:
        pytest.fail(format_verdicts(failures))
