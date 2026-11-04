"""Actual two/three-original PFMEA preparation predicates from source R19–R22."""

from __future__ import annotations

import re

from audit.document_tools import effective
from audit.kinds import PpapDocumentType
from audit.precheck_facts import DefectFact, UnknownFact
from audit.precheck_pairs import history, identity, merge_sources, pair, peer, rows, table

_PROCESS = {"number": ("工序号", "工序编号", "过程编号", "零件/工序号", "零件/过程编号"), "name": ("工序名称", "过程名称", "操作描述", "工序名称/操作描述")}
_CHARACTERISTIC = {"name": ("特性名称", "产品特性", "过程特性", "产品/过程特性", "重要特性名称"), "mark": ("特性类别", "特性分类", "特殊特性", "特性类型", "重要特性")}


def _symbol(value):
    value = str(value or "").strip().strip("[]【】").upper()
    return {"G": "CC", "Z": "SC"}.get(value, value)


def cross_operations(facts, customer, group):
    def characteristics(is_pgqp=False):
        other = peer(group, "采购件质量计划", "采购件质量计划（PGQP1）", "PGQP1") if is_pgqp else peer(group, PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST)
        located = table(other, _CHARACTERISTIC, required=("name", "mark"))
        original, header, _ = located
        pair(facts, facts.table, facts.header, other, original, header, customer)
        if is_pgqp:
            title_matches = [str(candidate["id"]) for candidate in other.data["tables"] if "pgqp1" in str(candidate["id"]).casefold()]
            title_matches += [str(effective(original, row, column).get("value") or "") for row in range(1, header) for column in range(1, original["cols"] + 1)]
            if not any(
                (identity(value) == "pgqp1" or "pgqp1" in identity(value) and "采购件质量计划" in value) and not any(word in value for word in ("参考", "参照", "不是", "非PGQP", "不适用"))
                for value in title_matches
            ):
                raise UnknownFact("关联原件未确认PGQP1标题身份；未用正文引用或文件名代替标题。")
        expected = rows(other, located, trigger=("name", "mark"))
        fields = [name for name in ("characteristic", "product_characteristic", "process_characteristic", "function") if name in facts.columns]
        if not fields:
            raise UnknownFact("PFMEA功能/产品/过程特性列未定位，尚不能执行特性覆盖。")
        targets = facts.values(*fields, *(["special"] if not is_pgqp else []))
        by_name: dict[str, list[dict]] = {}
        for target in targets:
            for field in fields:
                for name in re.split(r"[\n;；]+", str(target[field] or "")):
                    if identity(name):
                        by_name.setdefault(identity(name), []).append(target)
        checked = 0
        for item in expected:
            symbol = _symbol(item["mark"])
            if symbol in {"", "NA", "N/A", "无"}:
                continue
            if symbol not in {"CC", "SC", "YC", "YS", "★", "◆", "重要", "关键"}:
                raise UnknownFact("关联原件的重要特性标记不在已确认口径内，须人工核对。")
            checked += 1
            candidates = by_name.get(identity(item["name"]), [])
            merge_sources(facts, other)
            if not candidates:
                raise UnknownFact("已检查关联特性，但未找到唯一同名PFMEA条目；须确认是否同义或确为遗漏：" + str(item["name"]))
            if not is_pgqp:
                actual_marks = {_symbol(candidate["special"]) for candidate in candidates}
                if len(actual_marks) != 1:
                    raise UnknownFact("PFMEA同名特性存在多种标记，尚未唯一确定清单对端，未择一通过。")
                if actual_marks != {symbol}:
                    raise DefectFact("B06", "同零件同名特性的清单与PFMEA标记不一致：" + str(item["name"]))
        if not checked:
            raise UnknownFact("关联原件没有已确认的重要特性；未按空集合通过。")
        merge_sources(facts, other)
        return "已逐项核实同零件原件的" + str(checked) + "项重要特性覆盖" + ("及标记一致性。" if not is_pgqp else "。")

    def trio():
        pfd = peer(group, PpapDocumentType.PROCESS_FLOW_CHART, required=customer != "UAES")
        cp = peer(group, PpapDocumentType.CONTROL_PLAN, required=False)
        pfd_table = table(pfd, _PROCESS, required=("number", "name"))
        cp_table = table(cp, _PROCESS, required=("number", "name"))
        for other, located in [(pfd, pfd_table), (cp, cp_table)]:
            pair(facts, facts.table, facts.header, other, located[0], located[1], customer, numeric_zeros=True)
        return pfd, pfd_table, cp, cp_table

    def r21():
        pfd, pfd_table, cp, cp_table = trio()
        names = [facts.label(facts.table, ("零件名称", "物料名称"), facts.header)]
        for other, located in [(pfd, pfd_table), (cp, cp_table)]:
            names.append(other.label(located[0], ("零件名称", "物料名称"), located[1]))
        target = facts.values("step")
        target_numbers = set()
        for row in target:
            match = re.fullmatch(r"((?:OP)?\d+(?:[.-]\d+)*)(?:\s+.+)?", str(row["step"] or "").strip(), re.I)
            if not match:
                raise UnknownFact("PFMEA过程步骤没有可唯一抽取的工序编号，不按名称或行数代替编号集合。")
            target_numbers.add(identity(match[1]))
        sets = [target_numbers]
        for other, located in [(pfd, pfd_table), (cp, cp_table)]:
            data = rows(other, located, trigger=("number", "name"))
            if any(not identity(row["number"]) for row in data):
                raise UnknownFact("关联有效工序行编号未填写，尚不能比较编号集合。")
            sets.append({identity(row["number"]) for row in data})
        merge_sources(facts, pfd, cp)
        if not all(identity(name) for name in names):
            raise UnknownFact("三份原件的零件名称尚未全部填写。")
        if len({identity(name) for name in names}) != 1:
            raise DefectFact("B06", "同零件三份原件的零件名称不一致。")
        if not sets[0] == sets[1] == sets[2]:
            raise DefectFact("B06", "同零件PFD/PFMEA/CP工序编号集合不一致；未仅比较数量。")
        return "已核实三份独立原件的零件身份、名称与完整工序号集合。"

    def r22():
        pfd, _, cp, _ = trio()
        dates = []
        for reader in (pfd, facts, cp):
            _, _, values = history(reader)
            dates.append(values[-1])
        merge_sources(facts, pfd, cp)
        if not dates[0] < dates[1] < dates[2]:
            raise DefectFact("B06", "三份履历末行日期未按PFD < PFMEA < CP严格递增；未使用文件修改时间。")
        return "已按三份原件各自履历末行日期核实严格先后顺序。"

    result = {"R21": r21, "R22": r22}
    if customer == "UAES":
        result.update(R19=lambda: characteristics(False), R20=lambda: characteristics(True))
    return result
