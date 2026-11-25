"""MSL:4: match whole, source-bound material or process designations in both directions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from audit.engine.rule_engine import GuardStop, Sheet

from .materials import _FLOW, FLOW_STEP, IN_HOUSE, OUTSOURCED, OUTSOURCED_KINDS, compact, original_name, unable

_AMBIGUOUS = {"", "-", "—", "/", "na", "n/a", "待定", "见附件", "待补"}
_MATERIAL = {
    "材料名称和材料牌号",
    "材料牌号",
    "原材料牌号",
    "原材料名称",
    "材料名称",
    "材料名称和牌号",
    "gradecode",
    "materialgrade",
    "materialname",
    "materialnameandgradecode",
    "rawmaterialname",
    "corematerialgrade",
    "corematerialname",
    *(f"{role}{label}" for role in ("核心原材料", "主体材料", "基体材料", "配方辅料", "辅料") for label in ("名称", "牌号")),
}
_OPERATION = ("零件/部件名称", "部件名称", "子零件名称", "工序名称", "外购项名称", "外协工序", "外协项目", "componentname", "operationname")
_MARK = {"y", "yes", "是", "✓", "√", "★", "●", "☑", "x", "1"}
_SUPPLY = (*OUTSOURCED, "subcontract", "outsourced", "purchased")
_LOCAL = (*IN_HOUSE, "homemade", "in-house")
_SOURCE = (*_SUPPLY, *_LOCAL, "部件状态", "加工方式", "来源", "sourcing", "processstatus")
_CORE = ("核心原材料", "主体材料", "基体材料", "corematerial")
_AUX = ("配方辅料", "辅料", "添加剂")
_MATERIAL_NODE = re.compile(r"^(核心原材料|主体材料|基体材料|原材料|原料|材料牌号|配方辅料|辅料)(?:进料检验|入库|进料|检验)?\s*[:：]\s*(.+)$")
_GRADE = re.compile(r"[A-Za-z0-9][-A-Za-z0-9.()]*")


_STEP = (*FLOW_STEP, "工序名", "processname", "operationname")


@lru_cache(maxsize=512)
def _column_words(value):
    # The complete label in each printed language. “材料牌号备注” is not a grade column.
    return {compact(v) for v in re.findall(r"[A-Za-z]+(?:[ .\-/]+[A-Za-z]+)*|[\u3400-\u9fff]+(?:[/／][\u3400-\u9fff]+)*", str(value))}


def _is_column(head, names):
    return not _column_words(head).isdisjoint(names)


def _flow_header(value):
    label = compact(value)
    for word in sorted(_STEP, key=len, reverse=True):
        label = label.replace(compact(word), "")
    return label != compact(value) and not label.strip("/／（）():")


def _labels(head, names):
    return any(compact(key) in compact(head) for key in names)


@dataclass(frozen=True)
class Entry:
    kind: str
    text: str
    where: str
    # Optional flow process names can cover a list item without asserting that every operation is outsourced.
    # None: a material was read but its core/auxiliary role is not established.
    required: bool | None

    def keys(self):
        value = self.text.strip()
        if compact(value) in _AMBIGUOUS or value in {"原材料", "原料", "材料", "材料牌号", "外协", "委外", "外购", "外协工序"}:
            return set()
        # Explicitly printed aliases, not a global synonym list or arbitrary fragments of an item number.
        pair = re.fullmatch(r"(.+?)\s*[（(]\s*(?:简称|又称|别名|亦称|aka)\s*[:：]?\s*([^()（）]+)[)）]", value, re.I)
        names = [pair[1].strip(), pair[2].strip()] if pair else [value]
        keys = set(names)
        if self.kind == "材料":
            for name in names:
                # A material's written Chinese name followed by its complete grade (e.g. 黄铜 H62).
                # Keep the entire grade, including any suffix. Never split a grade at a hyphen or case-fold it.
                grade = re.fullmatch(r"[\u3400-\u9fff ]+\s+([^\s]+)", name)
                if grade and _GRADE.fullmatch(grade[1]) and re.search(r"\d", grade[1]):
                    keys.add(grade[1])
        return keys


def _entries(kind, text, where, required=True):
    # 顿号 enumerates distinct obligations, not aliases; slash has no universal meaning in a designation.
    return [Entry(kind, value.strip(), where, required) for value in re.split(r"[、；;]", text) if value.strip()]


def _sourcing(cells):
    """Read only sourcing/status cells, not vendor names, country-of-origin fields or prose elsewhere."""
    roles = set()
    for head, value in cells:
        if _is_column(head, _SOURCE):
            lit = compact(value)
            if lit in {compact(key) for key in _SUPPLY} or (lit in _MARK and _is_column(head, _SUPPLY)):
                roles.add("外协")
            if lit in {compact(key) for key in _LOCAL} or (lit in _MARK and _is_column(head, _LOCAL)):
                roles.add("自制")
    return roles


def _list_columns(headers):
    # Absence of an entire category is only established when its fields were actually read.
    known = set()
    if any(_is_column(h, _MATERIAL) for h in headers):
        known.add("材料")
    if any(_is_column(h, _OPERATION) for h in headers) and any(_is_column(h, _SOURCE) for h in headers):
        known.add("外协项")
    return known


def _list_row(cells, where, own):
    # Where a grade and a material name are printed separately, the more specific grade must trace,
    # not merely a common material family. Multiple grade cells remain separate obligations.
    entries = _material_fields(cells, where, listed=True)
    roles = _sourcing(cells)
    names = [v for h, v in cells if _is_column(h, _OPERATION)]
    if any(_is_column(h, ("外协工序", "外协项目", "外购项名称")) for h, _ in cells):
        roles.add("外协")
    if "外协" not in roles:
        if names and not roles:
            raise unable(f"清单{where}有部件或工序名称，但不能从该行确认内制或外协。")
        return entries
    component_ids = [v for h, v in cells if _labels(h, ("分部件零件号", "子件号", "componentnumber"))]
    if len(roles) > 1:
        if own and component_ids and all(own == num for num in component_ids):
            return entries  # The listed assembly itself, not an external operation.
        raise unable(f"清单{where}同时标为外协和自制，不能确认外协对象。")
    if not names and not entries:
        raise unable(f"清单{where}有外协标识但缺少可识别的名称。")
    for name in names:
        entries.extend(_entries("外协项", name, where))
    return entries


def _list_entries(agent, ctx, sheet, known):
    section = agent.section(sheet, "材料清单区")
    serial = section.column("材料序号")
    header, start = section.resolved.header_row, serial.data_start_row
    height, width = agent.extent(sheet)
    heads = {col: " ".join(dict.fromkeys(str(sheet.cell(r, col).value() or "") for r in range(header, start))) for col in range(1, width + 1)}
    known.intersection_update(_list_columns(heads.values()))
    try:
        own = agent.field_value(ctx, sheet, "零件号", agent.res.PART_NUMBER)
    except GuardStop:
        own = ""  # A missing top-level number never exempts an operation.
    # A blank/mis-merged serial cell cannot hide a material or an outsourced operation.
    # Only enter the body above the explicitly labelled signature block; templates can have lookups below it.
    body = {row.row_index for row in section.rows("材料序号")}
    filled = sheet.scan_non_empty(row_min=start, row_max=height, col_min=1, col_max=width)
    footer = min((c.row_index for c in filled if compact(re.split("[:：]", str(c.value()), maxsplit=1)[0]) in {compact(v) for v in agent.res.SIGNATURES}), default=height + 1)
    for c in filled:
        if c.row_index < footer and c.row_index not in body and _is_column(heads[c.col_index], (*_MATERIAL, *_OPERATION)) and not _is_column(c.value(), (*_MATERIAL, *_OPERATION)):
            agent.cite(sheet, c.row_index, c.col_index)
            raise unable(f"清单{sheet.title}第{c.row_index}行有材料或工序内容但未能归入记录，不能判已核全。")
    entries = []
    for i in sorted(body):
        values = [(c, head, str(v).strip()) for c, head in heads.items() if (v := sheet.cell(i, c).value()) is not None and str(v).strip()]
        if not any(c != serial.header_col for c, _, _ in values):
            continue
        for c, head, _ in values:
            if _is_column(head, (*_MATERIAL, *_OPERATION, *_SOURCE)):
                agent.cite(sheet, i, c)
        facts = _list_row([(h, v) for _, h, v in values], f"{sheet.title} 第{i}行", own)
        entries.extend(facts)
    return entries


def _material_fields(cells, where, *, listed):
    fields = [(head, value) for head, value in cells if _is_column(head, _MATERIAL)]
    # Retain all material fields with different core/aux scopes. Prefer a grade to its name within the same scope.
    found = []
    for core in (True, False, None):
        scoped = [(h, v) for h, v in fields if (True if _labels(h, _CORE) else False if _labels(h, _AUX) else None) is core]
        grades = [(h, v) for h, v in scoped if _labels(h, ("牌号", "grade"))]
        for _, val in grades or scoped:
            found.extend(_entries("材料", val, where, True if listed else core))
    return found


def _flow_row(cells, where):
    """A node, and only the named columns of that node. Never join unrelated rows or arbitrary PDF text."""
    descriptions = [v for h, v in cells if _flow_header(h)]
    roles = _sourcing(cells)
    found = []
    for desc in descriptions:
        if material := _MATERIAL_NODE.fullmatch(desc):
            kind, name = material.groups()
            found.extend(_entries("材料", name, where, False if kind in _AUX else True if kind in _CORE else None))
            continue
        # A sourcing qualification is meaningful only as a label on this operation, never as a word in prose.
        mode = re.fullmatch(r"(外协|委外|外委|外购|外包|自制|厂内|内制)\s*[:：]\s*(.+)|(.+?)\s*[（(](外协|委外|外委|外购|外包|自制|厂内|内制)[)）]", desc)
        local_roles = set(roles)
        if mode:
            part = mode[1] or mode[4]
            local_roles.add("自制" if part in IN_HOUSE else "外协")
            desc = mode[2] or mode[3]
        if len(local_roles) > 1:
            raise unable(f"流程图{where}的自制／外协标识相互冲突。")
        if "自制" not in local_roles:
            found.extend(_entries("外协项", desc, where, "外协" in local_roles or desc in OUTSOURCED_KINDS))
    if roles and not descriptions:
        raise unable(f"流程图{where}有来源标识，但无法定位该工序名称。")
    found.extend(_material_fields(cells, where, listed=False))
    return found


def _flow_entries(agent, ctx, item):
    from audit.checker_lib.uaes import materials

    if Path(str(item.file_url)).suffix.lower() == ".pdf":
        return _pdf_entries(agent, item, _flow_row)

    facts = []
    seen = False
    with agent.reading(ctx, item, materials.drawing_for_materials):
        for ws in ctx.workbook.worksheets:
            sheet = Sheet(ctx, ws)
            rows, cols = agent.extent(sheet)
            heads = [c for c in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=cols) if _flow_header(c.value())]
            for h in sorted({c.row_index for c in heads}):
                seen = True
                labels = [str(sheet.cell(h, c).value() or "") for c in range(1, cols + 1)]
                end = min((c.row_index for c in heads if c.row_index > h), default=rows + 1)
                for i in range(h + 1, end):
                    cells = [(label, str(val).strip()) for c, label in enumerate(labels, 1) if label and (val := sheet.cell(i, c).value()) is not None and str(val).strip()]
                    entries = _flow_row(cells, f"{original_name(item)}!{ws.title} 第{i}行")
                    if entries:
                        facts.extend(entries)
                        for col, label in enumerate(labels, 1):
                            if (_flow_header(label) or _is_column(label, (*_MATERIAL, *_SOURCE))) and sheet.cell(i, col).has_data():
                                agent.cite(sheet, i, col)
    if not seen:
        raise unable(f"流程图{original_name(item)}未读到可定位的工序列，不能判已核全。")
    return facts


def _pdf_entries(agent, item, read_row: Callable, known=None):
    """Only native table rows can certify the location on an original PDF. Free text is not a flow node."""
    import fitz

    def serial_label(value):
        return compact(value).rstrip(".") in {"序号", "序号no", "no", "itemno", "number"}

    def is_header(row):
        return any(_flow_header(v) for v in row) if known is None else any(serial_label(v) for v in row) and bool(_list_columns(row))

    entries = []
    version = str((item.file_metadata or {}).get("file_version_id") or item.audit_id)
    with fitz.open(str(item.file_url)) as doc:
        for page in doc:
            seen = False
            if page.get_text().strip() and not any(abs(fitz.Rect(img["bbox"]) & page.rect) >= 0.8 * abs(page.rect) for img in page.get_image_info()):
                for table in page.find_tables().tables:
                    rows = table.extract()
                    header = next((i for i, row in enumerate(rows) if is_header(row)), None)
                    if header is None:
                        continue
                    seen = True
                    if known is not None:
                        known.intersection_update(_list_columns(rows[header]))
                    for i in range(header + 1, len(rows)):
                        cells = [(h, val.strip()) for h, val in zip(rows[header], rows[i], strict=True) if h and val and val.strip()]
                        if known is not None and not any(serial_label(h) for h, _ in cells):
                            if any(_labels(h, (*_MATERIAL, *_OPERATION)) for h, _ in cells):
                                raise unable(f"{original_name(item)}第{page.number + 1}页有无法归行的材料或外协项。")
                            continue
                        where = f"{original_name(item)} 第{page.number + 1}页表格第{i + 1}行"
                        entries.extend(read_row(cells, where))
                        for c, (head, val) in enumerate(zip(rows[header], rows[i], strict=True)):
                            bbox = table.rows[i].cells[c]
                            if bbox and val and (_flow_header(head) or _is_column(head, (*_MATERIAL, *_OPERATION, *_SOURCE))):
                                agent.legacy().witness({"kind": "pdf_region", "page": page.number + 1, "bbox": list(bbox)}, val, version)
            if not seen:
                raise unable(f"{original_name(item)}第{page.number + 1}页未能把文字定位到原件表行，不能判已核全。")
    return entries


def check_trace(agent, ctx):
    flows = agent.lib.related_by_content(agent, _FLOW)
    if flows.missing:
        return "NA", "同批次未提交过程流程图，本条不适用。"
    known = {"外协项", "材料"}
    if Path(str(agent.audit_file.file_url)).suffix.lower() == ".pdf":
        records = _pdf_entries(agent, agent.audit_file, lambda cells, where: _list_row(cells, where, ""), known)
    else:
        records = [entry for sheet in agent.lists(ctx) for entry in _list_entries(agent, ctx, sheet, known)]
    if not records:
        raise unable("未从分供清单的原件记录行取得材料或外协项名称，不能据此判不存在。")
    nodes = [entry for item in flows.items for entry in _flow_entries(agent, ctx, item)]
    if not nodes:
        raise unable("流程图未读到可定位的节点；不能据此判不存在。")
    pending, missing, matched = [], [], []
    for left, right in ((records, nodes), (nodes, records)):
        index = {(r.kind, key): r for r in right for key in r.keys()}
        kinds = {r.kind for r in right}
        for entry in left:
            if entry.required is False:
                continue
            same = next((index[entry.kind, key] for key in sorted(entry.keys()) if (entry.kind, key) in index), None)
            if same:
                matched.append(f"{entry.where}“{entry.text}”↔{same.where}“{same.text}”")
            elif left is nodes and entry.kind not in kinds and entry.required and entry.keys() and entry.kind in known:
                missing.append(f"{entry.where}“{entry.text}”在清单中无{entry.kind}记录")
            else:
                pending.append(f"{entry.where}“{entry.text}”未取得同一{entry.kind}的对应依据")
    if missing:
        return "NG", "；".join(missing[:5]) + "。"
    if pending or not matched:
        raise unable("；".join([*pending[:5], "尚未能确认逐项双向对应，不能用泛称、相邻行或其他牌号补证。"]))
    return "PASS", "分供清单与流程图的材料及外协项逐项双向对应：" + "；".join(matched[:6]) + "。"
