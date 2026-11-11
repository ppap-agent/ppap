"""U17 source-backed UAES checks owned by this immutable directory leaf.

The business implementation is local. No checker-version fallback is used.
"""

from __future__ import annotations

import re

from audit.checker_lib.source import MissingFact, SourceAudit, UnknownFact
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return WarrantAudit(audit_file, rule, group_context, resource=RESOURCE)


class WarrantAudit(SourceAudit):
    def selection(self, aliases, *, exact=True):
        facts = self.native()
        if facts.has_unread_content():
            raise UnknownFact("选项区域仍有图形内容，原生文字不能证明实际勾选。")
        value = facts.field(aliases)
        count = len(re.findall("[☑✓√✔■]", value))
        if count == 1 or (count > 1 and (not exact)):
            return value
        if count > 1:
            return None
        if re.search("[□☐]", value):
            raise MissingFact("原件的该选项区域只有未勾选框，没有实际选择。")
        raise UnknownFact("尚不能确认实际勾选；空框及选项文字不能代替勾选。")

    def rule_1(self):
        facts = self.native()
        for names in [("零件名称",), ("客户零件号",), ("图纸号",), ("供应商零件号",), ("工程更改级别",), ("其他工程更改",)]:
            facts.field(names)
        fields = {"engineering_dates": "工程更改级别和其他工程更改各自对应的日期均有实际非空内容，不能借用另一处日期。", "safety_one": "安全及政府条例区域恰好有一个实际勾选选项。"}
        return self.visual_result(fields, "两个工程日期与安全法规唯一选择")

    def rule_2(self):
        facts = self.native()
        facts.require_text("供方制造厂资料")
        facts.require_text("买方资料")
        for names in [("公司名称",), ("采购方及部门",), ("采购方代码",), ("应用",), ("以IMDS或者其它客户格式递交",)]:
            facts.field(names)
        try:
            selected = self.selection(("是否报告了受客户关注的材料信息",))
        except UnknownFact:
            return self.visual_result({"material_exactly_one": "是否报告了受客户关注的材料信息区域恰好有一个实际勾选选项。"}, "材料信息的唯一实际选择")
        if selected is None:
            return ("NG", "材料信息申报选中了多个选项。")
        return ("PASS", "供方、买方、材料信息唯一选择与IMDS对应内容已读取。")

    def rule_3(self):
        self.native().require_text("供方代表签字")
        return self.visual_result({"handwriting": "供方代表签字字段有实际手写签字，不能是打印姓名或空栏。", "seal": "这份零件提交保证书存在实际印章。"}, "供方代表实际手写签字和印章")

    def rule_4(self):
        try:
            value = self.selection(("提交原因",), exact=False)
            marks = "☑✓√✔■"
            selected_other = bool(re.search(rf"[{marks}]\s*(?:其它|其他)|(?:其它|其他)\s*[{marks}]", value))
            unselected_other = bool(re.search(r"[□☐]\s*(?:其它|其他)|(?:其它|其他)\s*[□☐]", value))
            if selected_other and unselected_other:
                raise UnknownFact("其它选项的前后标记可能属于相邻选项，需查看原图。")
            if selected_other:
                text = re.split(r"(?:其它|其他).*?说明", value, maxsplit=1)
                explanation = re.split(r"[□☐☑✓√✔■]", text[1], maxsplit=1)[0].strip("-—_:： ") if len(text) == 2 else ""
                if not explanation:
                    return ("NG", "提交原因选中其它，但没有对应说明。")
            elif re.search("其它|其他", value) and not unselected_other:
                raise UnknownFact("提交原因的其它选项位置尚不能与实际勾选唯一对应，需核对原件。")
            return ("PASS", "提交原因已有实际选择，选择其它时对应说明已读取。")
        except UnknownFact:
            values = self.visual_pdf(
                {
                    "reason_selected": "提交原因区域至少有一个实际勾选的原因选项。",
                    "other_selected": "提交原因的其它或其他选项本身被实际勾选，不能把相邻选项的勾选借用到此处。",
                    "other_explained": "提交原因中其它或其他选项的说明栏有实际非空说明，不能把相邻选项文字当作说明。",
                }
            )
            if values["reason_selected"] is False and values["other_selected"] is True:
                raise UnknownFact("提交原因的视觉事实互相冲突，需核对原件。")
            if values["reason_selected"] is False:
                return ("NG", "提交原因没有实际选择。")
            if values["other_selected"] is True and values["other_explained"] is False:
                return ("NG", "提交原因选中其它，但没有对应说明。")
            if values["reason_selected"] is True and (values["other_selected"] is False or values["other_selected"] is True and values["other_explained"] is True):
                return ("PASS", "提交原因的实际选择及其它选项所需说明已从原图核实。")
            raise UnknownFact("提交原因的实际勾选和其它说明尚未全部核实，需核对原件。")

    def rule_5(self):
        try:
            selected = self.selection(("要求提交的等级",))
        except UnknownFact:
            return self.visual_result({"level_exactly_one": "要求提交的等级区域恰好有一个实际勾选等级，空框和选项文字不能代替勾选。"}, "提交等级的唯一实际选择")
        return ("NG", "要求提交的等级选中了多个选项。") if selected is None else ("PASS", "要求提交的等级恰好有一个实际勾选。")
