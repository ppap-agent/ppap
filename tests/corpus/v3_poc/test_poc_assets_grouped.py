from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import pytest
from dotenv import load_dotenv
from ppap_core.rule_profile import RULE_PROFILE_V3
from ppap_excel_audit_engine.routing import resolve_engine_agent_class

from .poc_oracle import (
    DEFAULT_POC_DATASET_ROOT,
    DEFAULT_POC_EXPECTED_XLSX,
    POC_SEQ_TO_DOC_TYPE,
    OracleRuleExpectation,
    PocCase,
    SimpleAuditFile,
    build_bundle_map,
    build_group_context_for_bundle,
    load_poc_cases,
    parse_seq_filter,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT
load_dotenv(APP_ROOT / ".env")


@dataclass(frozen=True)
class DummyRule:
    rules: List[Dict]
    customers: List[str]
    models: List[str]
    parts: List[str]
    rule_profile: str = RULE_PROFILE_V3
    type: str | None = None
    rule_id: str = "local_rule"


@dataclass(frozen=True)
class RuleCheckResult:
    rule_index: int
    expectation: OracleRuleExpectation
    actual_status: str
    suggestions: str


@dataclass(frozen=True)
class CaseEvaluation:
    case: PocCase
    agent_name: str
    agent_rule_count: int
    expected_rule_count: int
    parity_missing_indices: List[int]
    checks: List[RuleCheckResult]


_RUNTIME_BUNDLE_CACHE: Dict[str, tuple[Dict[str, PocCase], object]] = {}
_ALL_BUNDLE_CASES: Dict[str, List[PocCase]] = {}


def _make_dummy_rule(rule_count: int, file_seq: str) -> DummyRule:
    rules: List[Dict[str, str]] = []
    for idx in range(rule_count):
        rules.append(
            {
                "id": f"local_rule_{idx + 1}",
                "category": "local_excel_check",
                "priority": "P1",
                "rule_to_check": f"POC rule {idx + 1}",
                "type_of_rule": "table",
            }
        )
    return DummyRule(
        rules=rules,
        customers=[],
        models=[],
        parts=[],
        type=(POC_SEQ_TO_DOC_TYPE[file_seq].value[1] if file_seq in POC_SEQ_TO_DOC_TYPE else None),
    )


def _agent_class_for_case(case: PocCase) -> Any:
    doc_type = POC_SEQ_TO_DOC_TYPE.get(case.file_seq)
    if doc_type is None:
        raise AssertionError(f"Unsupported file seq '{case.file_seq}' for POC test")
    agent_cls = resolve_engine_agent_class(
        doc_type,
        str(case.file_path),
        RULE_PROFILE_V3,
    )
    if agent_cls is None:
        raise AssertionError(f"No agent class resolved for seq={case.file_seq}, doc_type={doc_type.name}, file={case.file_path}")
    return agent_cls


@lru_cache(maxsize=None)
def _accepts_group_context(agent_cls: Any) -> bool:
    try:
        params = inspect.signature(agent_cls.__init__).parameters.values()
    except (TypeError, ValueError):
        return True
    for param in params:
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "group_context":
            return True
    return False


def _get_runtime_bundle(bundle_cases: List[PocCase]) -> Any:
    if not bundle_cases:
        raise AssertionError("bundle_cases cannot be empty")

    bundle_id = bundle_cases[0].bundle_id
    cached = _RUNTIME_BUNDLE_CACHE.get(bundle_id)
    if cached is not None:
        return cached

    runtime_case_map = {case.file_name: case for case in bundle_cases}
    context = build_group_context_for_bundle(bundle_cases)
    _RUNTIME_BUNDLE_CACHE[bundle_id] = (runtime_case_map, context)
    return runtime_case_map, context


def evaluate_case(
    case: PocCase,
    bundle_cases: List[PocCase],
) -> CaseEvaluation:
    runtime_case_map, context = _get_runtime_bundle(bundle_cases)
    runtime_case = runtime_case_map[case.file_name]

    audit_file = SimpleAuditFile(
        file_url=str(runtime_case.file_path),
        audit_id=runtime_case.file_name,
    )
    expected_rule_indices = sorted(case.expected.keys())
    expected_rule_count = len(expected_rule_indices)
    rule_count = max(expected_rule_indices) if expected_rule_indices else 0

    agent_cls = _agent_class_for_case(runtime_case)
    rule = _make_dummy_rule(rule_count=rule_count, file_seq=case.file_seq)

    if _accepts_group_context(agent_cls):
        agent = agent_cls(audit_file, rule, group_context=context)
    else:
        agent = agent_cls(audit_file, rule)

    agent_rule_count = len(getattr(agent, "rule_checkers", []))
    parity_missing_indices = [idx for idx in expected_rule_indices if idx > agent_rule_count]

    checks: List[RuleCheckResult] = []
    if not parity_missing_indices:
        for rule_index in expected_rule_indices:
            expectation = case.expected[rule_index]
            try:
                result = agent.check(rule_index - 1)
                actual_status = (getattr(result, "status", "") or "").upper()
                suggestions = getattr(result, "suggestions", "") or ""
            except Exception as exc:
                actual_status = "ERR"
                suggestions = str(exc)
            checks.append(
                RuleCheckResult(
                    rule_index=rule_index,
                    expectation=expectation,
                    actual_status=actual_status,
                    suggestions=suggestions,
                )
            )

    return CaseEvaluation(
        case=case,
        agent_name=agent_cls.__name__,
        agent_rule_count=agent_rule_count,
        expected_rule_count=expected_rule_count,
        parity_missing_indices=parity_missing_indices,
        checks=checks,
    )


def _build_selected_cases(config: Any) -> List[PocCase]:
    global _ALL_BUNDLE_CASES

    bundle = config.getoption("--bundle", default=None)
    seq_raw = config.getoption("--seq", default=None)
    try:
        seq_set = parse_seq_filter(seq_raw)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc

    all_cases = load_poc_cases(
        dataset_root=DEFAULT_POC_DATASET_ROOT,
        expected_xlsx=DEFAULT_POC_EXPECTED_XLSX,
    )
    _ALL_BUNDLE_CASES = build_bundle_map(all_cases)

    selected_cases = load_poc_cases(
        bundle=bundle,
        seq_set=seq_set,
        dataset_root=DEFAULT_POC_DATASET_ROOT,
        expected_xlsx=DEFAULT_POC_EXPECTED_XLSX,
    )
    return selected_cases


def pytest_generate_tests(metafunc: Any) -> None:
    if "case" not in metafunc.fixturenames:
        return

    try:
        cases = _build_selected_cases(metafunc.config)
    except FileNotFoundError as exc:
        pytest.skip(str(exc), allow_module_level=True)
        return

    if not cases:
        raise AssertionError("No POC test cases found after applying filters.")

    metafunc.parametrize("case", cases, ids=lambda c: c.file_name)


@pytest.fixture
def verbose_check(request: Any) -> Any:
    return request.config.getoption("--verbose-check", default=False)


@pytest.fixture
def mismatch_only(request: Any) -> Any:
    return request.config.getoption("--mismatch-only", default=False)


def test_poc_assets_grouped(case: PocCase, verbose_check: bool, mismatch_only: bool) -> None:
    bundle_cases = _ALL_BUNDLE_CASES.get(case.bundle_id)
    if not bundle_cases:
        pytest.fail(f"Missing bundle context for {case.bundle_id}")

    try:
        evaluation = evaluate_case(case, bundle_cases)
    except RuntimeError as exc:
        pytest.fail(str(exc))

    if evaluation.parity_missing_indices:
        pytest.fail(
            "RuleParityGuard failed for "
            f"{case.file_name} (bundle={case.bundle_id}, seq={case.file_seq}): "
            f"expected_rules={evaluation.expected_rule_count}, "
            f"agent_rules={evaluation.agent_rule_count}, "
            f"missing_rule_indices={evaluation.parity_missing_indices}"
        )

    mismatches: List[str] = []
    if verbose_check:
        print("")
        print("========================================")
        print(f"Bundle: {case.bundle_id}")
        print(f"文件: {case.file_name}")
        print(f"序号: {case.file_seq}")
        print(f"Agent: {evaluation.agent_name}")
        print(f"规则数量: expected={evaluation.expected_rule_count}, agent={evaluation.agent_rule_count}")
        print("========================================")

    for check in evaluation.checks:
        expected_status = check.expectation.status
        actual_status = check.actual_status
        suggestions = " ".join(check.suggestions.strip().split())
        if len(suggestions) > 180:
            suggestions = suggestions[:177] + "..."

        is_mismatch = actual_status != expected_status
        if verbose_check and (not mismatch_only or is_mismatch):
            status_mark = "OK" if not is_mismatch else "NG"
            print(f"  [{status_mark}] Rule {check.rule_index}: expected={expected_status}, actual={actual_status}")
            if check.expectation.rule_desc:
                print(f"      规则: {check.expectation.rule_desc}")
            if check.expectation.manual_reason:
                print(f"      人工判定依据: {check.expectation.manual_reason}")
            if suggestions:
                print(f"      Agent建议: {suggestions}")

        if is_mismatch:
            mismatches.append(
                "/".join(
                    [
                        f"bundle_id={case.bundle_id}",
                        f"file_name={case.file_name}",
                        f"file_seq={case.file_seq}",
                        f"rule_index={check.rule_index}",
                        f"rule_desc={check.expectation.rule_desc}",
                        f"manual_reason={check.expectation.manual_reason}",
                        f"actual_suggestions={suggestions}",
                    ]
                )
            )

    if mismatches:
        pytest.fail(f"Mismatched rule results for {case.file_name}:\n- " + "\n- ".join(mismatches))
