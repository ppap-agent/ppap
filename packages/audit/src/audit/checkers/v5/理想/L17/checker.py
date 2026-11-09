"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        rows = self.native().rows({"item": ("项目", "文件名称", "交付物"), "attachment": ("附件",)}, title=("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单"))
        checked = 0
        for row in rows:
            if row["item"] == "零件认证相关":
                continue
            if blank(row["item"]):
                raise UnknownFact("交付物清单行主体未明确，不能默默忽略。")
            checked += 1
            if blank(row["attachment"]):
                return ("NG", "非零件认证相关的交付物行附件为空。")
        if not checked:
            raise UnknownFact("没有可核实的适用交付物行。")
        return ("PASS", "全部非零件认证相关的交付物行均有附件内容。")

    def rule_3(self):
        return self.visual_result(
            {
                "company_or_authorized_department": (
                    "零件提交保证书有实际印章，章文以公司结尾；或保证书有部门章，同时部门章授权声明表上有实际公司章。标签、公司标志、红色方块及无授权表的部门章都不满足。"
                )
            },
            "实际公司章或具公司章授权声明的部门章",
        )

    def rule_5(self):
        return self.visual_result(
            {"authorized_signature": "经授权的供方代表签字对应栏存在实际签字，不能用预印标签或打印姓名代替。", "seal": "此零件提交保证书存在实际盖章；盖章标签、公司标志或红色方块不算。"},
            "经授权供方代表实际签字和实际盖章",
            existential_fields=("seal",),
        )

    def rule_6(self):
        names = (
            "零件名称",
            "理想汽车零件号",
            "供方料号",
            "图纸号",
            "模具穴号",
            "工程更改版本",
            "版本日期",
            "重量(kg)",
            "供应商名称/供应商代码",
            "街道地址",
            "城市",
            "省",
            "国家",
            "顾客名称",
            "设计开发",
            "采购工程",
            "PTC",
        )
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "PSW原规则十七个必填字段均有原件内容。")

    def rule_7(self):
        if self.native().field(("其他-请在下方说明",), required=False):
            return ("PASS", "其他-请在下方说明字段已有实际内容。")
        return self.visual_result({"reason_selected": "提交原因区域至少有一个实际选择，预印选项和空框不代表勾选。"}, "提交原因的实际选择")

    def rule_8(self):
        return self.visual_result(
            {
                "approved_one": "认可情况区域恰有一个实际勾选，且该选项为批准。",
                "submitted_all": "提交结果中的尺寸测量、外观标准、材料和功能试验、统计过程数据四项全部实际勾选。",
                "compliant_selected": "这些结果均满足所有图纸和规范要求的区域有实际选项勾选。",
            },
            "唯一批准及全部必要提交结果的实际选择",
        )
