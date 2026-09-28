"""Known V2 oracle mismatches from ppap-agent@53a5bd2b.

This is not a second expected-status oracle. The human CSV remains the only
PASS/NG authority. Each record freezes one observed baseline behavior at a
(file, file_type, rule) location: CSV expected status plus the canonical
ExecutionOutcome. Suggestion text is not authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ppap_corpus_contracts.execution_outcome import ExecutionOutcome, O


@dataclass(frozen=True)
class ObservedRuleOutcome:
    expected_status: str
    actual: ExecutionOutcome

    @property
    def is_mismatch(self) -> bool:
        return str(self.expected_status or "").strip().upper() != self.actual.status


@dataclass(frozen=True)
class BaselineRuleBehavior:
    file_name: str
    file_type: str
    rule_index: int
    expected_status: str
    actual: ExecutionOutcome

    @property
    def location(self) -> tuple[str, str, int]:
        return (self.file_name, self.file_type, self.rule_index)


def _norm_status(value: str) -> str:
    return str(value or "").strip().upper()


def behavior_matches(known: BaselineRuleBehavior, observed: ObservedRuleOutcome) -> bool:
    return (
        _norm_status(known.expected_status) == _norm_status(observed.expected_status)
        and known.actual.semantic == observed.actual.semantic
    )


def classify_v2_baseline_outcomes(
    file_name: str,
    file_type: str,
    observed: Mapping[int, ObservedRuleOutcome],
    known: Iterable[BaselineRuleBehavior] | None = None,
) -> tuple[frozenset[int], frozenset[int], frozenset[int]]:
    records = {
        item.rule_index: item
        for item in (BASELINE_RULE_BEHAVIORS if known is None else known)
        if item.file_name == file_name and item.file_type == file_type
    }
    new_mismatches: set[int] = set()
    drifted: set[int] = set()
    mismatched = {rule for rule, outcome in observed.items() if outcome.is_mismatch}
    for rule, outcome in observed.items():
        if not outcome.is_mismatch:
            continue
        record = records.get(rule)
        if record is None:
            new_mismatches.add(rule)
        elif not behavior_matches(record, outcome):
            drifted.add(rule)
    stale = frozenset(records) - frozenset(mismatched)
    return frozenset(new_mismatches), frozenset(drifted), stale


def B(
    file_name: str,
    file_type: str,
    rule_index: int,
    expected_status: str,
    actual_status: str,
    diagnostic_code: str | None = None,
    *,
    execution_class: str | None = None,
) -> BaselineRuleBehavior:
    actual = O(actual_status, execution_class, diagnostic_code)
    return BaselineRuleBehavior(
        file_name=file_name,
        file_type=file_type,
        rule_index=rule_index,
        expected_status=_norm_status(expected_status),
        actual=actual,
    )


# Observed from the current required V2 corpus. Human CSV remains expected
# authority. ERR records freeze execution_failure without raised:* types.
# Product EvidenceDiagnostic codes may be stored on ExecutionOutcome when stable.
BASELINE_RULE_BEHAVIORS: frozenset[BaselineRuleBehavior] = frozenset(
    {
        B("1-全功能检验报告_巨一.pdf", "pdf", 3, "PASS", "NG"),
        B("10-控制计划-常州新泉.pdf", "pdf", 5, "NG", "PASS"),
        B("11-过程特殊特性清单-上海大亚.pdf", "pdf", 3, "PASS", "NG"),
        B("11-过程特殊特性清单-巨一.pdf", "pdf", 3, "PASS", "NG"),
        B("11-过程特殊特性清单-继峰 - 无边框.pdf", "pdf", 3, "PASS", "NG"),
        B("11-过程特殊特性清单-继峰.pdf", "pdf", 3, "PASS", "NG"),
        B("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 4, "NG", "PASS"),
        B("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 5, "NG", "PASS"),
        B("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 6, "NG", "PASS"),
        B("16-零件提交保证书-上海亚大.pdf", "pdf", 3, "NG", "ERR"),
        B("16-零件提交保证书-上海亚大.pdf", "pdf", 5, "NG", "ERR"),
        B("16-零件提交保证书-上海亚大.pdf", "pdf", 8, "NG", "ERR"),
        B("16-零件提交保证书-上海亚大.pdf", "pdf", 9, "NG", "ERR"),
        B("16-零件提交保证书-上海亚大.pdf", "pdf", 10, "NG", "ERR"),
        B("16-零件提交保证书-常州新泉.pdf", "pdf", 3, "NG", "ERR"),
        B("16-零件提交保证书-常州新泉.pdf", "pdf", 5, "PASS", "ERR"),
        B("16-零件提交保证书-常州新泉.pdf", "pdf", 8, "NG", "ERR"),
        B("16-零件提交保证书-常州新泉.pdf", "pdf", 9, "NG", "ERR"),
        B("16-零件提交保证书-常州新泉.pdf", "pdf", 10, "NG", "ERR"),
        B("16-零件提交保证书-继峰 - 无边框.pdf", "pdf", 3, "PASS", "ERR"),
        B("16-零件提交保证书-继峰 - 无边框.pdf", "pdf", 5, "NG", "ERR"),
        B("16-零件提交保证书-继峰 - 无边框.pdf", "pdf", 8, "NG", "ERR"),
        B("16-零件提交保证书-继峰 - 无边框.pdf", "pdf", 9, "NG", "ERR"),
        B("16-零件提交保证书-继峰 - 无边框.pdf", "pdf", 10, "NG", "ERR"),
        B("16-零件提交保证书-继峰.pdf", "pdf", 3, "PASS", "ERR"),
        B("16-零件提交保证书-继峰.pdf", "pdf", 5, "NG", "ERR"),
        B("16-零件提交保证书-继峰.pdf", "pdf", 8, "NG", "ERR"),
        B("16-零件提交保证书-继峰.pdf", "pdf", 9, "NG", "ERR"),
        B("16-零件提交保证书-继峰.pdf", "pdf", 10, "NG", "ERR"),
        B("8-过程流程图-亚大.pdf", "pdf", 3, "PASS", "NG"),
        B("8-过程流程图-巨一.pdf", "pdf", 3, "PASS", "NG"),
        B("8-过程流程图-继峰 - 无线框.pdf", "pdf", 3, "PASS", "NG"),
        B("8-过程流程图-继峰 - 无线框.pdf", "pdf", 4, "PASS", "NG"),
        B("8-过程流程图-继峰.pdf", "pdf", 3, "PASS", "NG"),
        B("8-过程流程图-继峰.pdf", "pdf", 4, "PASS", "NG"),
        B("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 2, "PASS", "NG"),
        B("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 4, "PASS", "NG"),
        B("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 6, "PASS", "NG"),
        B("SHF03-25021669-QC-15 阻燃性 试验报告 4.pdf", "pdf", 2, "PASS", "NG"),
        B("SHF03-25021669-QC-15 阻燃性 试验报告 4.pdf", "pdf", 4, "PASS", "NG"),
        B("W02管路PV试验报告2025052803_QFZ.pdf", "pdf", 4, "PASS", "NG"),
        B("零部件DPV试验报告20231226.pdf", "pdf", 4, "PASS", "NG"),
        B("PV试验报告2024-07-03 13.35.pdf", "scanned", 2, "PASS", "NG"),
        B("PV试验报告2024-07-03 13.35.pdf", "scanned", 3, "PASS", "NG"),
        B("PV试验报告2024-07-03 13.35.pdf", "scanned", 4, "PASS", "NG"),
        B("PV试验报告2024-07-03 13.35.pdf", "scanned", 5, "PASS", "NG"),
    }
)
