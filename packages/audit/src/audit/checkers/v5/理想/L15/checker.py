"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from datetime import datetime, time

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from audit.engine.checkers.date_utils import normalize_excel_date
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        names = ("车型", "供应商名称", "零件编号", "零件名称")
        for name in names:
            if key(self.native().field((name,))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "项目问题清单四个身份字段均有实际内容。")

    def rule_2(self):
        labels = ("过程审核问题是否完全关闭？", "检验体系审核问题是否完全关闭？", "供应商追溯体系评审问题是否完全关闭？", "其他专项问题")
        values = [self.native().field((name,)) for name in labels]
        if any((value == "No" for value in values)):
            rows = self.native().rows({"description": ("问题描述",)})
            if not any((not blank(row["description"]) for row in rows)):
                return ("NG", "存在未关闭问题，但没有实际问题描述。")
        return ("PASS", "四个关闭状态及条件触发后的实际问题描述已读取。")

    def rule_3(self):
        aliases = {"description": ("问题描述",), "action": ("对应措施",), "date": ("时间节点",), "owner": ("负责人",)}
        rows = self.native().rows(aliases, anchor=("description",))
        for row in rows:
            if any((blank(row[name]) for name in ("action", "date", "owner"))):
                return ("NG", "问题行的对应措施、时间节点或负责人为空。")
            raw = row["date"]
            value = normalize_excel_date(raw)
            if value is None:
                raise UnknownFact("问题时间节点无法可靠解析。")
            deadline = datetime.fromisoformat(raw) if isinstance(raw, str) and "T" in raw else datetime.combine(value, time.min)
            if deadline <= datetime.now(deadline.tzinfo):
                return ("NG", "问题时间节点必须晚于当前时间。")
        return ("PASS", "所有问题整改要素完整，且期限晚于当前时间。")

    def rule_4(self):
        risk = "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险"
        for row in self.native().rows({"description": ("问题描述",), "risk": (risk,)}, anchor=("description",)):
            if row["risk"] not in {"Yes", "No"}:
                return ("NG", "问题风险分类必须严格为Yes或No。")
        return ("PASS", "所有问题风险分类均为Yes或No。")
