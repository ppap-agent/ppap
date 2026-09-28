"""Known PDF vs Excel outcome divergences. Not a second PASS/NG oracle."""

from __future__ import annotations

from tests.corpus.v2_assets.cross_format import D, KnownPairDivergence

KNOWN_PAIR_DIVERGENCES: frozenset[KnownPairDivergence] = frozenset(
    {
        D("1-全功能检验报告_巨一", 3, "NG", "PASS"),
        D("11-过程特殊特性清单-上海大亚", 3, "NG", "PASS"),
        D("11-过程特殊特性清单-巨一", 3, "NG", "PASS"),
        D("11-过程特殊特性清单-继峰", 3, "NG", "PASS"),
        D("11-过程特殊特性清单-继峰 - 无边框", 3, "NG", "PASS"),
        D("13-生产系统产能评估报告-继峰 - 错误", 4, "PASS", "NG"),
        D("13-生产系统产能评估报告-继峰 - 错误", 5, "PASS", "NG"),
        D("13-生产系统产能评估报告-继峰 - 错误", 6, "PASS", "NG"),
        D("8-过程流程图-亚大", 3, "NG", "PASS"),
        D("8-过程流程图-巨一", 3, "NG", "PASS"),
        D("8-过程流程图-继峰", 3, "NG", "PASS"),
        D("8-过程流程图-继峰", 4, "NG", "PASS"),
        D("8-过程流程图-继峰 - 无线框", 3, "NG", "PASS"),
        D("8-过程流程图-继峰 - 无线框", 4, "NG", "PASS"),
    }
)
