"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_2(self):
        facts = self.native()
        table = facts.table(("检具清单",))
        candidates = []
        for text, locator in facts.observations():
            if locator.get("sheet") != table["id"]:
                continue
            match = re.fullmatch(r"[A-Z]+([0-9]+)", str(locator.get("cell", "")))
            if match and int(match.group(1)) <= 5 and "检具清单" in text:
                candidates.append((text, locator))
        if len(candidates) != 1:
            raise UnknownFact("检具清单没有唯一可确认的标题位置。")
        text, locator = candidates[0]
        self.witness(locator, text)
        return ("PASS", "检具清单标题有车型代号前缀。") if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*项目检具清单", text.strip()) else ("NG", "检具清单标题不满足车型代号前缀格式。")

    def rule_3(self):
        values = self.visual_pdf(
            {
                "negative_attachment": "所附文件区域存在实际勾选不合格、非完成或未完成的文件，不能把预印状态文字当作勾选。",
                "conclusion_filled": "本检具认可报告的结论区域有实际填写的内容，空栏和结论标签不算。",
            }
        )
        if values["negative_attachment"] is False:
            return "PASS", "已核实附件没有实际勾选不合格或非完成。"
        if values["negative_attachment"] is True and values["conclusion_filled"] is True:
            return "PASS", "存在负向附件选择，且结论已填写。"
        if values["negative_attachment"] is True and values["conclusion_filled"] is False:
            return "NG", "附件勾选不合格或非完成，但结论没有填写。"
        raise UnknownFact("所附文件选择或结论的对应区域尚未核实。")
