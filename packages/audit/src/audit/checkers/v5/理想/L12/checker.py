"""Source-owned Li Auto business clauses; unknown evidence cannot pass."""

from __future__ import annotations

from audit.checker_lib.excel import column_letter
from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header, key
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def _business_rows(self, facts, table, start):
        """Physical row membership is separate from either clause's fields."""
        reader = facts.readers[table["id"]]
        columns = range(1, table["cols"] + 1)
        headers = [{column: reader.cell(row, column) for column in columns} for row in (start - 1, start) if row > 0]
        found = False
        for row in range(start + 1, table["rows"] + 1):
            raw = {column: reader.cell(row, column) for column in columns}
            known = all(item.get("state") == "known" for item in raw.values())
            if known and all(blank(item.get("value")) for item in raw.values()):
                continue
            if known and any(all(reference[column].get("state") == "known" and item.get("value") == reference[column].get("value") for column, item in raw.items()) for reference in headers):
                continue
            present = next((column for column, item in raw.items() if item.get("state") == "known" and not blank(item.get("value"))), None)
            if present is None:
                for column, item in raw.items():
                    if item.get("state") != "known":
                        facts.cell(table, row, column)
                raise UnknownFact("无法确认此行是否有业务内容，不能按空行忽略。")
            facts.cell(table, row, present)
            found = True
            yield row
        if not found:
            raise UnknownFact("没有可核对的CCSC业务行，不能按空范围通过。")

    def rule_1(self):
        names = ("零件名称", "零件号", "供应商名称", "供应商编码")
        for name in names:
            if key(self.native().field((name,), title=("CCSC", "特性管理清单"))) in {key(label) for label in names}:
                raise UnknownFact("字段只读到相邻标签，尚不能证明有实际填写内容。")
        return ("PASS", "CCSC四项身份字段均有实际原件内容。")

    def rule_2(self):
        facts = self.native()
        title = ("CCSC", "特性管理清单")
        table = facts.table(title)
        names = ("过程/流程步骤", "部件(来料检时涉及)", "客户要求/功能要求", "特性名称", "检验岗位", "产品/过程特性分类", "名义值/规格中心值", "规格下限", "规格上限")
        aliases = {name: (name,) for name in names}
        start, columns = facts.columns(table, aliases)
        if start < 2:
            raise UnknownFact("T1父分组与叶列表头的上下归属尚未明确。")
        groups = [span for span in table.get("merges", []) if span[0] == span[2] == start - 1 and header(facts.cell(table, span[0], span[1]), ("T1-特性监控方法",))]
        if not groups:
            for column in range(1, table["cols"] + 1):
                facts.cell(table, start - 1, column)
            return "NG", "CCSC表头缺少可确认的T1-特性监控方法父分组。"
        if len(groups) != 1 or groups[0][3] - groups[0][1] != 7:
            raise UnknownFact("T1父分组不是唯一的八列水平范围，不能借用T2或其他组。")
        group = groups[0]
        facts.cell(table, group[0], group[1])
        children = ("测量设备", "测量方式", "防错控制", "检查控制", "抽样频率", "SPC监控", "SPC线上管理计划时间", "说明（关键要求/操作参数等）")
        leaves = {column: facts.cell(table, start, column) for column in range(group[1], group[3] + 1)}
        for name in children:
            matches = [column for column, value in leaves.items() if header(value, (name,))]
            if not matches:
                return "NG", "T1父分组内缺少列：" + name
            if len(matches) != 1:
                raise UnknownFact("T1父分组内有重复叶列，不能确定逐行归属。")
            columns[name] = matches[0]
        for row in self._business_rows(facts, table, start):
            for name, column in columns.items():
                if any(span[0] <= row <= span[2] and span[1] <= column <= span[3] for span in table.get("merges", [])):
                    raise UnknownFact("CCSC业务值存在合并，不能确认逐过程的必填范围。")
                if blank(facts.cell(table, row, column)):
                    return "NG", f"CCSC过程行缺少{name}：{column_letter(column)}{row}"
        return "PASS", "全部已命名过程的基础字段及唯一T1分组八项内容均已填写；未借用T2内容。"

    def rule_3(self):
        facts = self.native()
        title = ("CCSC", "特性管理清单")
        table = facts.table(title)
        aliases = {"risk": ("现有管理方式风险评估",), "action": ("高风险管理项预防措施",)}
        start, columns = facts.columns(table, aliases)
        for row in self._business_rows(facts, table, start):
            risk = facts.cell(table, row, columns["risk"])
            if risk not in ("低风险", "中风险", "高风险"):
                raise UnknownFact("风险等级尚未确认为低风险、中风险或高风险，不能判定预防措施条件不适用。")
            if risk == "高风险" and blank(facts.cell(table, row, columns["action"])):
                return ("NG", "高风险管理项缺少对应预防措施。")
        return ("PASS", "全部风险物理行已读取，高风险行均有预防措施。")
