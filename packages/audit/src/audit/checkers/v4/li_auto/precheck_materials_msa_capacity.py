"""Fixed Li Auto material, MSA and capability preparation predicates.

The readers are existing native snapshots. Scope facts require the frozen task
or an explicit declaration, and engineering acceptance thresholds stay outside.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.kinds import PpapDocumentType
from audit.precheck_facts import DefectFact, StateFact, UnknownFact, key
from audit.precheck_pairs import identity, merge_sources

_PART = ("零件号", "零件编号", "零件/部件零件号", "零件/分部件零件号")
_MATERIAL = {
    "part": _PART,
    "name": ("零件名称", "零件/部件名称"),
    "serial": ("序号", "NO", "No."),
    "level": ("层级", "级别", "级数"),
    "weight": ("单件重量",),
    "standard": ("材料执行标准", "材料标准"),
    "type": ("材料类型",),
    "grade": ("材料牌号",),
    "size": ("材料规格/尺寸", "材料规格·尺寸"),
    "supplier": ("材料供应商名称", "材料供应商名称（含生产地点）"),
    "component_supplier": ("部件供应商名称", "部件供应商", "零件供应商名称"),
    "cert": ("通过何种质量管理体系认证",),
    "made": ("自制",),
    "domestic": ("外协-国产", "国产"),
    "imported": ("外协-进口", "进口"),
    "taken": ("领用件",),
    "ppap": ("完成PPAP",),
}
_FOOTERS = ("填写说明", "填表说明", "编制：", "审核：", "批准：", "备注：")
_EMPTY = {"", "/", "-", "—", "n/a", "na", "无", "待填", "tbd", "xxx"}
_REGIONS = {
    "北京",
    "上海",
    "天津",
    "重庆",
    "江苏",
    "浙江",
    "广东",
    "山东",
    "安徽",
    "河北",
    "河南",
    "湖北",
    "湖南",
    "四川",
    "福建",
    "江西",
    "陕西",
    "山西",
    "辽宁",
    "吉林",
    "苏州",
    "杭州",
    "宁波",
    "无锡",
    "常州",
    "深圳",
    "广州",
    "南京",
    "合肥",
    "武汉",
    "成都",
}


def _blank(value):
    return str(value if value is not None else "").strip().casefold() in _EMPTY


def _located(facts, aliases, required):
    found = []
    for table in facts.data["tables"]:
        if any(word in str(table["id"]) for word in ("填写说明", "填写指南", "评分准则")):
            continue
        for header in range(1, min(80, table["rows"]) + 1):
            columns = {}
            for name, labels in aliases.items():
                matches = [col for col in range(1, table["cols"] + 1) if key(effective(table, header, col).get("value")) in {key(label) for label in labels}]
                if matches:
                    columns[name] = matches
            if set(required).issubset(columns):
                found.append((table, header, columns))
                break
    if not found:
        raise UnknownFact("原件中未定位到" + "/".join(required) + "的同一业务表头；需核对原件模板，不能猜列。")
    return found


def _rows(facts, located, *, allow_empty=False):
    table, header, columns = located
    result: list[Any] = []
    for values in columns.values():
        for col in values:
            facts.read(table, header, col)
    for row in range(header + 1, table["rows"] + 1):
        raw = [effective(table, row, col) for values in columns.values() for col in values]
        if any(str(cell.get("value") or "").startswith(_FOOTERS) for cell in raw):
            break
        if not any(cell["state"] != "known" or str(cell.get("value") if cell.get("value") is not None else "").strip() for cell in raw):
            continue
        if all(key(effective(table, row, values[0]).get("value")) == key(effective(table, header, values[0]).get("value")) for values in columns.values()):
            raise UnknownFact("同工作表存在重复业务表头，需先确认多个独立切片边界，未把后续表头当数据。")
        record = {name: [facts.read(table, row, col, vertical_only=True) for col in values] for name, values in columns.items()}
        result.append((row, record))
    if not result and not allow_empty:
        raise UnknownFact("业务表头已定位，但未取得任何可核实业务行；不能以空集合通过。")
    return result


def _one(record, field):
    values = record.get(field, [])
    if len(values) != 1:
        raise UnknownFact("业务列" + field + "缺失或同名重复，需先确认语义区块。")
    return values[0]


def _material_slices(facts):
    return _located(facts, _MATERIAL, ("part", "name"))


def _material_records(facts, located):
    result: list[Any] = []
    previous = None
    for row, record in _rows(facts, located):
        part = _one(record, "part")
        split = not str(part if part is not None else "").strip()
        if not split and _blank(part):
            raise DefectFact("B03", "零件号使用占位符，不能作为真实零件或材料拆分续行。")
        if split:
            # Explicit blank leading cells are a continuation only when the
            # previous item exists and this row contains material details alone.
            leading = ("name", "serial", "level", "weight", "made", "domestic", "imported", "taken", "component_supplier", "ppap")
            material = ("standard", "type", "grade", "size", "supplier", "cert")
            if previous is None or any(not _blank(value) for name in leading for value in record.get(name, [])) or not any(not _blank(value) for name in material for value in record.get(name, [])):
                raise DefectFact("B03", "非材料续行的业务行零件号为空，主体无法归属。")
            record = {**record, "part": [previous]}
        else:
            previous = part
        result.append((row, record, split))
    return result


def _material_hierarchy(facts, check):
    for located in _material_slices(facts):
        records = _material_records(facts, located)
        if "level" not in located[2]:
            raise UnknownFact("当前材料表未提供可读的层级列，不能从行序推定父子或叶子节点。")
        levels = []
        for _, record, split in records:
            if split:
                continue
            text = str(_one(record, "level")).strip()
            if not re.fullmatch(r"\d+", text):
                raise UnknownFact("层级不是唯一非负整数，需确认原件层级编码。")
            levels.append((int(text), record))
        check(levels)


def _metadata(facts, located, aliases, *, required=True):
    table, header, _ = located
    if header <= 1:
        if required:
            raise UnknownFact("业务表头上方没有对象信息区，尚不能核实表头声明。")
        return None
    wanted = {key(label) for label in aliases}
    if not any(key(effective(table, row, col).get("value")) in wanted for row in range(1, header) for col in range(1, table["cols"] + 1)):
        if required:
            raise UnknownFact("对象信息区未定位到" + "/".join(aliases) + "，需确认模板字段。")
        return None
    return facts.label(table, aliases, header)


def _scope(facts, field, aliases):
    expected = getattr(facts.audit, field, None)
    if expected in (None, "", "default"):
        raise UnknownFact("冻结任务未提供独立的" + field + "审核范围基准；原件自述不能同时作为独立对照真值。")
    for located in _material_slices(facts):
        actual = _metadata(facts, located, aliases)
        if _blank(actual):
            raise DefectFact("B03", "原件对象信息未填写，无法核对提交范围。")
        if identity(expected) != identity(actual):
            raise DefectFact("B03", "原件对象信息与冻结任务独立声明范围不一致。")


def _group(agent, facts):
    group = getattr(agent, "group_context", None)
    if not group or not group.task_group_id or facts.audit.audit_id not in group.by_audit_id or group.unavailable_files:
        raise UnknownFact("本次冻结文件集合未完整提供或含不可读原件，不能扩展为项目全量资料对账。")
    return group


def _self_list(agent, facts):
    candidates = _located(facts, {"filename": ("文件名", "文件名称", "File name")}, ("filename",))
    declared = {Path(str(_one(row, "filename"))).name.casefold() for located in candidates for _, row in _rows(facts, located)}
    group = _group(agent, facts)
    actual = {str(audit.file_metadata.get("original_filename") or "").casefold() for audit in group.by_audit_id.values()}
    if "" in actual:
        raise UnknownFact("冻结集合缺原始文件名，不能按临时下载路径或猜测文件名对账。")
    if declared != actual:
        raise DefectFact("W04", "自述文件清单与本次冻结实收文件名集合不一致；请核对分批范围及清单。")


def _same_kind(agent, facts, kind):
    from .precheck_l1 import L1Facts

    group = _group(agent, facts)
    candidates = [audit for audit in group.get_files_by_type(kind) if audit.audit_id != facts.audit.audit_id]
    if not candidates:
        for table in facts.data["tables"][:1]:
            facts.read(table, 1, 1)
        raise StateFact("S01", "本次冻结集合无同类其他原件，没有多版本比较对象。")

    def object_key(reader):
        values = [_labels(reader, table, _PART, required=False) for table in reader.data["tables"]]
        keys = {identity(value) for value in values if not _blank(value)}
        if len(keys) != 1:
            raise UnknownFact("同类原件未提供唯一零件主键，不能将不同对象误认为同一项目多版本。")
        return next(iter(keys))

    own_key = object_key(facts)
    others = []
    for audit in candidates:
        reader = L1Facts(audit)
        if object_key(reader) == own_key:
            others.append(audit)
            merge_sources(facts, reader)
    if not others:
        raise StateFact("S01", "本次同类原件属于不同零件，无同对象多版本比较对象。")
    if facts.audit.file_metadata.get("role") == "target" and all(audit.file_metadata.get("role") == "reference" for audit in others):
        return others
    raise DefectFact("W04", "同零件原件有多个候选，本次未以唯一本体与比对资料角色指明有效件；请确认有效版本。")


def _material_ops(agent, facts):
    def master_keys():
        for located in _material_slices(facts):
            for labels in (_PART, ("零件名称",)):
                if _blank(_metadata(facts, located, labels)):
                    raise DefectFact("B03", "表头对象主键有栏无值。")
            supplier = _metadata(facts, located, ("供应商名称", "供应商"), required=False)
            records = _material_records(facts, located)
            if _blank(supplier) and not any(not _blank(value) for _, record, _ in records for value in record.get("component_supplier", []) + record.get("supplier", [])):
                raise DefectFact("B03", "表头及明细部件供应商列均无可用供应商名称。")

    def root_match(field, labels, code):
        for located in _material_slices(facts):
            declared = _metadata(facts, located, labels)
            root = next(record for _, record, split in _material_records(facts, located) if not split)
            if identity(declared) != identity(_one(root, field)):
                raise DefectFact(code, "表头声明与正文根节点" + field + "不一致，需核对编码解释。")

    def levels_check():
        def check(levels):
            if not levels or levels[0][0] not in (0, 1):
                raise DefectFact("B04", "层级区段缺少0或1起始根节点。")
            if any(after[0] > before[0] + 1 for before, after in zip(levels, levels[1:])):
                raise DefectFact("B04", "相邻业务行层级跳级。")

        _material_hierarchy(facts, check)

    def assembly_children():
        def check(levels):
            for index, (level, row) in enumerate(levels):
                name = str(_one(row, "name"))
                made = _one(row, "made")
                if _blank(made) or not any(word in name for word in ("总成", "组件")):
                    continue
                if any(word in name for word in ("标准电子黑盒", "标准紧固件")):
                    continue
                if index + 1 == len(levels) or levels[index + 1][0] <= level:
                    if not any(word in name for word in ("支架", "壳体", "机械", "钣金", "焊接总成")):
                        raise UnknownFact("自制总成无下级，但名称未证明其不属于标准电子黑盒/紧固件豁免，需受控分类依据。")
                    raise DefectFact("B04", "明确机械自制总成/组件无下级，未见允许的标准电子黑盒或紧固件类别。")

        _material_hierarchy(facts, check)

    def columns():
        for located in _material_slices(facts):
            table, header, names = located
            for col in names["part"]:
                facts.read(table, header, col)
            missing = set(("standard", "type", "grade", "size", "supplier", "cert", "made", "domestic", "imported", "taken", "ppap")) - names.keys()
            if missing:
                raise DefectFact("B05", "理想材料模板缺少自身应有列：" + "/".join(sorted(missing)))

    def filled():
        for located in _material_slices(facts):
            records = _rows(facts, located, allow_empty=True)
            if not records:
                raise DefectFact("B02", "材料表只有表头，没有有效填报行。")
            for _, row in records:
                if any(str(value).strip().casefold() in {"示例", "example", "demo", "待填", "xxx", "样例"} for value in row.get("part", []) + row.get("name", [])):
                    raise DefectFact("B02", "材料对象仍为模板示例或待填写内容。")

    def row_keys():
        for located in _material_slices(facts):
            _material_records(facts, located)

    def leaf_weights():
        def check(levels):
            for index, (level, row) in enumerate(levels):
                if index + 1 == len(levels) or levels[index + 1][0] <= level:
                    if _blank(_one(row, "weight")):
                        raise DefectFact("W02", "叶子节点单件重量缺失。")

        _material_hierarchy(facts, check)

    def suppliers():
        for located in _material_slices(facts):
            for _, row, _ in _material_records(facts, located):
                for field in ("supplier", "component_supplier"):
                    for value in row.get(field, []):
                        text = str(value or "").strip()
                        if _blank(value) or text.removesuffix("市").removesuffix("省") in _REGIONS:
                            raise DefectFact("W01", "供应商名称为空或仅为明确地名，需填写完整机构名称。")
        raise UnknownFact("已核对空值及明确行政地名；其他短名称须依据受控机构名录确认，不能把2–4字企业简称一概判错。")

    def same_part():
        by_part: dict[Any, Any] = {}
        for located in _material_slices(facts):
            for _, row, split in _material_records(facts, located):
                if split:
                    raise UnknownFact("存在同零件多材续行，须先确认材料组合等价口径，不能逐行误判材料冲突。")
                value = tuple(tuple(identity(value) for value in row.get(name, [])) for name in ("grade", "supplier", "made", "domestic", "imported", "taken"))
                part = identity(_one(row, "part"))
                if part in by_part and by_part[part] != value:
                    raise DefectFact("W04", "不同切片同零件的材料、供应商或实现方式冲突。")
                by_part[part] = value

    def weight_scale():
        groups: dict[tuple[str, str, str], list[Decimal]] = {}
        for located in _material_slices(facts):
            # Slices and exact part names are not mixed across assemblies.
            for _, row, split in _material_records(facts, located):
                if split:
                    continue
                level, name = _one(row, "level"), _one(row, "name")
                weight = facts.strict_number(_one(row, "weight"))
                if weight <= 0:
                    raise UnknownFact("非正重量不能用于kg/g量级比，需先确认重量输入。")
                groups.setdefault((located[0]["id"], identity(level), identity(name)), []).append(weight)
        # The source gives no numeric threshold for '跨度过大'. Exact 1000
        # ratio is a unit-mix suspicion, never an engineering NG.
        if any(len(values) > 1 and max(values) / min(values) == 1000 for values in groups.values()):
            raise DefectFact("W06", "同切片、同层级、同名零件重量相差恰好1000倍，疑kg/g混用，需核对单位。")
        raise UnknownFact("已核对同切片同层级同名件的1000倍单位反例；其余同类件分组及异常跨度没有受控阈值，未自行设阈值。")

    def fill_rate():
        for located in _located(facts, _MATERIAL, ("part", "standard")):
            records = _material_records(facts, located)
            for field in ("standard", "type", "grade", "size", "supplier", "cert"):
                columns = located[2].get(field)
                if not columns:
                    raise UnknownFact("材料完整率所需列未定位：" + field + "；缺列另由PC-B8核对。")
                for index in range(len(columns)):
                    if sum(not str(record[field][index] if record[field][index] is not None else "").strip() for _, record, _ in records) * 10 >= len(records) * 9:
                        raise DefectFact("W01", "材料列" + field + "（独立第" + str(index + 1) + "列）至少90%业务行为空。")

    def boundary():
        for located in _material_slices(facts):
            for _, row in _rows(facts, located):
                for field in ("part", "name", "serial"):
                    if any(re.search(r"填写说明|填表说明|图例|第\s*\d+\s*页", str(value)) for value in row.get(field, [])):
                        raise DefectFact("B04", "材料业务数据区混入页码、填写说明或图例，边界不清。")

    def name_content():
        filename = str(facts.audit.file_metadata.get("original_filename") or "")
        if not filename:
            raise UnknownFact("冻结输入未提供原始文件名，不能以下载临时名核对文件名语义。")
        explicit = re.findall(r"(?:车型|项目|版本)[=：:]([^_\s.]+)", filename)
        if not explicit:
            raise StateFact("S02", "原始文件名未声明可唯一分割的车型/项目/版本标签，不把任意文件名片段当作对象声明。")
        for located in _material_slices(facts):
            values = [_metadata(facts, located, labels, required=False) for labels in (("车型", "适用车型"), ("项目", "项目名称"), ("版本", "版本号"))]
            if any(identity(value) not in {identity(item) for item in values if item is not None} for value in explicit):
                raise DefectFact("W04", "文件名显式声明的车型/项目/版本与原件表头不对应。")

    def placeholder_consistency():
        for located in _material_slices(facts):
            by_part: dict[Any, Any] = {}
            for _, record, split in _material_records(facts, located):
                if split:
                    continue
                part = identity(_one(record, "part"))
                for name in ("standard", "type", "grade", "size", "supplier", "cert"):
                    for index, value in enumerate(record.get(name, [])):
                        token = str(value or "").strip().casefold()
                        if token not in _EMPTY | {"√", "✓"}:
                            continue
                        observed = by_part.setdefault((part, name, index), set())
                        observed.add(token)
                        if len(observed) > 1:
                            raise DefectFact("W03", "同一零件同一材料字段重复记录使用不同空白/占位/勾选写法。")
        raise UnknownFact("已核对同对象同列占位写法；跨零件的不同占位可能有适用性差异，需按受控白名单核对。")

    def readable():
        for located in _material_slices(facts):
            _rows(facts, located)
        raise UnknownFact("已读取原生材料表业务单元格；原件中的图像、裁切与乱码视觉质量仍需原图核对。")

    def generation():
        columns()
        raise UnknownFact("已核对理想自身列组合；原件未提供受控模板版本签名，不能仅凭选择理想推定V1.0/V1.5/V2.0/V3.0。")

    def external():
        text = _native_text(facts)
        references = [token for value in text for token in re.findall(r"(?:随包|附件|附送|随附)[：:]\s*(PSW|控制计划|证书)", value, re.I)]
        if not references:
            raise StateFact("S02", "原件未声明随包PSW、控制计划或证书，本项无明确对账对象；理想无图纸依赖。")
        group = _group(agent, facts)
        for token in references:
            if token == "证书":
                raise UnknownFact("原件声明随包证书，但未提供唯一证书名称/编号，不能将其他认证文件任意配对。")
            kind = PpapDocumentType.PSW if token.upper() == "PSW" else PpapDocumentType.CONTROL_PLAN
            if not group.get_files_by_type(kind):
                raise DefectFact("W01", "原件明确声明的随包" + token + "未在完整冻结集合识别到，请确认并补充。")

    def paired():
        from .precheck_l1 import L1Facts

        others = _same_kind(agent, facts, PpapDocumentType.SUB_SUPPLIER_LIST)
        if len(others) != 1:
            raise UnknownFact("同源比对资料不唯一，需明确哪份是表格/版式成对原件。")
        reader = L1Facts(others[0])
        own = _material_slices(facts)
        other = _material_slices(reader)
        if len(own) != 1 or len(other) != 1:
            raise UnknownFact("成对原件含多个切片，需提供同切片对应，未按页序猜测。")
        left_version = _metadata(facts, own[0], ("版本", "版本号"), required=False)
        right_version = _metadata(reader, other[0], ("版本", "版本号"), required=False)
        if left_version is None or right_version is None:
            raise UnknownFact("成对原件未共同声明版本，不能把历史件当同一份版式件比较。")
        if identity(left_version) != identity(right_version):
            raise StateFact("S02", "两份原件版本不同，本项同一份表格/版式件比对不适用。")

        def values(source, located):
            result = {}
            for _, row, split in _material_records(source, located):
                if split:
                    raise UnknownFact("成对件含多材料续行，需确认材料组及版式对应。")
                part = identity(_one(row, "part"))
                if part in result:
                    raise UnknownFact("成对件同零件出现多次，缺少唯一行标识，未按行序比较。")
                result[part] = (identity(_one(row, "name")), source.strict_number(_one(row, "weight")))
            return result

        left, right = values(facts, own[0]), values(reader, other[0])
        merge_sources(facts, reader)
        if left != right:
            raise DefectFact("W04", "同零件同版本成对原件的零件集合、名称或单件重量不一致。")

    return {
        "PC-A1": lambda: _self_list(agent, facts),
        "PC-A2": lambda: _scope(facts, "model", ("车型", "适用车型", "项目")),
        "PC-A3": lambda: _scope(facts, "part", _PART),
        "PC-A4": lambda: _same_kind(agent, facts, PpapDocumentType.SUB_SUPPLIER_LIST),
        "PC-A5": generation,
        "PC-A6": row_keys,
        "PC-A7": filled,
        "PC-A8": external,
        "PC-B1": master_keys,
        "PC-B2": lambda: (root_match("part", _PART, "B06"), root_match("name", ("零件名称",), "B06")),
        "PC-B3": levels_check,
        "PC-B5": assembly_children,
        "PC-B6": boundary,
        "PC-B7": readable,
        "PC-B8": columns,
        "PC-C4": weight_scale,
        "PC-C8": suppliers,
        "PC-D1": row_keys,
        "PC-D2": row_keys,
        "PC-D3": fill_rate,
        "PC-D4": leaf_weights,
        "PC-D5": suppliers,
        "PC-E1": same_part,
        "PC-E2": lambda: root_match("component_supplier", ("供应商名称", "供应商"), "W04"),
        "PC-E3": paired,
        "PC-E4": name_content,
        "PC-E7": placeholder_consistency,
    }


def _labels(facts, table, aliases, *, required=True):
    wanted = {key(value) for value in aliases}
    values = []
    header_words = {key(label) for labels in _MATERIAL.values() for label in labels} | {
        key(label) for label in ("量具编号", "量具名称", "测量范围", "分析类型", "分析项目", "分辨率", "精度", "零件号")
    }
    for row in range(1, min(80, table["rows"]) + 1):
        for col in range(1, table["cols"] + 1):
            if key(effective(table, row, col).get("value")) not in wanted:
                continue
            right = col + 1
            for r1, c1, r2, c2 in table.get("merges", []):
                if r1 <= row <= r2 and c1 <= col <= c2:
                    right = c2 + 1
            facts.read(table, row, col)
            if right > table["cols"]:
                continue
            value = facts.read(table, row, right)
            if key(value) in header_words:
                continue
            values.append(value)
    if not values:
        if required:
            raise UnknownFact("区块" + table["id"] + "未定位到唯一的" + "/".join(aliases) + "标签值。")
        return None
    if len({identity(value) for value in values}) != 1:
        raise UnknownFact("区块" + table["id"] + "的" + "/".join(aliases) + "有多个值，须明确分表。")
    return values[0]


def _native_text(facts):
    result: list[Any] = []
    for table in facts.data["tables"]:
        if table.get("visual_evidence"):
            raise UnknownFact("原件含未解释的图像区，不能以原生文本未出现推断整份文件未声明。")
        for address, raw in table["cells"].items():
            if raw["state"] != "known" or raw.get("value") is not None:
                row, col = coordinate_to_tuple(address)
                result.append(str(facts.read(table, row, col) or ""))
    return result


def _msa_forms(facts):
    plans, reports = [], []
    for table in facts.data["tables"]:
        title = key(table["id"])
        if "计划" in title and any(word in title for word in ("测量", "msa")):
            plans.append(table)
        elif any(word in title for word in ("r&r", "grr", "重复性", "再现性", "kappa")) and not any(word in title for word in ("说明", "指南")):
            reports.append(table)
    if not plans and not reports:
        raise UnknownFact("没有可定位的MSA计划或R&R/Kappa报告区块，不能把未知工作表当作缺失类型。")
    return plans, reports


def _msa_plan_records(facts):
    aliases = {"id": ("量具编号",), "name": ("量具名称",), "part": _PART, "analysis": ("分析项目", "分析类型"), "range": ("测量范围",), "resolution": ("分辨率", "精度", "精度或分辨率")}
    located = _located(facts, aliases, ("id", "name"))
    result: list[Any] = []
    for item in located:
        if "计划" not in item[0]["id"]:
            continue
        for _, record in _rows(facts, item):
            record["_version"] = [_metadata(facts, item, ("版本", "版本号"), required=False)]
            record["_project"] = [_metadata(facts, item, ("项目", "项目名称", "车型"), required=False)]
            result.append(record)
    if not result:
        raise UnknownFact("未定位到MSA计划的量具数据行。")
    return result


def _msa_ops(agent, facts):
    def blank_forms():
        plans, reports = _msa_forms(facts)
        for table in plans:
            located = [item for item in _located(facts, {"id": ("量具编号",), "name": ("量具名称",)}, ("id", "name")) if item[0]["id"] == table["id"]]
            if not located:
                raise UnknownFact("MSA计划表存在但数据表头未定位，不能判整表空白。")
            if not _rows(facts, located[0], allow_empty=True):
                raise DefectFact("B05", "MSA计划表只有表头，没有数据行。")
        for table in reports:
            positions = []
            for row in range(1, table["rows"] + 1):
                for col in range(1, table["cols"] + 1):
                    if key(effective(table, row, col).get("value")) in {key("测量值"), key("测量数据"), key("读数")}:
                        positions.append((row, col))
            if not positions:
                raise UnknownFact("报告测量区没有明确读数标签，不能以未定位推断空表。")
            for header, col in positions:
                facts.read(table, header, col)
                if all(_blank(facts.read(table, row, col)) for row in range(header + 1, table["rows"] + 1)):
                    raise DefectFact("B05", "MSA报告已定位测量列整体为空。")
        raise UnknownFact("已核对可定位表单数据区；实际打印空白页需原件分页和框线证据，未以工作表数替代。")

    def numeric_chars():
        located = _located(facts, {"value": ("测量值", "测量数据", "读数", "P阶段Cpk结果")}, ("value",))
        for item in located:
            bad, total = 0, 0
            for _, record in _rows(facts, item):
                for value in record["value"]:
                    total += 1
                    if isinstance(value, bool) or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", str(value).strip()):
                        bad += 1
            if bad:
                raise DefectFact("B05" if bad == total else "W03", "已定位测量数值列有整列或个别值含非法数值字符。")

    def declared_scope():
        text = _native_text(facts)
        if not any(re.search(r"提交范围|本次范围|Scope", value, re.I) for value in text):
            raise DefectFact("W01", "原件未提供可辨识的本次提交范围声明；请确认计划/报告范围。")
        _self_list(agent, facts)

    def required_types():
        plans, reports = _msa_forms(facts)
        declarations = []
        for table in facts.data["tables"]:
            value = _labels(facts, table, ("提交范围", "本次范围", "Scope"), required=False)
            if value is not None:
                declarations.append(str(value))
        if len(set(declarations)) != 1:
            raise UnknownFact("本次计划/报告提交范围未唯一声明，不能把单独计划自动判缺报告。")
        scope = declarations[0]
        if "计划" in scope and not plans or any(word in scope for word in ("报告", "R&R", "Kappa")) and not reports:
            group = _group(agent, facts)
            if len(group.get_files_by_type(PpapDocumentType.MSA_REPORT)) > 1:
                raise UnknownFact("本文件缺声明类型，但冻结集合还有MSA原件，须先核对跨文件对端。")
            # Cite the declaration already read by _labels.
            raise DefectFact("B02", "原件明确声明的计划或报告类型整类未在本文件定位到；需按冻结范围核对补充。")
        raise UnknownFact("已核对本文件声明类型；跨文件是否补齐仍需冻结任务同类原件逐一关联，不把单文件检查当整批闭合。")

    def relations():
        from .precheck_l1 import L1Facts

        readers = [facts]
        group = getattr(agent, "group_context", None)
        if group is not None:
            _group(agent, facts)
            readers.extend(L1Facts(audit) for audit in group.get_files_by_type(PpapDocumentType.MSA_REPORT) if audit.audit_id != facts.audit.audit_id)
        planned = {}
        actual: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
        for reader in readers:
            plans, reports = _msa_forms(reader)
            if plans:
                for row in _msa_plan_records(reader):
                    identifier = identity(_one(row, "id"))
                    if not identifier or identifier in planned:
                        raise UnknownFact("冻结计划量具编号为空或重复，不能任取一行关联报告。")
                    planned[identifier] = row
            for table in reports:
                identifier = identity(_labels(reader, table, ("量具编号",)))
                if not identifier:
                    raise UnknownFact("报告量具编号为空，不能与计划配对。")
                actual.setdefault(identifier, []).append((reader, table))
            if reader is not facts:
                merge_sources(facts, reader)
        if not planned or not actual:
            if group is None:
                raise UnknownFact("本文件缺计划或报告一端且未提供完整冻结集合，不能推断本次漏交。")
            raise StateFact("S04-S", "完整冻结集合尚未定位计划与报告双端，请确认分批范围并补充同量具对端。", warning_codes=("W01",))
        return planned, actual

    def paired_context(record, reader, table):
        for field, labels in (("_version", ("版本", "版本号")), ("_project", ("项目", "项目名称", "车型"))):
            planned = _one(record, field)
            reported = _labels(reader, table, labels, required=False)
            if planned is not None or reported is not None:
                if _blank(planned) or _blank(reported) or identity(planned) != identity(reported):
                    raise UnknownFact("同量具计划与报告的项目或版本未对应，须先确认配对，未借历史报告闭合当前计划。")
        declared_customer = _labels(reader, table, ("客户", "客户名称"), required=False)
        if declared_customer is not None and key(declared_customer) not in {key("理想"), key("理想汽车"), "liauto"}:
            raise UnknownFact("报告客户与理想任务冲突，须拆分或确认后再比较。")
        if reader is not facts:
            merge_sources(facts, reader)

    def closure():
        planned, actual = relations()
        if set(planned) - actual.keys():
            _group(agent, facts)
            declarations = [_labels(facts, table, ("提交范围", "本次范围", "Scope"), required=False) for table in facts.data["tables"]]
            if not any(value is not None and "计划" in str(value) and "报告" in str(value) for value in declarations):
                raise StateFact("S04-S", "计划列出的量具尚未定位同批报告；未提供双端应收声明，请确认分批范围并补充。", warning_codes=("W01",))
            raise DefectFact("B02", "原件双端声明范围内的量具报告未在完整冻结集合覆盖。")
        if set(actual) - planned.keys():
            raise DefectFact("W04", "报告量具未登记在本文件计划中。")
        for identifier in planned.keys() & actual.keys():
            record = planned[identifier]
            for reader, table in actual[identifier]:
                paired_context(record, reader, table)
                part = _labels(reader, table, _PART)
                if identity(_one(record, "part")) != identity(part) or _blank(part):
                    raise UnknownFact("同编号量具的被测零件未对应，不能借不同零件报告闭合计划。")

    def attributes():
        planned, actual = relations()
        for identifier in planned.keys() & actual.keys():
            record = planned[identifier]
            for reader, table in actual[identifier]:
                paired_context(record, reader, table)
                for field, labels in (("part", _PART), ("name", ("量具名称",)), ("analysis", ("分析项目", "分析类型"))):
                    value = _labels(reader, table, labels)
                    if reader is not facts:
                        merge_sources(facts, reader)
                    if identity(_one(record, field)) != identity(value):
                        raise DefectFact("W04", "同量具计划与报告的被测对象、名称或分析类型冲突。")

    def metadata_consistency():
        _, reports = _msa_forms(facts)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for table in reports:
            identifier = identity(_labels(facts, table, ("量具编号",)))
            if not identifier:
                raise UnknownFact("报告量具编号为空，不能确认多联表单属于同一量具。")
            grouped.setdefault(identifier, []).append(table)
        for tables in grouped.values():
            for labels in (("量具名称",), _PART, ("规格公差", "公差"), ("评价人", "评价人姓名")):
                values = [identity(_labels(facts, table, labels)) for table in tables]
                if len(set(values)) > 1:
                    raise DefectFact("W04", "同编号MSA多联报告的对象、规格或评价人元数据不一致。")
        if not any(len(tables) > 1 for tables in grouped.values()):
            raise StateFact("S02", "仅定位到单份同量具表单，无多联元数据比较对象。")

    def conclusion():
        _, reports = _msa_forms(facts)
        for table in reports:
            explicit = _labels(facts, table, ("判定结论", "分析结论", "结论", "Conclusion"), required=False)
            if explicit is not None and re.fullmatch(r"\s*(?:可接受|不可接受|有条件接受|合格|不合格|OK|NG|PASS|FAIL|Acceptable|Unacceptable)\s*", str(explicit), re.I):
                continue
            grr = _labels(facts, table, ("%GRR", "%R&R"), required=False)
            ndc = _labels(facts, table, ("ndc",), required=False)
            if grr is not None and ndc is not None:
                facts.strict_number(str(grr).removesuffix("%"))
                facts.strict_number(ndc)
                continue
            if table.get("visual_evidence"):
                raise UnknownFact("未读取明确结论或GRR+ndc；原件含图像/勾选结论区，需原图核对，不能断言结论缺失。")
            phrases = []
            for address in table["cells"]:
                row, col = coordinate_to_tuple(address)
                phrases.append(str(facts.read(table, row, col) or ""))
            if any(re.fullmatch(r"\s*(?:本测量系统)?(?:结论[：:]?)?(?:可接受|不可接受|有条件接受|合格|不合格|Acceptable|Unacceptable)\s*", value, re.I) for value in phrases):
                continue
            raise DefectFact("B05", "已读取MSA报告中未见明确结论，也未见完整GRR与ndc指标表达。")

    return {
        "MSA-PRE-02": blank_forms,
        "MSA-PRE-04": numeric_chars,
        "MSA-PRE-05": closure,
        "MSA-PRE-06": attributes,
        "MSA-PRE-07": metadata_consistency,
        "MSA-PRE-13": declared_scope,
        "MSA-PRE-14": required_types,
        "MSA-PRE-15": relations,
        "MSA-PRE-16": conclusion,
        "MSA-PRE-21": lambda: _same_kind(agent, facts, PpapDocumentType.MSA_REPORT),
    }


_CAP_VALUES = ("Cp", "Cpk", "Pp", "Ppk", "均值", "极差", "标准差", "UCL", "LCL", "测量值", "测量数据", "读数", "P阶段Cpk结果")


def _cap_cells(facts):
    cells = []
    wanted = {key(name) for name in _CAP_VALUES}
    for table in facts.data["tables"]:
        for row in range(1, table["rows"] + 1):
            labels = [(col, str(effective(table, row, col).get("value"))) for col in range(1, table["cols"] + 1) if key(effective(table, row, col).get("value")) in wanted]
            if not labels:
                continue
            for col, label in labels:
                facts.read(table, row, col)
                business_header = any(key(effective(table, row, index).get("value")) in {key("特性名称"), key("工序流程编号"), key("特性编号"), key("序号")} for index in range(1, table["cols"] + 1))
                if len(labels) > 1 or business_header:
                    for index in range(row + 1, table["rows"] + 1):
                        raw = effective(table, index, col)
                        if str(raw.get("value") or "").startswith(_FOOTERS):
                            break
                        if raw["state"] != "known" or raw.get("value") is not None:
                            cells.append((table, index, col, label, raw))
                elif col < table["cols"]:
                    raw = effective(table, row, col + 1)
                    cells.append((table, row, col + 1, label, raw))
    if not cells:
        raise UnknownFact("未定位能力报告统计量或原始读数取值区，不能把任意单元格当计算结果。")
    return cells


def _formula_source(facts, table, row, col, formula):
    # The formula syntax is present in original OOXML even if its cached number
    # is unknown. Cite the syntax; do not claim to have evaluated its result.
    import json

    from audit.evidence import cell_locator

    locator = {"file_version_id": facts.audit.audit_id, **cell_locator(facts.data["source"]["format"], table["id"], row, col).model_dump(mode="json")}
    facts.sources.append({"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False, separators=(",", ":")), "excerpt": str(formula)[:500]})


def _cap_ops(agent, facts):
    def shape():
        suffix = Path(facts.audit.file_url).suffix.lower()
        if suffix not in {".xlsx", ".xlsm", ".xls", ".pdf", ".docx"}:
            raise UnknownFact("当前已读取载体不属于受支持能力原件格式，需提交可解析原件。")
        _cap_cells(facts)
        raise UnknownFact("已核实可打开及存在能力取值标签；载体族与误交文件仍需受控模板签名确认，不以文件后缀代替。")

    def evidence():
        for table, row, col, _, _ in _cap_cells(facts):
            facts.read(table, row, col)
        raise UnknownFact("已核对可定位统计量读取状态；纯图数值与其他未定位关键区块仍需核对。")

    def declared():
        text = _native_text(facts)
        if any(re.fullmatch(r"\s*(?:草稿|待填|DEMO|示例|Draft)\s*", value, re.I) for value in text):
            raise DefectFact("B02", "能力报告保留明确草稿或演示标记，尚非有效定稿件。")
        try:
            _located(facts, {"filename": ("文件名", "文件名称", "File name")}, ("filename",))
        except UnknownFact:
            raise StateFact("S01", "本原件无可定位自述文件清单，本子项无对账对象；未要求新增目录。") from None
        _self_list(agent, facts)

    def required_fields():
        ccsc = [table for table in facts.data["tables"] if "ccsc" in key(table["id"])]
        if not ccsc:
            raise UnknownFact("当前未确认理想CCSC主表族，报告页与图组字段须按实际载体签名读取，不能套用其他客户族。")
        located = _located(facts, {"name": ("特性名称",), "cpk": ("P阶段Cpk结果",), "decision": ("P阶段结果判定",)}, ("name",))
        for table, row, columns in located:
            facts.read(table, row, columns["name"][0])
            if not {"cpk", "decision"}.issubset(columns):
                raise DefectFact("B05", "理想CCSC表缺P阶段Cpk结果或P阶段结果判定列。")
        raise UnknownFact("已核对CCSC判定列；各控制图短期能力区块、对象身份及工具栏仍须逐区块确认。")

    def complete_rows():
        located = _located(facts, {"name": ("特性名称",), "cpk": ("P阶段Cpk结果",), "decision": ("P阶段结果判定",)}, ("name", "cpk", "decision"))
        for item in located:
            for _, record in _rows(facts, item):
                if _blank(_one(record, "name")):
                    raise UnknownFact("Cpk或判定有内容但特性名称为空，需先确定行主体，不能跳行。")
                if any(_blank(_one(record, name)) and str(_one(record, name)).strip() != "NA" for name in ("cpk", "decision")):
                    raise DefectFact("B04", "已填写特性名称的CCSC条目缺Cpk结果或结果判定。")

    def charts():
        sizes = set()
        for table in facts.data["tables"]:
            value = _labels(facts, table, ("子组容量", "样本容量", "Subgroup size"), required=False)
            if value is not None:
                size = facts.strict_number(value)
                if size <= 0 or size != size.to_integral_value():
                    raise UnknownFact("子组容量不是正整数，不能判应提供哪类控制图。")
                sizes.add(int(size))
        if not sizes:
            raise UnknownFact("未取得实际子组容量，不能要求I-MR、Xbar-R和Xbar-S全部同时提供。")
        for size in sizes:
            expected = "imr" if size == 1 else "xbarr" if size <= 8 else "xbars"
            if not any(expected in key(table["id"]) for table in facts.data["tables"]):
                raise DefectFact("B05", "按声明子组容量应提供的控制图" + expected + "未定位。")
        raise UnknownFact("已核对应提供图型工作表；图本体、中心线、控制限及短期能力区块需逐图读取，未以页签存在代替完整图。")

    def numeric():
        for table, row, col, _, raw in _cap_cells(facts):
            value = facts.read(table, row, col)
            if value == "NA":
                continue
            if isinstance(value, (str, bool)):
                raise DefectFact("B05", "能力取值区为文本数字、判定词或混写单位，不是可直接计算的数值单元格。")
            facts.strict_number(value)

    def placeholders():
        for table in facts.data["tables"]:
            for address, raw in table["cells"].items():
                row, col = coordinate_to_tuple(address)
                value = facts.read(table, row, col)
                text = str(value or "").strip()
                if text.casefold() in {"xx", "--", "待填", "tbd", "?", "＊"}:
                    raise DefectFact("B05", "能力原件保留模板占位符。")
                if re.fullmatch(r"#{4,}", text):
                    raise DefectFact("W05", "能力原件有列宽溢出占位，需确认真实数值。")
        for table, row, col, _, _ in _cap_cells(facts):
            text = str(facts.read(table, row, col) or "").strip()
            if text in {"N/A", "n/a", "－", "/", "无"}:
                raise DefectFact("W03", "能力值不适用标注须统一为NA。")
            if text in {"∞", "√", "≈"}:
                raise DefectFact("B05", "能力取值位置不是有效能力值。")
        if Path(facts.audit.file_url).suffix.lower() not in {".xlsx", ".xlsm"}:
            raise UnknownFact("已核对可读取文本占位符；当前载体批注和待办底色尚需原图核对。")
        book = load_workbook(facts.audit.file_url, data_only=False)
        has_fill = False
        try:
            for table in facts.data["tables"]:
                for row in book[table["id"]]:
                    for cell in row:
                        has_fill = has_fill or cell.fill.fill_type is not None
                        if cell.comment and any(word in cell.comment.text for word in ("待补", "待确认")):
                            _formula_source(facts, table, cell.row, cell.column, "原件批注：" + cell.comment.text)
                            raise DefectFact("B05", "能力原件仍有待补或待确认批注，需完成定稿。")
        finally:
            book.close()
        if has_fill:
            raise UnknownFact("已核对原件文本占位符和待办批注；底纹是否表示未完成需模板颜色口径，未把任意高亮都判为缺陷。")
        return "已检查原件文本、能力值、批注和底纹，未见模板占位、待办批注或底纹。"

    def metadata():
        found = False
        for table in facts.data["tables"]:
            size = _labels(facts, table, ("子组容量", "样本容量", "Subgroup size"), required=False)
            count = _labels(facts, table, ("子组数", "组数", "Number of subgroups"), required=False)
            if size is None and count is None:
                continue
            if size is None or count is None:
                raise UnknownFact("子组容量或子组数只定位到一项，不能推定另一项。")
            n, k = facts.strict_number(size), facts.strict_number(count)
            if n <= 0 or k <= 0 or n != n.to_integral_value() or k != k.to_integral_value():
                raise DefectFact("B05", "子组容量和子组数须为可复核正整数。")
            found = True
            exception = _labels(facts, table, ("小批量特批", "替代方案批准", "抽样豁免"), required=False)
            if not _blank(exception):
                continue
            if n * k < (30 if n == 1 else 100):
                raise DefectFact("W06", "原件声明读数总数低于单值30或计量100的预检样本门槛，需确认特批依据。")
            constants = {"E2": "2.66", "d2": "1.128", "D4": "3.267", "D3": "0"} if n == 1 else {"A2": ".577", "D3": "0", "D4": "2.114", "d2": "2.326"} if n == 5 else {}
            for label, expected in constants.items():
                value = _labels(facts, table, (label,), required=False)
                if value is not None and facts.strict_number(value) != Decimal(expected):
                    raise DefectFact("B06", "卷面控制图常数" + label + "与源给出的该子组容量常数不一致。")
        if not found:
            raise UnknownFact("未定位子组容量及组数，不能以页数、图数或固定默认5件替代。")
        raise UnknownFact("已核对声明样本量及源明确给出的n=1/5常数；其他容量AIAG受控常数表、实际图型及跨区块一致性尚需核对。")

    def trace():
        cells = _cap_cells(facts)
        formulas, raw_data, constants = 0, 0, 0
        for table, row, col, label, raw in cells:
            formula = str(raw.get("formula") or "")
            if formula:
                formulas += 1
                _formula_source(facts, table, row, col, formula)
                tokens = re.sub(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'', "", formula)
                if re.search(r"\b(?:RAND|RANDBETWEEN|RANDARRAY)\s*\(", tokens, re.I):
                    raise DefectFact("B05", "能力取值区使用随机数生成公式，测量或统计量来源不可信。")
                # External-link truth needs all three source preconditions. A
                # missing cache alone is not proof of a missing target.
                if re.search(r"\[[^\]]+\].*!", formula) and raw["state"] != "known":
                    raise UnknownFact("取值区有无缓存外部公式；须核对随批目标和同源替代后才能确认断链，未仅凭!判缺陷。")
            else:
                value = facts.read(table, row, col)
                if not _blank(value):
                    facts.strict_number(value)
                    if key(label) in {key("测量值"), key("测量数据"), key("读数")}:
                        raw_data += 1
                    else:
                        constants += 1
        if formulas or raw_data:
            raise UnknownFact("已定位真实公式或原始读数；尚需逐统计量确认依赖链，不以存在一条公式证明全部结果可追溯。")
        text = _native_text(facts)
        if any(any(word in value for word in ("计算过程", "系统导出", "原始测量记录", "计算依据")) for value in text):
            raise DefectFact("W05", "统计量为粘贴常量，文件声明计算或导出依据；请移交核对计算过程。")
        _group(agent, facts)
        if constants:
            raise UnknownFact("本原件统计量为常量且未定位追溯声明；冻结其他原件与外部系统凭证仍需对应，不以未读到声明断言不存在任何依据。")

    def chronology():
        located = _located(facts, {"sample": ("子组", "子组号", "组号"), "time": ("抽样时间", "采样时间", "测量时间")}, ("sample", "time"))
        for item in located:
            moments, has_time = [], []
            for _, record in _rows(facts, item):
                raw = _one(record, "time")
                if isinstance(raw, datetime):
                    value, timed = raw, raw.time() != time(0, 0)
                else:
                    text = str(raw or "").strip()
                    try:
                        value = datetime.fromisoformat(text.replace("/", "-"))
                    except ValueError:
                        normalized = normalize_excel_date(raw)
                        if normalized is None:
                            raise UnknownFact("抽样时间不是可可靠还原的日期时间，未猜测日期。") from None
                        value = datetime.combine(normalized, time())
                    timed = bool(re.search(r"\d{1,2}:\d{2}", text))
                moments.append(value)
                has_time.append(timed)
            if any(b < a for a, b in zip(moments, moments[1:])):
                raise DefectFact("B06", "后一个子组抽样时间早于前一个子组。")
            if len(moments) > 1 and all(has_time) and len(set(moments)) == 1:
                raise DefectFact("W06", "所有子组时间戳精确到时间仍完全一致，需确认复制痕迹。")
            signed = _labels(facts, item[0], ("签署日期", "批准日期", "报告签发日期"), required=False)
            signed_label = any(key(raw.get("value")) in {key("签署日期"), key("批准日期"), key("报告签发日期")} for raw in item[0]["cells"].values())
            if signed is not None or signed_label:
                date = normalize_excel_date(signed)
                if date is None:
                    raise DefectFact("W02", "报告已有签署日期栏但值为空或不可解析。")
                if date < max(moments).date():
                    raise DefectFact("W06", "报告签署日期早于数据采集完成日期。")
        raise UnknownFact("已核对抽样先后、完整时间戳和可定位签署日期；生产起始日及抽样方案未独立提供，不能证明正常生产覆盖。")

    def consistency():
        records: dict[Any, str] = {}
        located = _located(
            facts, {"name": ("特性名称",), "id": ("特性编号",), "lsl": ("LSL", "规格下限"), "usl": ("USL", "规格上限"), "cpk": ("Cpk", "P阶段Cpk结果"), "stage": ("阶段", "过程阶段")}, ("name",)
        )
        for item in located:
            part = _metadata(facts, item, _PART)
            if _blank(part):
                raise UnknownFact("特性表的对象零件号未明确，不能跨区块配对。")
            for _, row in _rows(facts, item):
                name = identity(_one(row, "name"))
                characteristic = identity(_one(row, "id")) if "id" in row else name
                if not characteristic:
                    raise UnknownFact("特性名称/编号为空，不能按行序跨表配对。")
                marker = (identity(part), characteristic)
                for field, values in row.items():
                    if field not in {"lsl", "usl", "cpk", "stage"}:
                        continue
                    value = identity(_one(row, field))
                    prior = records.setdefault((marker, field), value)
                    if prior != value:
                        raise DefectFact("W04" if field == "stage" else "B06", "同零件同特性的规格限、能力值或阶段在原件不同区块冲突。")
        raise UnknownFact("已核对本文件同对象同特性可定位重复数值；特殊特性清单/控制计划对端和汇总-明细全集仍需冻结关联集合配对。")

    return {
        "PRE-01": shape,
        "PRE-02": evidence,
        "PRE-03": declared,
        "PRE-06": required_fields,
        "PRE-07": complete_rows,
        "PRE-08": charts,
        "PRE-09": placeholders,
        "PRE-10": numeric,
        "PRE-13": metadata,
        "PRE-14": trace,
        "PRE-15": chronology,
        "PRE-16": consistency,
    }


def operations(agent, entry, facts):
    """Return source criterion IDs and existing SourceFacts-bound operations."""
    if entry == "L04":
        return _material_ops(agent, facts)
    if entry == "L05":
        return _msa_ops(agent, facts)
    if entry == "L06":
        return _cap_ops(agent, facts)
    return {}
