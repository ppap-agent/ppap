"""UAES PSW fields, real selections and handwritten sign-off."""

import re

from audit.checkers.v3.excel_agent import rule

from .native import UaesSourceAgent, UnknownFact


class UaesWarrantAgent(UaesSourceAgent):
    def selection(self, aliases, *, exact=True):
        value = self.native().field(aliases)
        count = len(re.findall(r"[☑✓√✔■]", value))
        if count == 1 or count > 1 and not exact:
            return value
        if count > 1:
            return None
        raise UnknownFact("尚不能确认实际勾选；空框及选项文字不能代替勾选。")

    @rule("PSW基础身份两个工程日期及法规唯一选择")
    def rule_1(self):
        facts = self.native()
        for names in [("零件名称",), ("客户零件号",), ("图纸号",), ("供应商零件号",), ("工程更改级别",), ("其他工程更改",)]:
            facts.field(names)
        # Repeated generic date labels must remain separately scoped. The
        # PDF fact pass identifies each date next to its engineering change.
        fields = {"engineering_dates": "工程更改级别和其他工程更改各自对应的日期均有实际非空内容，不能借用另一处日期。", "safety_one": "安全及政府条例区域恰好有一个实际勾选选项。"}
        return self.visual_result(fields, "两个工程日期与安全法规唯一选择")

    @rule("供方和买方资料材料申报及IMDS")
    def rule_2(self):
        facts = self.native()
        facts.require_text("供方制造厂资料")
        facts.require_text("买方资料")
        for names in [("公司名称",), ("采购方及部门",), ("采购方代码",), ("应用",), ("以IMDS或者其它客户格式递交",)]:
            facts.field(names)
        if self.selection(("是否报告了受客户关注的材料信息",)) is None:
            return "NG", "材料信息申报选中了多个选项。"
        return "PASS", "供方、买方、材料信息唯一选择与IMDS对应内容已读取。"

    @rule("供方代表手写签字和真实印章")
    def rule_3(self):
        self.native().require_text("供方代表签字")
        return self.visual_result({"handwriting": "供方代表签字字段有实际手写签字，不能是打印姓名或空栏。", "seal": "这份零件提交保证书存在实际印章。"}, "供方代表实际手写签字和印章")

    @rule("提交原因选择与其它说明")
    def rule_4(self):
        value = self.selection(("提交原因",), exact=False)
        if re.search(r"[☑✓√✔■](?:其它|其他)", value):
            text = re.split(r"(?:其它|其他).*?说明", value, maxsplit=1)
            if len(text) < 2 or not text[1].strip("-—_:： □☐☑✓√✔■"):
                return "NG", "提交原因选中其它，但没有对应说明。"
        return "PASS", "提交原因已有实际选择，选择其它时对应说明已读取。"

    @rule("提交等级唯一实际选择")
    def rule_5(self):
        return ("NG", "要求提交的等级选中了多个选项。") if self.selection(("要求提交的等级",)) is None else ("PASS", "要求提交的等级恰好有一个实际勾选。")
