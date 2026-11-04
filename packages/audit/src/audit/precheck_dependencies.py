"""00-A v1.7 §4.1: availability of this Round's original counterparts only."""

from __future__ import annotations

from audit.document_tools import effective
from audit.kinds import PpapDocumentType as T
from audit.precheck_facts import SourceFacts, StateFact, UnknownFact, key
from audit.precheck_pairs import identity, merge_sources, peer

# These are the source's dependency rows, not additional engineering checks.
# (stable name, accepted context types, original title aliases, customer-owned)
_PFD = ("pfd", (T.PROCESS_FLOW_CHART,), ("过程流程图", "PFD"), False)
_PFMEA = ("pfmea", (T.PFMEA,), ("PFMEA", "潜在过程失效模式"), False)
_CP = ("control-plan", (T.CONTROL_PLAN,), ("控制计划",), False)
_SCL = ("characteristics", (T.PROCESS_SPECIAL_CHARACTERISTIC_LIST,), ("特殊特性清单",), False)
_PGQP = ("pgqp1", ("采购件质量计划", "采购件质量计划（PGQP1）", "PGQP1"), ("采购件质量计划", "PGQP1"), False)
_DRAWING = ("drawing", ("图纸", "客户图纸", "Drawing"), ("图纸", "Drawing"), True)
_DEPENDENCIES = {
    "U08": (_DRAWING, _PFD, _PFMEA, _PGQP),
    "U07": (_SCL, _PGQP),
    "U05": (_PGQP, _CP, ("kappa", ("Kappa", "Kappa分析记录"), ("Kappa",), False)),
    "U06": (_PGQP,),
    "U04": (_DRAWING,),
    "U01": (("bubble", ("气泡图", "Bubble drawing"), ("气泡图", "Bubble"), False),),
    "U19": (("deviation", ("偏差认可汇总清单",), ("偏差认可汇总",), False),),
    "U03": (("dvr", (T.DVP_R, "设计验证报告", "DVR"), ("DVR", "设计验证报告", "试验报告"), False),),
    "L07": (_PFD,),
    "L17": (("delivery-list", ("生产件批准交付物文件清单",), ("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单"), False),),
    "N03": (_PFD, _SCL, _PFMEA),
    "N02": (_PFD,),
    "N05": (_SCL,),
}
_CUSTOMERS = {"U": ("UAES", {"uaes", "联电", "联合电子", "联合汽车电子", "联合汽车电子有限公司"}), "L": ("理想", {"理想", "理想汽车", "liauto", "li"}), "N": ("蔚来", {"蔚来", "蔚来汽车", "nio"})}


def _field(reader, aliases, *, required=True):
    values = []
    wanted = {key(alias) for alias in aliases}
    for table in reader.data.get("tables", []):
        if any(key(effective(table, row, column).get("value")) in wanted for row in range(1, table["rows"] + 1) for column in range(1, table["cols"] + 1)):
            values.append(reader.label(table, aliases, table["rows"] + 1))
    if not values:
        if required:
            raise UnknownFact("关联身份字段尚未定位：" + aliases[0] + "。")
        return None
    if len({identity(value) for value in values}) != 1 or identity(values[0]) in {"", "/", "na", "n/a", "tbd", "xxx", "无", "待填", "待定"}:
        raise UnknownFact("关联身份字段为空或有多个冲突值：" + aliases[0] + "。")
    return values[0]


def _has_title(reader, aliases):
    def matches(value):
        return any(key(alias) in key(value) for alias in aliases) and not any(word in key(value) for word in ("参考", "参照", "不是", "非", "不适用", "依据", "请参阅", "填写说明", "填写指南"))

    for table in reader.data.get("tables", []):
        if matches(table["id"]):
            return True
        for row in range(1, min(5, table["rows"]) + 1):
            for column in range(1, table["cols"] + 1):
                observed = effective(table, row, column)
                if observed["state"] == "known" and matches(observed.get("value")):
                    reader.read(table, row, column)
                    return True
    # DOCX/PDF titles need their native location, not synthetic spreadsheet cells.
    for paragraph in reader.data.get("paragraphs", [])[:3]:
        if matches(paragraph.get("text", "")):
            return True
    for page in reader.data.get("pages", [])[:1]:
        if any(matches(block.get("text", "")) for block in page.get("blocks", [])[:3]):
            return True
    return False


def dependency_facts(audit, entry, group):
    """Keep missing SUB/CUS, unknown pairing and unreadable originals distinct."""
    dependencies = _DEPENDENCIES.get(entry, ())
    if not dependencies:
        return []
    try:
        target = SourceFacts(audit)
    except Exception:
        from audit.precheck_facts import state_fact

        return [state_fact(entry + ":dependency:" + name, None, "本轮主件无法读取，关联到位尚未核实。", status="error") for name, *_ in dependencies]
    cache = {audit.audit_id: target}
    results = []
    for name, kinds, titles, customer_owned in dependencies:

        def check(name=name, kinds=kinds, titles=titles, customer_owned=customer_owned):
            if group is None:
                raise UnknownFact("未提供本轮冻结集合，尚不能判定对端是否缺失。")
            if name == "kappa":
                declared = _field(target, ("量具类型", "测量系统类型", "MSA类型", "评价方法"), required=False)
                if declared is None:
                    raise UnknownFact("MSA尚未声明计数/属性或计量类型，未预设必须提交Kappa。")
                if not any(term in key(declared) for term in ("计数", "属性", "kappa", "目视", "人工")):
                    if "计量" in key(declared):
                        raise StateFact("S02", "本件明确为计量型MSA，Kappa对端不适用。")
                    raise UnknownFact("MSA类型尚不能可靠归一，Kappa适用性待确认。")
            candidates = {item.audit_id: item for kind in kinds for item in group.get_files_by_type(kind) if item.audit_id != audit.audit_id}
            all_originals = {item.audit_id: item for item in group.by_audit_id.values()}
            all_originals.update({item.audit_id: item for items in group.by_doc_type.values() for item in items})
            all_originals[audit.audit_id] = audit
            for version, original in all_originals.items():
                if version in candidates:
                    continue
                reader = cache.setdefault(version, None)
                if reader is None:
                    reader = cache[version] = SourceFacts(original)
                if _has_title(reader, titles):
                    candidates[version] = original
            if not candidates:
                if name == "dvr":
                    raise StateFact("S03", "实验室DVR仅供测试机构一致性提示；未提供时不记缺件。")
                # Existing helper owns missing/unknown/unreadable classifications.
                peer(group, *kinds, customer_owned=customer_owned)
                raise UnknownFact("关联原件标题及范围尚未确认。")
            if len(candidates) != 1:
                raise UnknownFact("本轮有多个关联原件候选，须先确认适用对象，未任取一份。")
            original = next(iter(candidates.values()))
            other = cache.get(original.audit_id)
            if other is None:
                other = cache[original.audit_id] = SourceFacts(original)
            try:
                if name == "dvr":
                    fields = [("实验室名称", "检测机构", "机构名称", "测试机构")]
                else:
                    fields = [("零件号", "零件编号", "物料编号", "客户零件号", "理想汽车零件号", "Part number", "Part No")]
                    if name in {"bubble", "deviation"}:
                        fields.append(("零件名称", "物料名称", "Part name"))
                for aliases in fields:
                    if identity(_field(target, aliases)) != identity(_field(other, aliases)):
                        raise UnknownFact("对端" + aliases[0] + "与本件不一致，不能用于当前对象。")
                if name != "dvr":
                    if other is not target:
                        for aliases in [("版本", "版本号", "修订版次", "工程更改版本", "Revision"), ("项目", "项目名称", "项目号", "UAES项目编号", "车型项目")]:
                            first, second = _field(target, aliases, required=False), _field(other, aliases, required=False)
                            if (first is not None or second is not None) and (first is None or second is None or identity(first) != identity(second)):
                                raise UnknownFact("关联原件项目或版本缺失/冲突，须确认配对。")
                    allowed = _CUSTOMERS[entry[0]][1]
                    for reader in (target, other):
                        customer = _field(reader, ("客户", "客户名称", "Customer", "Customer Name"), required=False)
                        if customer is not None and key(customer) not in allowed:
                            raise UnknownFact("关联原件客户与本次客户模式冲突，须拆分或确认。")
                return "本轮已定位可配对的" + titles[0] + "原件；仅核实到位与身份，业务一致性由对应正式规则判断。"
            finally:
                merge_sources(target, other)

        results.append(target.observe(entry + ":dependency:" + name, check))
    return results
