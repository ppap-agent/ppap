"""Run V2 agents and normalize each rule to ExecutionOutcome."""

from __future__ import annotations

from ppap_core.contracts import AuditInput

from ppap_corpus_contracts.baseline_exceptions import ObservedRuleOutcome
from ppap_corpus_contracts.execution_outcome import outcome_from_agent_rule
from ppap_corpus_contracts.v2_assets_shared import FileCase, make_dummy_rule


def observe_v2_rule_outcome(case: FileCase, rule_index: int, agent: object, agent_rule_count: int) -> ObservedRuleOutcome:
    expected_status = (case.expected[rule_index].status or "").upper()
    if rule_index > agent_rule_count:
        return ObservedRuleOutcome(
            expected_status=expected_status,
            actual=outcome_from_agent_rule(rule_index=rule_index, agent_rule_count=agent_rule_count),
        )
    try:
        result = agent.check(rule_index - 1)  # type: ignore[attr-defined]
    except Exception as exc:
        return ObservedRuleOutcome(
            expected_status=expected_status,
            actual=outcome_from_agent_rule(
                rule_index=rule_index,
                agent_rule_count=agent_rule_count,
                raised=exc,
            ),
        )
    return ObservedRuleOutcome(
        expected_status=expected_status,
        actual=outcome_from_agent_rule(
            rule_index=rule_index,
            agent_rule_count=agent_rule_count,
            result=result,
        ),
    )


def collect_v2_rule_outcomes(
    case: FileCase,
    verbose_check: bool = False,
    mismatch_only: bool = False,
) -> dict[int, ObservedRuleOutcome]:
    rule_count = max(case.expected.keys())
    audit_file = AuditInput(
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
            print(f"  [{status_mark}] Rule {rule_index}: expected={outcome.expected_status}, actual={outcome.actual.status}/{outcome.actual.execution_class}")
            if expectation.rule_desc:
                rule_desc_short = expectation.rule_desc
                if len(rule_desc_short) > 120:
                    rule_desc_short = rule_desc_short[:117] + "..."
                print(f"      📋 规则: {rule_desc_short}")
            if expectation.manual_reason:
                print(f"      📝 人工判定依据: {expectation.manual_reason}")
            if outcome.actual.diagnostic_code:
                print(f"      ⚙ diagnostic: {outcome.actual.diagnostic_code}")

    if verbose_check:
        mismatches = [rule for rule, outcome in outcomes.items() if outcome.is_mismatch]
        if mismatches:
            print("------ Mismatch Summary ------")
            for rule in mismatches:
                outcome = outcomes[rule]
                extra = f" {outcome.actual.diagnostic_code}" if outcome.actual.diagnostic_code else ""
                print(f"  - rule {rule}: expected {outcome.expected_status}, got {outcome.actual.status}/{outcome.actual.execution_class}{extra}")
            print("------------------------------")
    return outcomes
