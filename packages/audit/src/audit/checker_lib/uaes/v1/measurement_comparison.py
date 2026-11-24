"""UAES U02: measurement comparison report, UAES v1.0 revision 3 (MCMP:1).

The `…R3` readers add the clause method below to the UAES measurement comparison readers, reusing their title and
sign-off row reading. Verdicts are decided in code; the image model only reads signatures and the supplier's seal
off the original.
"""

from __future__ import annotations

from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes import records
from audit.checker_lib.uaes.records import UaesMeasurementComparisonAgent, UaesMeasurementComparisonDocxAgent
from audit.engine.rule_engine import Decision, GuardStop, RuleContext

SIGNERS = ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师")
FIELDS = {
    **{f"sign_{index}": f"测量比较报告底部“{name}”字段对应区域有真实签字；预印姓名、其他位置的签字或空白为false。" for index, name in enumerate(SIGNERS)},
    "supplier_seal": "测量比较报告底部“供应商”签署区盖有供应商的有效印章；只有“盖章”文字或空白为false。",
}


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def judge(values: dict[str, Any]) -> tuple[str, str]:
    unsigned = [name for index, name in enumerate(SIGNERS) if values.get(f"sign_{index}") is False]
    problems = []
    if unsigned:
        problems.append("、".join(f"“{name}”" for name in unsigned) + "区域没有签字")
    if values.get("supplier_seal") is False:
        problems.append("供应商签署区没有加盖供应商有效印章")
    if problems:
        return "NG", "测量比较报告" + "；".join(problems) + "。"
    if any(value is None for value in values.values()):
        return "UNABLE", "测量比较报告底部四方签字或供应商印章尚未能从原件全部核实，需人工复核。"
    return "PASS", "测量比较报告底部“供应商”“UAES技术中心”“UAES测量部门”“UAES供应商质量开发工程师”四方均已签字，供应商签署区有供应商印章。"


class ComparisonClauses(UaesMeasurementComparisonAgent):
    """MCMP:1 over the declared UAES measurement comparison reader (`READER` is its module)."""

    READER: Any = None

    @rule("测量比较报告四方签字与供应商印章")
    def mcmp_1(self, ctx: RuleContext):
        if not self.mentioned(ctx, self.READER.resources.COMPARISON_TITLE):
            return self.absent("测量比较报告")
        cells = self.sign_off(ctx)
        if cells is not None:
            for cell in cells:
                cell.value()
        else:
            missing = [label for label in SIGNERS if not self.pdf_mentions(label)]
            if missing or not str(self.audit_file.file_url).lower().endswith(".pdf"):
                return self.absent("报告底部同一行的" + "、".join(f"“{label}”" for label in (missing or SIGNERS)) + "字段")
        values, _unreadable = self._from_legacy(lambda agent: agent.visual_observation(FIELDS))
        status, message = judge(values)
        if status == "UNABLE":
            raise unable(message)
        return status, message


class ComparisonDocxClauses(UaesMeasurementComparisonDocxAgent):
    """MCMP:1 over the declared UAES Word measurement comparison reader."""

    READER: Any = None

    @rule("测量比较报告四方签字与供应商印章")
    def mcmp_1(self):
        facts = self.native()
        facts.require_text(self.READER.resources.COMPARISON_TITLE)
        for label in SIGNERS:
            facts.require_text(label)
        values, _unreadable = self.visual_observation(FIELDS)
        return judge(values)


# ---- the readers the leaf names ----


class UaesMeasurementComparisonAgentR3(ComparisonClauses):
    READER = records


class UaesMeasurementComparisonDocxAgentR3(ComparisonDocxClauses):
    READER = records
