"""Known V2 oracle mismatches from ppap-agent@53a5bd2b.

This is not a second expected-status oracle. The human CSV remains the only
PASS/NG authority. Each tuple is one allowed (file, file_type, rule) mismatch;
any other mismatch, or a listed mismatch that disappears, fails the required
V2 corpus.
"""

from __future__ import annotations

from typing import Iterable

# (file_name, file_type, rule_index) with 1-based oracle rule numbers.
BASELINE_RULE_MISMATCHES: frozenset[tuple[str, str, int]] = frozenset(
    {
        ("1-全功能检验报告_巨一.pdf", "pdf", 3),
        ("10-控制计划-常州新泉.pdf", "pdf", 5),
        ("11-过程特殊特性清单-上海大亚.pdf", "pdf", 3),
        ("11-过程特殊特性清单-巨一.pdf", "pdf", 3),
        ("11-过程特殊特性清单-继峰 - 无边框.pdf", "pdf", 3),
        ("11-过程特殊特性清单-继峰.pdf", "pdf", 3),
        ("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 4),
        ("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 5),
        ("13-生产系统产能评估报告-继峰 - 错误.pdf", "pdf", 6),
        ("8-过程流程图-亚大.pdf", "pdf", 3),
        ("8-过程流程图-巨一.pdf", "pdf", 3),
        ("8-过程流程图-继峰 - 无线框.pdf", "pdf", 3),
        ("8-过程流程图-继峰 - 无线框.pdf", "pdf", 4),
        ("8-过程流程图-继峰.pdf", "pdf", 3),
        ("8-过程流程图-继峰.pdf", "pdf", 4),
        ("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 2),
        ("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 4),
        ("NJ24010097_EVB理想X04_PACK3_绝缘耐压等电位正本-DVP试验报告.pdf", "pdf", 6),
        ("PV试验报告2024-07-03 13.35.pdf", "scanned", 2),
        ("PV试验报告2024-07-03 13.35.pdf", "scanned", 3),
        ("PV试验报告2024-07-03 13.35.pdf", "scanned", 4),
        ("PV试验报告2024-07-03 13.35.pdf", "scanned", 5),
        ("SHF03-25021669-QC-15 阻燃性 试验报告 4.pdf", "pdf", 2),
        ("SHF03-25021669-QC-15 阻燃性 试验报告 4.pdf", "pdf", 4),
        ("W02管路PV试验报告2025052803_QFZ.pdf", "pdf", 4),
        ("零部件DPV试验报告20231226.pdf", "pdf", 4),
    }
)


def split_v2_baseline_mismatches(
    file_name: str,
    file_type: str,
    mismatched_rules: Iterable[int],
    known: frozenset[tuple[str, str, int]] = BASELINE_RULE_MISMATCHES,
) -> tuple[frozenset[int], frozenset[int]]:
    actual = frozenset(mismatched_rules)
    known_rules = {rule for name, kind, rule in known if name == file_name and kind == file_type}
    return frozenset(actual - known_rules), frozenset(known_rules - actual)
