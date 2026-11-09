"""N04 product identifiers and complete characteristic rows from originals."""

from __future__ import annotations

import re

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE

_FIELDS = ("零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数")


def create_audit(audit_file, rule, group_context=None):
    return CharacteristicsAudit(audit_file, rule, group_context, resource=RESOURCE)


class CharacteristicsAudit(SourceAudit):
    def rule_1(self):
        facts = self.native()
        table = facts.table(("特殊特性清单",))
        found = []
        for row in range(1, min(table["rows"], 8) + 1):
            for column in range(1, table["cols"] + 1):
                raw = facts.readers[table["id"]].cell(row, column)
                if raw.get("state") != "known" or "特殊特性清单" not in str(raw.get("value") or ""):
                    continue
                value = str(facts.cell(table, row, column))
                prefix = value.split("特殊特性清单", 1)[0].strip()
                candidate = re.sub(r"^(?:产品编号|零件编号|Product\s*(?:No\.?|Number))\s*[:：]?\s*", "", prefix, flags=re.I).strip()
                if not candidate:
                    return "NG", "特殊特性清单标题前缺少产品编号。"
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. /-]*", candidate) and re.search(r"\d", candidate) and not re.match(r"(?:version|rev|ver)\W*\d", candidate, re.I):
                    found.append(key(candidate))
                else:
                    raise UnknownFact("标题前有文字，但尚不能确认它是产品编号。")
        if len(set(found)) > 1:
            raise UnknownFact("存在多个冲突的标题产品编号，不能选择一个判为通过。")
        if found:
            return "PASS", "原件特殊特性清单标题前有可读取产品编号。"
        if facts.has_unread_content():
            raise UnknownFact("标题区域存在未读取内容，尚不能确认产品编号。")
        return "NG", "原件未找到带产品编号的特殊特性清单标题。"

    def rule_2(self):
        aliases = {name: ((name, "特殊特性类别") if name == "特性类别" else (name,)) for name in _FIELDS}
        rows = self.native().rows(aliases, title=("特殊特性清单",))
        count = 0
        for row in rows:
            if any(key(row[name]).startswith(("备注", "编制/日期")) for name in ("零件编号", "零件名称", "特性描述")):
                continue
            count += 1
            missing = [name for name in _FIELDS if blank(row[name])]
            if missing:
                return "NG", "特性业务行缺少：" + "、".join(missing)
        if not count:
            raise UnknownFact("没有可核实的特殊特性业务行，不能按空表通过。")
        return "PASS", "全部特殊特性业务行的十一项对应内容均已读取。"
