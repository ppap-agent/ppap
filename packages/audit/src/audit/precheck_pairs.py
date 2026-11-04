"""Fixed original-table and pair facts for preparation predicates."""

from __future__ import annotations

import unicodedata

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.precheck_facts import DefectFact, SourceFacts, StateFact, UnknownFact, key


def identity(value, *, numeric_zeros=False):
    text = "".join(unicodedata.normalize("NFKC", str(value if value is not None else "")).split()).casefold()
    return str(int(text)) if numeric_zeros and text.isdecimal() else text


def peer(group, *kinds, customer_owned=False, required=True):
    if group is None:
        raise UnknownFact("未提供本次冻结关联集合，尚不能确定是否缺少对端。")
    candidates = {audit.audit_id: audit for kind in kinds for audit in group.get_files_by_type(kind)}
    if not candidates:
        typed = {audit.audit_id for entries in group.by_doc_type.values() for audit in entries}
        if any(audit_id not in typed for audit_id in group.by_audit_id):
            raise UnknownFact("本批仍有类别未识别的可及原件，不能因文件名或类型未定判缺对端。")
        if not required:
            raise StateFact("S02", "本客户未要求本项可选对端；本批没有可比较的三文件集合，本项关闭，不要求补件。")
        code = "S04-C" if customer_owned else "S04-S"
        title = kinds[0].get_type_name() if hasattr(kinds[0], "get_type_name") else str(kinds[0])
        message = "缺比对文件《" + title + "》；" + ("请向客户索取受控资料，不计供应商缺件。" if customer_owned else "请供应商确认并补充同零件原件（W01 缺件提示）；本项未执行，不阻断。")
        raise StateFact(code, message, warning_codes=() if customer_owned else ("W01",))
    if len(candidates) != 1:
        raise UnknownFact("关联原件有多个候选，须确认配对，未任取文件。")
    return SourceFacts(next(iter(candidates.values())))


def table(reader, aliases, *, required, hint=None):
    candidates = []
    for original in reader.data["tables"]:
        if hint and not any(key(token) in key(original["id"]) for token in hint):
            # DOCX/PDF table ids carry geometry rather than a worksheet title.
            if reader.data["source"]["format"] not in {".pdf", ".docx"}:
                continue
        for header in range(1, min(original["rows"], 60) + 1):
            columns = {}
            for field, names in aliases.items():
                found = [column for column in range(1, original["cols"] + 1) if key(effective(original, header, column).get("value")) in {key(name) for name in names}]
                if len(found) == 1:
                    columns[field] = found[0]
            if set(required).issubset(columns) and len(set(columns.values())) == len(columns):
                candidates.append((original, header, columns))
    if len(candidates) != 1:
        raise UnknownFact("关联表及语义列不能唯一定位，未混用多个表或猜测列。")
    original, header, columns = candidates[0]
    for column in columns.values():
        reader.read(original, header, column)
    return original, header, columns


def rows(reader, located, *, trigger):
    original, header, columns = located
    result = []
    for row in range(header + 1, original["rows"] + 1):
        present = [effective(original, row, columns[field]) for field in trigger]
        if any(item["state"] != "known" for item in present):
            raise UnknownFact("数据行归属所需单元格不能读取；不能静默漏行。")
        if not any(key(item.get("value")) for item in present):
            continue
        result.append({field: reader.read(original, row, column, vertical_only=True) for field, column in columns.items()})
    if not result:
        raise UnknownFact("关联原件没有可核实有效行，不能按空集合通过。")
    return result


def optional_label(reader, original, aliases, before):
    wanted = {key(value) for value in aliases}
    if not any(key(effective(original, row, column).get("value")) in wanted for row in range(1, before) for column in range(1, original["cols"] + 1)):
        return None
    return reader.label(original, aliases, before)


def pair(left, left_table, left_header, right, right_table, right_header, customer, *, numeric_zeros=False, compare_version=True):
    left_part = left.label(left_table, ("零件号", "零件编号", "物料编号"), left_header)
    right_part = right.label(right_table, ("零件号", "零件编号", "物料编号"), right_header)
    placeholders = {"", "/", "na", "n/a", "tbd", "xxx", "待填", "待定", "无"}
    if identity(left_part) in placeholders or identity(right_part) in placeholders or identity(left_part, numeric_zeros=numeric_zeros) != identity(right_part, numeric_zeros=numeric_zeros):
        raise UnknownFact("原件零件号不完整或不对应，不能将不同零件当作跨文件缺陷。")
    identity_fields: list[tuple[str, ...]] = [("项目", "项目名称", "车型项目", "项目代号", "UAES项目编号")]
    if compare_version:
        identity_fields.append(("版本", "版本号", "修订版次"))
    for aliases in identity_fields:
        first = optional_label(left, left_table, aliases, left_header)
        second = optional_label(right, right_table, aliases, right_header)
        if first is not None or second is not None:
            if not identity(first) or identity(first) != identity(second):
                raise UnknownFact("原件项目或版本不对应/未填写，须先确认配对。")
    allowed = {"UAES": {"uaes", "联合电子", "联合汽车电子"}, "理想": {"理想", "理想汽车", "liauto"}, "蔚来": {"蔚来", "蔚来汽车", "nio"}}[customer]
    for reader, original, before in [(left, left_table, left_header), (right, right_table, right_header)]:
        declared = optional_label(reader, original, ("客户", "客户名称", "Customer", "Customer Name"), before)
        if declared is not None and key(declared) not in allowed:
            raise UnknownFact("原件客户与任务客户明确冲突，须拆分或确认任务。")


def merge_sources(target, *readers):
    # Preserve citations from every participating original under the same
    # bounded report budget; no source URLs or access credentials are copied.
    combined = []
    for reader in (target, *readers):
        for source in reader.sources[: max(1, 100 // (len(readers) + 1))]:
            if source not in combined:
                combined.append(source)
    target.sources[:] = combined


def history(reader):
    candidates = []
    for original in reader.data["tables"]:
        named = any(token in str(original["id"]).casefold() for token in ("履历", "变更", "修订", "历史", "history", "revision"))
        for header in range(1, min(original["rows"], 60) + 1):
            columns = {}
            for field, names in {
                "date": ("日期", "修订日期", "修改日期", "变更日期", "Date"),
                "version": ("版本", "版本号", "版次", "修订版本", "Revision"),
                "change": ("变更内容", "更改内容", "修订内容", "变更说明", "修订说明", "修改说明"),
                "operation": ("工序号", "工序编号", "过程编号"),
            }.items():
                matches = [column for column in range(1, original["cols"] + 1) if key(effective(original, header, column).get("value")) in {key(name) for name in names}]
                if len(matches) == 1:
                    columns[field] = matches[0]
            if "date" in columns and (named or {"version", "change"}.issubset(columns)):
                candidates.append((original, header, columns))
    if not candidates:
        hinted = any(any(token in str(original["id"]).casefold() for token in ("履历", "变更", "修订", "历史", "history", "revision")) for original in reader.data["tables"])
        if hinted:
            raise UnknownFact("履历区块存在但日期字段未定位；未把读取缺口当作无履历关闭。")
        raise StateFact("S02", "原件无可定位变更履历区块；本项关闭，不要求新增非必填区块。")
    if len(candidates) != 1:
        raise UnknownFact("变更履历存在多个候选区块，须确认有效范围。")
    located = candidates[0]
    data = rows(reader, located, trigger=tuple(field for field in ("date", "version") if field in located[2]))
    dates = []
    for row in data:
        if not key(row["date"]):
            raise DefectFact("W02", "变更履历的有效行日期尚未填写。")
        date = normalize_excel_date(row["date"])
        if date is None:
            raise DefectFact("B05", "已读取的履历日期不是有效日历日期。")
        dates.append(date)
    return located, data, dates
