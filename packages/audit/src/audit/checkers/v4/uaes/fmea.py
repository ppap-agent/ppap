"""PFMEA scoring facts and explicit customer characteristic reconciliation."""

import re

from audit.checkers.v3.excel_agent import rule

from .native import UaesSourceAgent, UnknownFact, blank, key, number


class UaesPfmeaAgent(UaesSourceAgent):
    @rule("经验教训的实际输入引用")
    def rule_1(self):
        facts = self.native()
        for value, locator in facts.observations():
            for term in ("历史问题", "客户抱怨", "过往失效案例", "类似产品问题"):
                if term not in value or any(word in value for word in ("例如", "示例", "请填写", "填写指南")):
                    continue
                detail = re.sub(r"[\W_]", "", value.split(term, 1)[1])
                if len(detail) >= 4:
                    self.witness(locator, value)
                    return "PASS", "PFMEA存在实际经验教训引用：" + value
        raise UnknownFact("尚不能确认经验教训有实际案例输入；仅有经验教训栏标题不能证明已引用。")

    @rule("逐行SOD整数及后果控制一致性")
    def rule_2(self):
        rows = self.native().rows(
            {
                "s": ("S", "严重度", "严重度S"),
                "o": ("O", "发生度", "频度O"),
                "d": ("D", "探测度", "探测度D"),
                "effect": ("失效后果", "潜在失效后果"),
                "mode": ("失效模式", "潜在失效模式"),
                "cause": ("失效原因", "潜在失效起因"),
                "prevention": ("预防控制", "现行预防控制"),
                "detection": ("探测控制", "现行探测控制"),
            },
            title=("PFMEA", "失效"),
            anchor=("mode", "cause"),
        )
        conditions: dict[tuple[str, ...], tuple] = {}
        for row in rows:
            scores = tuple(number(row[name]) for name in ("s", "o", "d"))
            if any(score != score.to_integral_value() or not 1 <= score <= 10 for score in scores):
                return "NG", "PFMEA的S/O/D存在超出1至10的整数范围的评分。"
            s, _o, d = scores
            effect = str(row["effect"])
            if any(term in effect for term in ("安全", "法规", "整车召回")) and s < 9:
                return "NG", "PFMEA安全/法规/整车召回后果的严重度低于9。"
            if "轻微" in effect and "外观" in effect and not 2 <= s <= 4:
                return "NG", "仅轻微外观后果的严重度不在2至4范围。"
            if key(row["detection"]) in {"无", "无法探测", "无探测措施", "none"} and d < 10:
                return "NG", "无探测措施的失效项探测度小于10。"
            identity = tuple(key(row[name]) for name in ("mode", "cause", "prevention", "detection"))
            if identity in conditions and conditions[identity] != scores:
                return "NG", "同失效模式、原因和控制条件的S/O/D评分不一致。"
            conditions[identity] = scores
        return self.visual_result(
            {"reasonable": "对全部PFMEA行，S与后果匹配，O与起因和预防控制匹配（有效预防O低、无预防O高），D与方法和时机匹配（下线高、在线自动低、无法探测10）。工程有效性未知时null。"},
            "全部S/O/D评分的工程合理性",
        )

    def reconcile(self, related, *, marked):
        aliases = {"name": ("特性名称", "产品特性", "过程特性", "产品/过程特性"), "mark": ("特性类别", "特性分类", "特殊特性", "特性类型")}
        targets = self.native().rows({"name": ("功能/要求", "产品特性", "过程特性", "功能/产品特性", "功能/过程特性"), "mark": ("特殊特性", "分类")}, title=("PFMEA", "失效"), anchor=("name",))
        expected = related.rows(aliases, anchor=("name",))
        count = 0
        for item in expected:
            if blank(item["mark"]):
                continue
            count += 1
            matches = [target for target in targets if key(target["name"]) == key(item["name"])]
            if len(matches) != 1:
                raise UnknownFact("PFMEA尚不能唯一对应关联特性：" + str(item["name"]))
            if marked and key(matches[0]["mark"]) != key(item["mark"]):
                return "NG", "关联特殊特性在PFMEA中的标识不一致。"
        if not count:
            raise UnknownFact("关联原件没有可确认的重要/特殊特性，不能按空范围通过。")
        return "PASS", "已逐项核实同零件关联清单的适用特性及标识。"

    @rule("特殊特性清单全部产品过程特性和一致标识")
    def rule_3(self):
        related = self.related(("特殊特性",), require_name=True)
        return self.reconcile(related, marked=True)

    @rule("PGQP1重要特性覆盖")
    def rule_4(self):
        related = self.related(("采购件质量计划", "PGQP1"))
        related.require_text("PGQP1")
        return self.reconcile(related, marked=False)
