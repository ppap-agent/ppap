"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def has_graphics(self):
        facts = self.native()
        return facts.format == ".xls" or any(table.get("visual_evidence") for table in facts.data.get("tables", [])) or any(page.get("image_count", 0) for page in facts.data.get("pages", []))

    def rule_2(self):
        labels = ("材料执行标准", "材料类型", "材料牌号", "材料规格/尺寸", "材料供应商名称（包括生产地点）", "通过何种质量管理体系认证")
        rows = self.native().rows({"part": ("零件号",), **{name: (name,) for name in labels}}, anchor=("part",))
        for row in rows:
            if any(blank(row[name]) for name in labels):
                return "NG", "已有零件号的材料行有必填材料字段为空。"
        return "PASS", "已逐零件读取六项必填材料内容。"

    def rule_3(self):
        facts = self.native()
        labels = ("自制", "外协-国产", "外协-进口", "领用件")
        rows = facts.rows({"part": ("零件号",), **{name: (name,) for name in labels}})
        if self.has_graphics():
            return self.visual_result({"one_status": "每个有内容的材料业务行，在自制、外协-国产、外协-进口、领用件中恰有一个实际选择。"}, "材料业务行的唯一部件状态")
        for row in rows:
            if blank(row["part"]):
                raise UnknownFact("材料行主体不明确，不能按无业务行忽略。")
            values = [row[name] for name in labels]
            selected = sum(not blank(value) and str(value).strip() not in {"□", "☐"} for value in values)
            if selected != 1:
                return "NG", "材料业务行必须恰有一个部件状态。"
        return "PASS", "各材料业务行恰有一个部件状态。"

    def rule_4(self):
        facts = self.native()
        rows = facts.rows({"part": ("零件号",), "domestic": ("外协-国产",), "imported": ("外协-进口",), "ppap": ("完成PPAP",)}, anchor=("part",))
        if self.has_graphics():
            return self.visual_result({"outsourced_ppap": "每个有零件号且实际选择外协-国产或外协-进口的行，其完成PPAP为大写Y。"}, "外协状态及同行PPAP完成标记")
        for row in rows:
            outsourced = any(not blank(row[name]) and str(row[name]).strip() not in {"□", "☐"} for name in ("domestic", "imported"))
            if outsourced and row["ppap"] != "Y":
                return "NG", "外协零件完成PPAP必须严格为大写Y。"
        return "PASS", "所有已读取外协行的完成PPAP均为Y。"
