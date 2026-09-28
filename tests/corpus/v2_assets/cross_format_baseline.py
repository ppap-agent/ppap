"""Known PDF vs Excel outcome divergences. Not a second PASS/NG oracle."""

from __future__ import annotations

from cross_format import D, KnownPairDivergence
from execution_outcome import O

KNOWN_PAIR_DIVERGENCES: frozenset[KnownPairDivergence] = frozenset(
    {
        D("1-全功能检验报告_巨一", 3, O("NG"), O("PASS")),
        D("11-过程特殊特性清单-上海大亚", 3, O("NG"), O("PASS")),
        D("11-过程特殊特性清单-巨一", 3, O("NG"), O("PASS")),
        D("11-过程特殊特性清单-继峰", 3, O("NG"), O("PASS")),
        D("11-过程特殊特性清单-继峰 - 无边框", 3, O("NG"), O("PASS")),
        D("13-生产系统产能评估报告-继峰 - 错误", 4, O("PASS"), O("NG")),
        D("13-生产系统产能评估报告-继峰 - 错误", 5, O("PASS"), O("NG")),
        D("13-生产系统产能评估报告-继峰 - 错误", 6, O("PASS"), O("NG")),
        D("8-过程流程图-亚大", 3, O("NG"), O("PASS")),
        D("8-过程流程图-巨一", 3, O("NG"), O("PASS")),
        D("8-过程流程图-继峰", 3, O("NG"), O("PASS")),
        D("8-过程流程图-继峰", 4, O("NG"), O("PASS")),
        D("8-过程流程图-继峰 - 无线框", 3, O("NG"), O("PASS")),
        D("8-过程流程图-继峰 - 无线框", 4, O("NG"), O("PASS")),
    }
)
