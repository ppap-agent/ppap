"""U15 source-backed UAES checks owned by this immutable directory leaf.

The business implementation is local. No checker-version fallback is used.
"""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return TrialAudit(audit_file, rule, group_context, resource=RESOURCE)


class TrialAudit(SourceAudit):
    def rule_1(self):
        facts = self.native()
        for phrase in ("流转记录", "试装结论", "检验结论", "流程结束", "流程完成"):
            facts.require_text(phrase)
        visual_fields = {"trial_passed": "试装结论恰好选择了通过，未选择未通过；以实际勾选或填写状态为准。", "inspection_passed": "检验结论恰好选择了合格，未选择不合格；以实际勾选或填写状态为准。"}
        if facts.has_unread_content():
            return self.visual_result(visual_fields, "试装通过及检验合格的实际选择")
        try:
            trial = facts.field(("试装结论",))
            inspection = facts.field(("检验结论",))
        except UnknownFact:
            return self.visual_result(visual_fields, "试装通过及检验合格的实际选择")

        def chosen(value, positive, negative):
            # Match whole option tokens, longest first. A suffix mark on
            # “未通过” must not also mark its “通过” substring as selected.
            labels = "|".join(re.escape(label) for label in sorted((positive, negative), key=len, reverse=True))
            marks = "☑✓√✔■"
            pattern = rf"([{marks}□☐])\s*({labels})|({labels})\s*([{marks}□☐])"
            options = [(match[2] or match[3], match[1] or match[4]) for match in re.finditer(pattern, str(value))]
            selected = {label for label, mark in options if mark in marks}
            if negative in selected:
                return False
            if selected == {positive} and sum(mark in marks for _label, mark in options) == 1:
                return True
            if not selected and {label for label, _ in options} == {positive, negative}:
                return False
            return None

        results = [chosen(trial, "通过", "未通过"), chosen(inspection, "合格", "不合格")]
        if False in results:
            return ("NG", "试装或检验结论选中了未通过/不合格。")
        if None in results:
            return self.visual_result(visual_fields, "试装通过及检验合格的实际选择")
        return ("PASS", "已核实流程结束/完成和两类通过结论的实际勾选。")
