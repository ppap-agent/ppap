"""Fixed Li inspection/DVP/DVR preparation predicates over original facts.

These checks do not judge measured performance, infer an absent scope, or treat
embedded-image existence as proof of a seal or a test photograph.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from openpyxl.utils.cell import coordinate_to_tuple

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.checkers.dimension_measurement_policy import parse_quality_standard_range
from audit.precheck_facts import DefectFact, StateFact, UnknownFact, key

_PART = ("零件/总成编号", "客户零件号", "零件号", "零部件编号", "零部件号", "物料编号", "零件编号")
_MODEL = ("适用车型", "车型", "产品型号", "车型/机型")
_SUPPLIER = ("供应商名称", "供应商", "供方名称")
_REPORT = ("报告编号", "文件编号", "试验报告编号")
_PHASE = ("申报阶段", "试验阶段", "试验类型")
_VERSION = ("图纸版本", "图纸版本号", "版本", "版本号")
_IR = {"id": ("序号", "NO", "No."), "standard": ("判定标准", "要求", "质量标准"), "method": ("供方检查方法/检查工具", "检查方法", "检查工具"), "result": ("判定结果", "结论")}
_NUM = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_PLACEHOLDERS = {"/", "na", "n/a", "-", "--", "待定", "待填", "xxx", "xx", "____"}


def _text(value):
    return str(value if value is not None else "").strip()


def _identity(value):
    return "".join(unicodedata.normalize("NFKC", _text(value)).split()).casefold()


def _one(facts, aliases, *, required=True):
    values = facts.values(aliases, required=required)
    unique = {_identity(value) for value in values}
    if len(unique) > 1:
        raise UnknownFact("同一字段存在多个不同值，需先确认字段的页、表与对象归属。")
    return values[0]


def _optional(facts, aliases):
    # Only absence of the label is optional. Unreadable cells stay unknown.
    names = {key(name) for name in aliases}
    if not any(key(cell.get("value")) in names for table in facts.data["tables"] for cell in table["cells"].values()):
        return None
    return _one(facts, aliases, required=False)


def _cells(facts):
    for table in facts.data["tables"]:
        if any(word in str(table["id"]) for word in ("填写说明", "填写指南")):
            continue
        for address, cell in table["cells"].items():
            if cell.get("value") is not None or cell.get("state") != "known":
                row, column = coordinate_to_tuple(address)
                yield table, row, column, facts.read(table, row, column)


def _group(agent, facts):
    group = getattr(agent, "group_context", None)
    if not group or not group.task_group_id or facts.audit.audit_id not in group.by_audit_id:
        raise UnknownFact("缺少包含当前原件的冻结任务文件集合，不能核对批次范围。")
    if group.unavailable_files:
        raise OSError("本次冻结集合含已提供但不能读取的原件，不能判作缺件。")
    return group


def _peers(agent, facts):
    from .precheck_l1 import L1Facts

    group = _group(agent, facts)
    result = []
    for audit in group.by_audit_id.values():
        if audit.audit_id == facts.audit.audit_id:
            continue
        if audit.customer != facts.audit.customer:
            raise UnknownFact("冻结集合存在不同客户原件，不能自动建立关联。")
        other = L1Facts(audit)
        result.append(other)
    return result


def _copy_sources(facts, other):
    for source in other.sources:
        if source not in facts.sources:
            facts.sources.append(source)


def _scope(agent, facts):
    scope = _optional(facts, ("提交范围", "本次提交范围"))
    if scope is None:
        # This is a known absent declaration, never a supplier B02.
        list(_cells(facts))
        raise DefectFact("W01", "未提供范围声明；仅按本批部分提交处理，未推定项目缺件。")
    project = _optional(facts, ("项目标识", "项目编号", "项目ID"))
    items = _optional(facts, ("PPAP项清单", "提交PPAP项清单", "本次PPAP项清单"))
    names = _optional(facts, ("提交文件清单", "本次文件清单", "文件清单"))
    if not all(_text(value) for value in (project, items, names)) or key(scope) not in {"full", "part", "全部", "部分"}:
        raise DefectFact("B02", "已作出的范围声明缺少项目、PPAP项、逐项文件清单或FULL/PART范围。")
    group = _group(agent, facts)
    actual = {Path(str(audit.file_metadata.get("original_filename") or "")).name for audit in group.by_audit_id.values()}
    if "" in actual:
        raise UnknownFact("冻结原件未保留原始文件名，不能按临时路径核对文件清单。")
    declared = {part.strip() for part in re.split(r"[\n;,；，]", str(names)) if part.strip()}
    if declared != actual:
        raise DefectFact("B02", "范围声明的文件名/数量与本次冻结实收原件不一致。")


def _versions(agent, facts):
    own = _one(facts, _REPORT, required=False)
    own_part = _one(facts, _PART)
    version = _optional(facts, _VERSION)
    effective_version = _optional(facts, ("唯一有效版本", "有效版本说明", "有效文件"))
    found = []
    for other in _peers(agent, facts):
        identifier = _optional(other, _REPORT)
        if identifier is None or _identity(identifier) != _identity(own) or _identity(_optional(other, _PART)) != _identity(own_part):
            continue
        candidate = _optional(other, _VERSION)
        _copy_sources(facts, other)
        if version is None or candidate is None:
            raise UnknownFact("同报告编号同件号的原件缺少明确版本信息，不能推定不存在多版本。")
        if candidate != version:
            found.append(candidate)
    if found and effective_version is None:
        raise DefectFact("B02", "同一报告编号存在多个版本，未提供书面的唯一有效版本说明。")
    if found and _identity(effective_version) not in {_identity(value) for value in [version, *found]}:
        raise UnknownFact("有效版本说明不能唯一对应本次实际提供的版本。")
    return "已按报告编号和明确版本核对本轮集合；未用相似文件名猜同源。"


def _outside(facts):
    expected = _optional(facts, ("项目应提交文件清单", "项目级应提交清单"))
    if expected is None:
        raise StateFact("S01", "未提供项目级清单，不推定当前原件在项目范围之外。")
    actual = facts.audit.file_metadata.get("original_filename")
    if not actual:
        raise UnknownFact("未保留原始文件名，不能核对项目级清单。")
    scope = _optional(facts, ("提交文件清单", "本次文件清单", "文件清单"))

    def names(value):
        return {Path(part.strip()).name for part in re.split(r"[\n;,；，]", str(value)) if part.strip()}

    if scope is not None and Path(actual).name in names(scope):
        return "当前原件已被本批范围声明明确列入。"
    if Path(actual).name not in names(expected):
        raise StateFact("S01", "当前原件不在明确提供的项目级应提交清单内。")


def _filename(facts):
    filename = facts.audit.file_metadata.get("original_filename")
    if not filename:
        raise UnknownFact("未保留原始文件名，不能把服务端临时路径当提交方命名。")
    # Compare explicit labeled filename tokens; arbitrary filename fragments are
    # not reliable IDs (hyphens may themselves be part numbers).
    labels = [("PN|件号|零件号", _PART, "B06"), ("车型|model", _MODEL, "W04"), ("阶段|phase", _PHASE, "W04"), ("版本|ver|rev", _VERSION, "W04")]
    checked = False
    for label, aliases, code in labels:
        match = re.search(r"(?:^|[ _;；])(?:" + label + r")[=:：]([^ _;；]+)", Path(filename).stem, re.I)
        if not match:
            continue
        checked = True
        if _identity(match[1]) != _identity(_one(facts, aliases)):
            raise DefectFact(code, "文件名中明确标注的身份字段与原件内对应字段不一致。")
    if not checked:
        raise UnknownFact("文件名没有可唯一定位的件号/车型/阶段/版本标记，未按任意片段猜测。")


def _units(facts):
    rows = facts.table_rows({"value": ("阈值", "判定阈值", "试验要求", "要求值"), "unit": ("单位", "量纲")})
    for row in rows:
        value, unit = _text(row["value"]), _text(row["unit"])
        if not value:
            continue  # blank engineering requirements are not invented results
        if re.search(r"\d", value) and not unit:
            raise DefectFact("B05", "已填写的数值判定阈值缺少单位，无法可靠比较。")
        if unit in {"Mpa", "mpa", "k"}:
            raise DefectFact("B05", "判定阈值单位大小写不符（MPa/K）。")
        if "扭矩" in value and unit == "N":
            raise DefectFact("B05", "扭矩判定阈值使用力单位N，缺少长度量纲。")
    quantity = _optional(facts, ("数量", "样品数量", "样本数量"))
    if quantity is not None and re.search(r"每[年日月]|/年|/月|/日", str(quantity)):
        raise DefectFact("W03", "数量字段混入周期描述，不能还原为单一数量。")


def _timeline(facts):
    rows = _trial_rows(
        facts,
        {
            "ps": ("计划开始", "计划开始日期", "计划试验开始日期"),
            "pe": ("计划结束", "计划结束日期", "计划试验结束日期"),
            "start": ("试验实际开始日期", "实际开始日期", "实际试验开始日期"),
            "end": ("试验实际结束日期", "实际结束日期", "实际试验结束日期"),
        },
    )
    declaration = _optional(facts, ("跨年度或借用说明", "借用说明", "跨年度说明"))
    for row in rows:
        if any(not _text(value) for value in row.values()):
            continue
        parsed = {name: normalize_excel_date(value) for name, value in row.items()}
        if any(value is None for value in parsed.values()):
            raise UnknownFact("计划/实际日期尚不能可靠归一化。")
        ps, pe, actual_start, actual_end = (parsed[name] for name in ("ps", "pe", "start", "end"))
        if ps is None or pe is None or actual_start is None or actual_end is None:
            raise UnknownFact("计划/实际日期尚未全部取得。")
        if (ps - actual_start).days > 30 or (actual_end - pe).days > 30:
            if declaration and _text(declaration):
                return "已声明跨年度/借用场景，超过30天仅作记录，未判业务合理性。"
            raise DefectFact("W06", "实际开始早于计划超过30天，或实际结束晚于计划超过30天。")


def _date_years(facts):
    report = _text(_one(facts, _REPORT))
    years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", report))
    if len(years) != 1:
        raise UnknownFact("报告编号没有唯一可定位的四位年份码；未推定两位码含义。")
    checked = False
    for names in (("签发日期", "报告日期"), ("试验开始日期", "实际试验开始日期"), ("试验结束日期", "实际试验结束日期")):
        value = _optional(facts, names)
        if value is None:
            continue
        parsed = normalize_excel_date(value)
        if parsed is None:
            raise UnknownFact("原件日期不能可靠归一化。")
        checked = True
        if abs(parsed.year - int(next(iter(years)))) > 2:
            raise DefectFact("W04", "编号年份与签发/试验日期相差超过2年；未作真伪判断。")
    if not checked:
        raise UnknownFact("没有可对照编号年份的签发或试验日期。")


def _cross_identity(agent, facts):
    own_part = _one(facts, _PART)
    own_version = _optional(facts, _VERSION)
    count = 0
    for other in _peers(agent, facts):
        part = _optional(other, _PART)
        if part is None:
            continue
        _copy_sources(facts, other)
        # Group membership alone does not mean all files concern one part.
        if _identity(part) != _identity(own_part):
            raise UnknownFact("本批包含不同件号，缺少独立的同对象关联，未将合法多零件提交判不一致。")
        other_version = _optional(other, _VERSION)
        if own_version is None or other_version is None or _identity(own_version) != _identity(other_version):
            raise UnknownFact("同件号对端缺少相同明确版本，不能将合法不同版本判身份冲突。")
        for aliases in (_MODEL, _SUPPLIER):
            left, right = _optional(facts, aliases), _optional(other, aliases)
            _copy_sources(facts, other)
            if left is None or right is None:
                raise UnknownFact("同件号对端缺少本项身份字段，不能核对。")
            if _identity(left) != _identity(right):
                raise DefectFact("W04", "本轮同件号原件的车型或供应商标识不同，已定位双原件。")
        count += 1
    if not count:
        raise StateFact("S04-S", "本轮未提供可核对的同对象其他报告。", warning_codes=("W01",))


def _same_source(agent, facts):
    own_report = _one(facts, _REPORT)
    own_part = _one(facts, _PART)
    candidates = []
    for other in _peers(agent, facts):
        report, part = _optional(other, _REPORT), _optional(other, _PART)
        if report is not None and part is not None and _identity(report) == _identity(own_report) and _identity(part) == _identity(own_part):
            candidates.append(other)
    if not candidates:
        raise StateFact("S01", "本轮没有报告编号与件号均相同的同源副本；未要求额外提交PDF。")
    if len(candidates) != 1:
        raise UnknownFact("同源候选不唯一，需指定唯一有效载体。")
    other = candidates[0]
    for aliases in (_VERSION, _MODEL, _SUPPLIER):
        left, right = _optional(facts, aliases), _optional(other, aliases)
        _copy_sources(facts, other)
        if left is None or right is None:
            raise UnknownFact("同源副本缺少明确版本或身份字段，不能证明完整一致。")
        if _identity(left) != _identity(right):
            raise DefectFact("B06", "本轮同源副本的明确身份或版本字段不同。")
    left_tables = facts.data["tables"]
    right_tables = other.data["tables"]
    left_shape = [(table["id"], table["rows"], table["cols"]) for table in left_tables]
    right_shape = [(table["id"], table["rows"], table["cols"]) for table in right_tables]
    if facts.data["source"]["format"] != other.data["source"]["format"] or left_shape != right_shape:
        raise UnknownFact("同源副本载体或布局不同，尚不能可靠对齐完整表头与逐行值；未以身份相同代替完整一致。")
    has_visual = False
    for left, right in zip(left_tables, right_tables):
        has_visual = has_visual or bool(left.get("visual_evidence") or right.get("visual_evidence"))
        for row in range(1, left["rows"] + 1):
            for col in range(1, left["cols"] + 1):
                value = facts.read(left, row, col)
                peer = other.read(right, row, col)
                _copy_sources(facts, other)
                if _text(value) != _text(peer):
                    # Keep the exact differing pair even when previous reads
                    # reached the evidence display budget.
                    facts.sources = []
                    other.sources = []
                    facts.read(left, row, col)
                    other.read(right, row, col)
                    _copy_sources(facts, other)
                    raise DefectFact("B06", "本轮同源、同布局副本的同位置字段值不同，已保留双原件。")
    if has_visual or facts.data["source"]["format"] not in {".xlsx", ".xlsm", ".xls"}:
        raise UnknownFact("同布局副本文字与表头已逐格核对；签章/照片内容尚未双端核实，不能声称完整一致。")
    return "本轮同源同布局副本的原件单元格已逐格核对；没有用身份相同代替逐行比较。"


def _page_numbers(facts, *, code):
    if Path(facts.audit.file_url).suffix.lower() != ".pdf":
        raise UnknownFact("Office未提供固定分页原件，不能用工作表数推定实际页数。")
    import json

    import fitz

    found = []
    with fitz.open(facts.audit.file_url) as document:
        if not 0 < len(document) <= 1000:
            raise UnknownFact("PDF页数超出本项有界读取范围。")
        total = len(document)
        for index, page in enumerate(document):
            marks = []
            for block in page.get_text("blocks"):
                if block[1] < page.rect.height * 0.85:
                    continue
                text = str(block[4]).strip()
                match = re.fullmatch(r"(?:第\s*)?(\d+)\s*(?:/|of|页\s*共)\s*(\d+)\s*页?", text, re.I)
                if match:
                    marks.append((int(match[1]), int(match[2])))
                    locator = {"file_version_id": facts.audit.audit_id, "kind": "pdf_page", "page": index + 1}
                    facts.sources.append({"kind": "structure", "locator": json.dumps(locator), "excerpt": text})
            if len(marks) > 1:
                raise UnknownFact("同页存在多个页脚页码，可能为内嵌子报告，不能猜分页作用域。")
            if marks:
                found.append((index + 1, *marks[0]))
        if not found:
            raise UnknownFact("未定位明确页脚页码，不把正文分数当页码。")
        if any(physical != declared or count != total for physical, declared, count in found):
            raise DefectFact(code, "PDF页脚页序/总页数与实际文件分页矛盾。")
        if len(found) != total:
            raise UnknownFact("仅部分页面有明确页码，完整连续性尚未核实。")


def _unknown(message):
    def operation():
        raise UnknownFact(message)

    return operation


def _ir_rows(facts):
    """Read physical sample columns; merged child rows remain separate rows."""
    result = []
    for table in facts.data["tables"]:
        header = None
        for row in range(1, min(table["rows"], 80) + 1):
            columns = {}
            for name, aliases in _IR.items():
                found = {effective(table, row, col)["anchor"]: col for col in range(1, table["cols"] + 1) if key(effective(table, row, col).get("value")) in {key(alias) for alias in aliases}}
                if len(found) == 1:
                    columns[name] = next(iter(found.values()))
            actual = [col for col in range(1, table["cols"] + 1) if key(effective(table, row, col).get("value")) in {"检查数据", "实测值", "实测数据", "测量结果"}]
            if len(columns) == len(_IR) and actual:
                header = row, columns, min(actual)
                break
        if header is None:
            continue
        start, columns, first = header
        # choose leftmost anchor rather than a merged-header duplicate
        columns = {name: coordinate_to_tuple(effective(table, start, col)["anchor"])[1] for name, col in columns.items()}
        for col in [*columns.values(), first]:
            facts.read(table, start, col)
        bottom = max([start] + [span[2] for span in table.get("merges", []) if span[0] == start and span[1] <= columns["id"] <= span[3]])
        for row in range(bottom + 1, table["rows"] + 1):
            serial = _text(effective(table, row, columns["id"]).get("value"))
            if any(serial.startswith(word) for word in ("车型", "零件名称", "供应商", "责任人", "日期", "填表说明")):
                break
            selected = [*columns.values(), *range(first, columns["result"])]
            if all(effective(table, row, col).get("state") == "known" and not _text(effective(table, row, col).get("value")) for col in selected):
                continue
            if key(serial) in {"序号", "no", "尺寸检验", "非尺寸检验", "尺寸", "非尺寸"}:
                continue
            values = {name: facts.read(table, row, col, vertical_only=True) for name, col in columns.items()}
            values["actual"] = [facts.read(table, row, col, vertical_only=True) for col in range(first, columns["result"])]
            values["anchor"] = effective(table, row, columns["standard"])["anchor"]
            values["serial_anchor"] = effective(table, row, columns["id"])["anchor"]
            values["table"] = table["id"]
            result.append(values)
    if not result:
        raise UnknownFact("未取得完整的检验报告业务表头与物理样本行，需核对受控版式。")
    return result


def _ir_scope(agent, facts, *, project=False):
    scope = _optional(facts, ("提交范围", "本次提交范围"))
    if project and key(scope) not in {"full", "全部"}:
        raise StateFact("S01", "非FULL或未明确FULL，不判项目全量报告齐套。")
    names = ("项目应有报告清单", "项目应有检验报告清单") if project else ("提交报告类别", "本次报告类别")
    declared = _optional(facts, names)
    if declared is None:
        if project:
            raise StateFact("S01", "未提供项目应有清单，不推定四类报告全部应交。")
        list(_cells(facts))
        raise DefectFact("W01", "未声明报告类别，仅以实际定位类别作为本批范围。")
    requested = [word for word in ("尺寸", "材料", "功能", "可靠性") if word in str(declared)]
    if not requested:
        raise UnknownFact("声明类别不能映射到分则报告类别。")
    located = " ".join(str(table["id"]) + " " + " ".join(_text(cell.get("value")) for address, cell in table["cells"].items() if coordinate_to_tuple(address)[0] < 8) for table in facts.data["tables"])
    list(_cells(facts))
    for other in _peers(agent, facts):
        located += " " + " ".join(str(table["id"]) for table in other.data["tables"])
        for table, row, column, value in _cells(other):
            if row < 8:
                located += " " + _text(value)
        _copy_sources(facts, other)
    for word in requested:
        if word not in located:
            raise DefectFact("W01", "声明的" + word + "报告在当前原件内未定位，需结合冻结文件清单补齐核对。")


def _ir_supplier(facts):
    supplier, code, part = _one(facts, _SUPPLIER), _one(facts, ("供应商代码", "供应商编号")), _one(facts, _PART)
    if not _text(supplier) or not _text(code):
        raise DefectFact("W02", "供应商名称或代码为空。")
    if _identity(code) == _identity(part) or not re.fullmatch(r"[A-Za-z0-9_.-]+", _text(code)):
        raise DefectFact("W03", "供应商代码与客户件号串位或含无效字符。")


def _ir_identity(facts):
    _one(facts, _PART)
    _one(facts, ("供应商零件号", "供方零件号"))
    _one(facts, _VERSION)
    _one(facts, ("零件名称", "物料名称"))
    weight = _one(facts, ("重量", "单件重量", "零件重量"))
    if not _NUM.fullmatch(_text(weight)):
        raise DefectFact("W03", "零件重量不是可解析的正数。")
    if Decimal(_text(weight)) <= 0:
        raise DefectFact("W03", "零件重量不是正数。")
    raise UnknownFact("已核对独立件号字段、版本、名称及正重量；未提供独立主数据，件号是否互串尚不能确认。")


def _ir_dates(facts):
    dates = [normalize_excel_date(_one(facts, (name,))) for name in ("检验日期", "审核日期", "批准日期")]
    if any(value is None for value in dates):
        raise DefectFact("W03", "三级签批日期有不可解析值。")
    first, middle, last = dates
    if first is None or middle is None or last is None:
        raise UnknownFact("签批日期尚未全部取得。")
    if not first <= middle <= last or (last - first).days > 30:
        raise DefectFact("W06", "三级签批日期逆序或首尾跨度超过30天。")


def _ir_names(facts):
    values = [_one(facts, names, required=False) for names in (("检验人", "检验员"), ("审核人", "审核员"), ("批准人",))]
    if any(not _text(value) or key(value) in _PLACEHOLDERS for value in values):
        raise DefectFact("W02", "三级签批姓名为空或为占位符。")
    if key(values[0]) == key(values[1]):
        raise DefectFact("W06", "检验人与审核人相同，未满足职责分离。")


def _ir_modality(facts, *, gauge_only=False):
    rows = _ir_rows(facts)
    unknown = False
    for row in rows:
        method = _text(row["method"])
        if not method:
            pool = _optional(facts, ("全局设备池", "全局测量设备"))
            if not pool:
                raise DefectFact("W02", "行级量具为空且没有可读取的全局设备池。")
            method = _text(pool)
        actual = [_text(value) for value in row["actual"] if _text(value)]
        qualitative = any(key(value) in {"ok", "合格", "ng", "不合格", "pass", "fail"} for value in actual)
        quantitative = parse_quality_standard_range(row["standard"]) is not None
        if not actual:
            raise UnknownFact("实测值为空，尚不能核对量具与结果模态。")
        if not quantitative and re.search(r"\d", _text(row["standard"])):
            raise UnknownFact("带数值标准尚不能解析，不能推定量具与结果模态匹配。")
        if quantitative and qualitative:
            # §7.7 is a Li override to the generic no-whitelist principle.
            if method not in {"通止规", "菲林尺", "卡板"}:
                raise DefectFact("W06", "理想尺寸行采用通过型数据时，方法不是通止规、菲林尺或卡板。")
        elif not gauge_only and quantitative and not all(_NUM.fullmatch(value) for value in actual):
            raise DefectFact("W03", "定量行实测数据不能归为单一计量模态。")
        elif gauge_only:
            modality = _optional(facts, ("量具模态", "全局量具模态"))
            if modality is None:
                unknown = True
            elif key(modality) not in {"计量型", "计数型", "变量型", "属性型"}:
                raise UnknownFact("明确量具模态字段尚不能归一化。")
    if unknown:
        raise UnknownFact("量具已有值，但缺少明确的计量/计数模态；未用自由量具名猜模态。")


def _ir_sequence(facts):
    seen: dict[str, list[int]] = defaultdict(list)
    serial_anchors = set()
    parents = set()
    for row in _ir_rows(facts):
        table = row["table"]
        if not _text(row["standard"]):
            if table not in parents:
                raise DefectFact("W06", "要求为空的子行没有上游父行。")
            continue
        parents.add(table)
        token = _text(row["id"])
        if not re.fullmatch(r"\d+(?:\.0)?", token):
            raise UnknownFact("序号不是可可靠解析的整数。")
        value = int(Decimal(token))
        anchor = table, row["serial_anchor"]
        if anchor not in serial_anchors:
            seen[table].append(value)
            serial_anchors.add(anchor)
    if any(values != list(range(1, len(values) + 1)) for values in seen.values()):
        raise DefectFact("W06", "检验项目序号不连续。")


def _ir_standards(facts, *, tolerance=False):
    for row in _ir_rows(facts):
        value = _text(row["standard"])
        if not value:
            continue  # parent-child ownership is checked independently in IR26
        if "\ufffd" in value:
            raise UnknownFact("原始标准含替代字符，文本尚未可靠转译。")
        if re.search(r"\d", value):
            limits = parse_quality_standard_range(value)
            if tolerance:
                if limits is None or not all(Decimal(str(bound)).is_finite() for bound in limits):
                    raise UnknownFact("定量要求未构成可验证的有限闭区间；未虚构ISO兜底或单边界。")
                if limits[0] > limits[1]:
                    raise DefectFact("W06", "标准上下界代数倒挂。")
            elif limits is None:
                raise UnknownFact("带数值标准尚不能可靠结构化，未按普通定性文本通过。")


def _ir_occurrences(facts):
    groups: dict[tuple, list[int]] = {}
    parents = {}
    for row in _ir_rows(facts):
        text = _text(row["standard"])
        if text:
            identity = row["table"], row["anchor"]
            match = re.match(r"(\d+)\s*[-×xX]\s*(?:[ΦφØM]|\d)", text)
            groups.setdefault(identity, [int(match[1]) if match else 1, 0])
            parents[row["table"]] = identity
        elif row["table"] in parents:
            identity = parents[row["table"]]
        else:
            raise UnknownFact("处数子行没有可确认的父特征。")
        groups[identity][1] += 1
    if any(expected != actual for expected, actual in groups.values()):
        raise DefectFact("W06", "要求处数与实际展开物理行数不同，未将单格样本斜杠计为特征数。")


def _ir_data(facts, *, raw=False, verdict=False):
    for row in _ir_rows(facts):
        if verdict:
            if key(row["result"]) not in {"", "ok", "ng", "nok", "pass", "fail", "合格", "不合格", "通过", "不通过", "p", "f"}:
                raise DefectFact("W03", "供应商结论无法归一化为通过/不通过/空值。")
            continue
        values = [_text(value) for value in row["actual"] if _text(value)]
        if not values:
            raise DefectFact("W02", "实测行没有可定位的数据。")
        if raw:
            if any(re.search(r"\d\.\.\d|#{3,}|[\u200b-\u200f\ufeff]|\n\s*\n", value) for value in values):
                raise DefectFact("W03", "实测原文包含非法数值、截断标记、不可见字符或多余空行。")
            continue
        modes = set()
        for value in values:
            if _NUM.fullmatch(value):
                modes.add("numeric")
            elif key(value) in {"ok", "ng", "nok", "pass", "fail", "合格", "不合格"}:
                modes.add("qualitative")
            elif any(term in value for term in ("见报告", "参见", "附件")):
                modes.add("external")
            else:
                raise UnknownFact("原始实测文本尚不能唯一归入数值、定性或外部引用。")
        if len(modes) != 1:
            raise DefectFact("W03", "同一特征实测混有多种数据模态。")
        if parse_quality_standard_range(row["standard"]) is not None and modes == {"qualitative"}:
            if _text(row["method"]) not in {"通止规", "菲林尺", "卡板"}:
                raise DefectFact("W03", "定量特征使用定性数据且不满足理想专属通过型量具条件。")


def _declared_categories(facts):
    units = {
        "线性尺寸": {"mm", "cm", "m"},
        "直径": {"mm", "cm"},
        "角度": {"°", "deg", "度"},
        "形位公差": {"mm", "μm", "um"},
        "表面粗糙度": {"μm", "um"},
        "倒角": {"mm", "°", "deg", "度"},
        "外观": {"", "/", "na"},
        "材料性能": None,
    }
    rows = facts.table_rows({"category": ("特征分类", "特征类别"), "unit": ("单位", "测量单位")})
    for row in rows:
        category = _text(row["category"])
        if category not in units:
            raise UnknownFact("特征类别尚不能映射到分则八类；未根据自由文本猜测。")
        allowed = units[category]
        if allowed is not None and _text(row["unit"]) not in allowed:
            raise DefectFact("W03", "明确特征分类与填写单位不匹配。")


def _symbols(facts):
    rows = facts.table_rows({"mark": ("特殊特性", "特性标识", "特殊特性标识")})
    allowed = {"", "★", "◇", "cc", "sc", "kpc", "[3,5]", "[5]", "[f|3]", "[s]"}
    for row in rows:
        if key(row["mark"]) not in {key(value) for value in allowed}:
            raise DefectFact("W03", "特殊特性标识不能映射到缺省CP-G符号集。")
    return "已读取特殊特性符号；理想单一报告不要求封面附件，未套用其他客户附件合同。"


def _composite(facts):
    original_rows = _ir_rows(facts)
    if any("锥度" in _text(row["standard"]) for row in original_rows):
        raise UnknownFact("锥度含多个物理量，当前标准尚未可靠拆分，不能判子维度完整。")
    candidates = [row for row in original_rows if re.search(r"\d+(?:\.\d+)?\s*[xX×]\s*\d+(?:\.\d+)?\s*[°度]", _text(row["standard"]))]
    if not candidates:
        return "已读取原始标准，未发现可确定的线性×角度复合要求。"
    rows = facts.table_rows({"id": _IR["id"], "sub": ("子要求", "子维度"), "unit": ("单位", "子要求单位")})
    for candidate in candidates:
        units = {_text(row["unit"]) for row in rows if key(row["id"]) == key(candidate["id"]) and _text(row["sub"])}
        if not ("mm" in units and bool(units & {"°", "度", "deg"})):
            raise DefectFact("W02", "线性×角度复合特征未拆出两种独立单位的子要求。")


def operations(agent, entry, facts):
    """Return zero-argument source operations; the caller owns observation."""
    if entry == "L01":
        result = {
            "IR01a": lambda: _ir_scope(agent, facts),
            "IR01b": lambda: _ir_scope(agent, facts, project=True),
            "IR12": lambda: _ir_modality(facts),
            "IR14": lambda: _inspection_seal(agent, facts),
            "IR18": lambda: (_ir_rows(facts), "已定位理想单一报告的表头、数据及物理样本列。")[-1],
            "IR19": lambda: _ir_supplier(facts),
            "IR20": lambda: _ir_identity(facts),
            "IR22": lambda: _ir_dates(facts),
            "IR23": lambda: _ir_names(facts),
            "IR24": lambda: _ir_modality(facts, gauge_only=True),
            "IR25": lambda: _one(facts, ("未注公差标准", "未注公差", "一般公差标准")),
            "IR26": lambda: _ir_sequence(facts),
            "IR27": lambda: _declared_categories(facts),
            "IR28": lambda: _ir_standards(facts),
            "IR29": lambda: _symbols(facts),
            "IR30": lambda: _ir_occurrences(facts),
            "IR31": lambda: _ir_standards(facts, tolerance=True),
            "IR32": lambda: _composite(facts),
            "IR33": lambda: _ir_data(facts),
            "IR34": lambda: _ir_data(facts, raw=True),
            "IR35": lambda: _ir_data(facts, verdict=True),
        }
        return {_source_id(name): operation for name, operation in result.items()}
    if entry not in {"L02", "L03"}:
        return {}
    result = {
        "COR05": lambda: _units(facts),
        "COM01": lambda: _scope(agent, facts),
        "COM02": lambda: _versions(agent, facts),
        "CON01": lambda: _same_source(agent, facts),
        "CON04": lambda: _filename(facts),
    }
    if entry == "L02":
        result.update(
            {
                "COR09": _unknown("完整原件的截断、遮挡、内嵌模糊和批注可读性尚未提供可验证视觉事实。"),
                "COR10": lambda: _internal_contradiction(facts),
                "COM03": lambda: _phase(facts),
                "COM08": lambda: _explanations(agent, facts),
                "COM10": lambda: _outside(facts),
                "CON05": lambda: _family(facts),
                "CON06": lambda: _dictionaries(agent, facts),
                "CON07": lambda: _paired_dates(agent, facts),
                "CON08": lambda: _family_mapping(facts),
                "CON10": lambda: _timeline(facts),
                "CON11": lambda: _cross_identity(agent, facts),
            }
        )
    else:
        result.update(
            {
                "COR08": _unknown("完整原件的局部遮挡、模糊或仅批注文字尚无可验证视觉事实，未归责供应商。"),
                "COR09": lambda: _page_numbers(facts, code="W05"),
                "COR10": _unknown("实际印章的裁切、淡色及遮挡需原件视觉证据；未以对象个数代替印章识别。"),
                "COM05": lambda: _report_sections(facts),
                "COM07": lambda: _photos(agent, facts),
                "COM10": lambda: _nested_reports(facts),
                "COM11": lambda: _belongs(facts),
                "COM12": lambda: _outside(facts),
                "COM13": lambda: _visual(agent, facts, ("seal",)),
                "COM15": lambda: _critical_structure(facts),
                "CON06": lambda: _summary_count(facts),
                "CON07": lambda: _page_numbers(facts, code="W04"),
                "CON08": _unknown("缺少实际印章/签署主体视觉文字与实验室性质的可靠对齐，未判断真伪。"),
                "CON09": lambda: _date_years(facts),
                "CON10": lambda: _cross_identity(agent, facts),
            }
        )
    return {_source_id(name): operation for name, operation in result.items()}


def _source_id(name):
    return re.sub(r"^(IR|COR|COM|CON)(\d)", r"\1-\2", name)


def _phase(facts):
    value = _optional(facts, ("申报阶段", "项目阶段", "试验阶段"))
    if value is not None and _text(value):
        return "已读取明确申报阶段。"
    filename = facts.audit.file_metadata.get("original_filename", "")
    match = re.search(r"(?:^|[_ -])(DV|PV|OTS|PPAP)(?:[_ .-]|$)", filename, re.I)
    if match:
        list(_cells(facts))
        return "原始文件名存在唯一明确阶段标记，按分则登记为推断阶段。"
    # Test-type DV/PV is not automatically a submission phase.
    list(_cells(facts))
    raise UnknownFact("未读取到明确申报阶段；当前版式尚不能证明报告区填充度可确定阶段，不能把抽取失败升为B03。")


def _internal_contradiction(facts):
    rows = facts.table_rows({"number": ("数值结果", "实际数值", "实测值"), "word": ("文字结论", "试验结论", "判定"), "threshold": ("判定阈值", "要求值")})
    for row in rows:
        if not all(_text(value) for value in row.values()):
            continue
        value = _text(row["number"])
        limits = parse_quality_standard_range(row["threshold"])
        if not _NUM.fullmatch(value) or limits is None:
            raise UnknownFact("同行数值或明确阈值尚不能解析，未判断业务合理性。")
        word = key(row["word"])
        if word not in {"合格", "符合", "通过", "pass", "ok", "不合格", "fail", "ng"}:
            raise UnknownFact("同行文字结论方向尚不能可靠归一化。")
        parsed_value = Decimal(value)
        if not parsed_value.is_finite():
            raise UnknownFact("内部一致性数值不是有限值。")
        inside = Decimal(str(limits[0])) <= parsed_value <= Decimal(str(limits[1]))
        if inside != (word in {"合格", "符合", "通过", "pass", "ok"}):
            raise DefectFact("W06", "同行明确阈值与文字结论互相矛盾；仅核对自述内部关系。")
    # Stage chronology needs an explicit stage definition, not inferred DV/PV order.
    definition = _optional(facts, ("阶段定义", "完成节点定义"))
    if definition is None:
        return "已核对明确数值阈值与文字结论关系；无明确阶段定义时未推定节点时序。"


def _explanations(agent, facts):
    declarations = []
    separate = _optional(facts, ("豁免取消借用说明清单", "豁免说明清单", "借用说明清单"))
    if separate is not None and _text(separate):
        declarations.append(_text(separate))
    group = getattr(agent, "group_context", None)
    if group is not None:
        for other in _peers(agent, facts):
            if facts.audit.part and other.audit.part and _identity(facts.audit.part) != _identity(other.audit.part):
                continue
            declaration = _optional(other, ("豁免取消借用说明清单", "豁免说明清单", "借用说明清单"))
            if declaration is not None and _text(declaration):
                _copy_sources(facts, other)
                declarations.append(_text(declaration))
    return _native_explanations(facts, declarations=declarations)


def _family(facts):
    form = _one(facts, ("表号", "表单编号", "表格编号"))
    expected = _optional(facts, ("模板族系表号", "允许表号"))
    if expected is None:
        raise UnknownFact("已读取表号，但未提供受控模板族系与允许值域对应关系。")
    if _identity(form) not in {_identity(item) for item in re.split(r"[,，;；\n]", str(expected))}:
        raise DefectFact("W04", "表号与原件明确引用的模板族系表号不一致。")
    raise UnknownFact("表号对应已核对；列布局和值域的独立受控模板尚未取得，未以自述作为独立真值。")


def _dictionaries(agent, facts):
    dictionaries, unresolved = _native_dictionaries(facts)
    _dictionary_conflict(dictionaries)
    peers = _peers(agent, facts)
    for other in peers:
        if not _is_trial_original(other):
            continue
        values, missing = _native_dictionaries(other)
        dictionaries.extend(values)
        unresolved.extend(missing)
        _copy_sources(facts, other)
    _dictionary_conflict(dictionaries)
    if unresolved:
        raise UnknownFact("；".join(dict.fromkeys(unresolved)))
    if not dictionaries:
        raise StateFact("S01", "已读取本轮原件，未提供实际试验结果值域字典；不判规则字典是否齐全。")
    if len(set(dictionaries)) > 1:
        raise UnknownFact("原件字典仅存在包含关系，未确认互斥；不把按行限制的子集误判为多套互斥字典。")
    return "已读取本轮原件实际试验结果的原生下拉/明示字典，未发现互斥字典；未替代规则权威枚举。"


def _dictionary_conflict(dictionaries):
    for index, left in enumerate(dictionaries):
        for right in dictionaries[index + 1 :]:
            if left - right and right - left:
                raise DefectFact("W03", "本轮同一实际试验结果字段存在互不兼容的原件字典；规则枚举仍为权威，仅登记差异。")


def _paired_dates(agent, facts):
    own = facts.table_rows({"test": ("试验项目", "项目名称"), "start": ("实际试验开始日期", "试验实际开始日期", "试验开始日期"), "end": ("实际试验结束日期", "试验实际结束日期", "试验结束日期")})
    part = _one(facts, _PART)
    candidates = []
    for other in _peers(agent, facts):
        if _identity(_optional(other, _PART)) == _identity(part):
            candidates.append(other)
    if not candidates:
        raise StateFact("S04-S", "本轮无同件号的DVR日期对端。", warning_codes=("W01",))
    if len(candidates) != 1:
        raise UnknownFact("同件号日期对端有多个，未按顺序或相似名称配对。")
    other = candidates[0]
    own_version, other_version = _optional(facts, _VERSION), _optional(other, _VERSION)
    if own_version is None or other_version is None or _identity(own_version) != _identity(other_version):
        raise UnknownFact("双端版本缺失或不同，不能自动建立日期关联。")
    rows = other.table_rows({"test": ("试验项目", "项目名称"), "start": ("试验开始日期", "实际试验开始日期"), "end": ("试验结束日期", "实际试验结束日期")})
    _copy_sources(facts, other)
    indexed = defaultdict(list)
    for row in rows:
        indexed[key(row["test"])].append(row)
    for row in own:
        matches = indexed[key(row["test"])]
        if len(matches) != 1:
            raise UnknownFact("试验项目未能双端唯一对应。")
        for name in ("start", "end"):
            left, right = normalize_excel_date(row[name]), normalize_excel_date(matches[0][name])
            if left is None or right is None:
                raise UnknownFact("双端试验日期有空值或不能归一化，未记不一致。")
            if left != right:
                raise DefectFact("W06", "同件号、同版本、同试验项目双端日期不一致，已保留双原件。")


def _family_mapping(facts):
    rows = facts.table_rows({"part": ("家族件号", "件号列表", "零件号"), "report": ("报告号列表", "报告编号"), "mapping": ("映射关系", "报告适用件号")})
    for row in rows:
        parts = {key(item) for item in re.split(r"[,，;；、\n]", str(row["part"])) if _text(item)}
        applies = {key(item) for item in re.split(r"[,，;；、\n]", str(row["mapping"])) if _text(item)}
        if not parts or not _text(row["report"]):
            raise UnknownFact("家族件或报告号列表为空，尚不能核对映射。")
        if parts != applies:
            raise DefectFact("W04", "明确列出的件号与报告适用件号不一致；未要求1:1映射。")


def _section_titles(facts):
    headings = defaultdict(list)
    for table, row, column, value in _cells(facts):
        token = key(value)
        for title, names in {
            "cover": {"封面", "报告封面"},
            "summary": {"汇总", "试验汇总", "汇总表"},
            "detail": {"详情", "试验详情", "试验详情页"},
            "sign": {"签署", "签署页", "签批", "签批页"},
        }.items():
            if token in names or key(table["id"]) in names:
                headings[title].append((table["id"], row))
    return headings


def _report_sections(facts):
    headings = _section_titles(facts)
    if not headings:
        raise UnknownFact("未定位到可分段的封面/汇总/详情/签署标记；可能为单页单项目，需核对实际版式。")
    if set(headings) != {"cover", "summary", "detail", "sign"}:
        raise UnknownFact("报告只有部分可定位分段标记，尚不能排除单页单项目或视觉承载区块，未推定B04。")
    try:
        return _summary_count(facts)
    except DefectFact as exc:
        raise DefectFact("B04", "汇总与详情项目不能一一对应。") from exc


def _summary_count(facts):
    summary: list[str] = []
    detail: list[str] = []
    for table in facts.data["tables"]:
        if not any(word in str(table["id"]) for word in ("汇总", "详情", "明细")):
            continue
        saved = facts.data
        facts.data = {**saved, "tables": [table]}
        try:
            rows = facts.table_rows({"test": ("试验项目", "项目名称", "检验项目")})
        finally:
            facts.data = saved
        target = summary if "汇总" in str(table["id"]) else detail
        target.extend(key(row["test"]) for row in rows)
    if not summary or not detail:
        raise UnknownFact("未定位可独立读取的汇总和详情项目范围，未从照片标题推算详情数。")
    if len(summary) != len(detail) or sorted(summary) != sorted(detail):
        raise DefectFact("W04", "汇总项目数或项目标识与详情范围不一致。")
    if len(set(summary)) != len(summary):
        raise UnknownFact("项目名称重复，缺少唯一编号，不能证明汇总与详情一一对应。")


def _photos(agent, facts):
    return _visual(agent, facts, ("before", "during", "after"))


def _visual(agent, facts, fields):
    method = getattr(agent, "_original_visual_facts", None)
    if method is None or Path(facts.audit.file_url).suffix.lower() != ".pdf":
        raise UnknownFact("尚无完整原件的可验证视觉读取；图片对象/标签不能证明实际照片或印章。")
    from audit.engine.rule_engine import GuardStop

    try:
        observed = method()
    except GuardStop as exc:
        raise UnknownFact(str(exc)) from exc
    for source in getattr(agent, "_original_visual_sources", []):
        facts.sources.append(source.model_dump(mode="json") if hasattr(source, "model_dump") else source)
    missing = []
    unresolved = False
    for name in fields:
        value = observed.get(name)
        if value is False:
            missing.append(name)
        elif value is not True:
            unresolved = True
    if missing:
        raise DefectFact("W01", "完整原件未定位到实际对象：" + "、".join(missing) + "；按理想专属规则仅提示缺项。")
    if unresolved:
        raise UnknownFact("视觉读取未明确核实本项实际对象的有/无。")
    return "已从完整PDF原件核实实际对象，未将标签或对象数量当作照片/印章。"


def _nested_reports(facts):
    # A bitmap is not necessarily an embedded report; explicitly identify the
    # marked report region before testing whether it carries readable text.
    found = []
    for table, row, col, value in _cells(facts):
        if key(value) in {"内嵌子报告", "扫描子报告", "子报告"}:
            found.append((table, row, col))
    if not found:
        raise UnknownFact("未从文本定位内嵌子报告标记；是否存在未标记贴图子报告需完整原件视觉读取。")
    for table, row, col in found:
        if table.get("visual_evidence"):
            raise UnknownFact("已定位子报告及图片对象，尚无局部图文可靠转译，不能把不可读技术状态归责供应商。")
        if col == table["cols"] or not _text(facts.read(table, row, col + 1)):
            raise UnknownFact("子报告标记没有可读取的关联正文，需核对同源替代载体。")
    return "已定位明确标记的文字子报告正文；未把未标记视觉区域判为已读。"


def _belongs(facts):
    content = " ".join(_text(value) for _, _, _, value in _cells(facts))
    title = any(word in content for word in ("试验报告", "实验报告", "验证报告", "检测报告"))
    if title or (_optional(facts, _REPORT) is not None and _optional(facts, ("试验项目", "试验类型")) is not None):
        return "已定位报告类型特征；结构缺项由COM-05独立核对。"
    raise UnknownFact("未定位明确报告特征；不能因抽取不到字段就断言原件不属本项。")


def _critical_structure(facts):
    # Number, photos, verdict, sample count and seal have their own criteria.
    # This fallback must not invent a new universal report template.
    test = _optional(facts, ("试验项目", "项目名称"))
    method = _optional(facts, ("试验标准", "试验方法", "试验标准/方法"))
    if test is None or method is None:
        raise StateFact("S05", "未定位兜底关键栏位，需先核对客户受控模板及本报告适用结构，未直接归为供应商B05。")
    return "已定位试验项目与方法关键栏；编号、照片、结论、数量和印章由专条分别核对。"


def _inspection_seal(agent, facts):
    if Path(facts.audit.file_url).suffix.lower() != ".pdf":
        raise UnknownFact("Office尚无完整原件视觉读取，不能以图像对象或盖章标签证明实际印章。")
    import json

    from audit.checkers.v4.visual_facts import VisualFactsUnavailable, read_original_pdf_visual_facts

    sources = []

    def witness(locator, excerpt, *, visual=False):
        sources.append({"kind": "visual" if visual else "structure", "locator": json.dumps({**locator, "file_version_id": facts.audit.audit_id}), "excerpt": excerpt})

    try:
        observed = read_original_pdf_visual_facts(
            facts.audit,
            {"seal": "完整原件中存在实际印章；预印盖章标签、图标、红色方块或公司标识不算。"},
            witness,
            operation="li_auto_inspection_precheck_seal",
        )
    except VisualFactsUnavailable as exc:
        raise UnknownFact(str(exc)) from exc
    facts.sources.extend(sources)
    if observed["seal"] is None:
        raise UnknownFact("实际印章视觉事实未核实。")
    if observed["seal"] is False:
        raise DefectFact("W01", "完整原件未见实际印章，仅提示缺项，不作真伪或合规判断。")
    return "已在完整PDF原件核实实际印章存在。"


def _trial_rows(facts, aliases):
    """Original DVP two-tier headers, with no positional rule-number binding."""
    requested = set(aliases)
    aliases = {"test": ("试验项目", "项目名称"), **aliases}
    result = []
    for table in facts.data["tables"]:
        if any(word in str(table["id"]) for word in ("填写说明", "填写指南")):
            continue
        located = None
        for top in range(1, min(table["rows"], 80) + 1):
            found = {}
            anchors = []
            for name, names in aliases.items():
                candidates = {}
                for row in range(top, min(top + 2, table["rows"]) + 1):
                    for column in range(1, table["cols"] + 1):
                        cell = effective(table, row, column)
                        if key(cell.get("value")) in {key(value) for value in names}:
                            candidates[cell["anchor"]] = coordinate_to_tuple(cell["anchor"])
                if len(candidates) == 1:
                    anchor = next(iter(candidates.values()))
                    found[name] = anchor[1]
                    anchors.append(anchor)
            if len(found) != len(aliases):
                continue
            bottom = max(row for row, column in anchors)
            for row, column in anchors:
                facts.read(table, row, column)
                bottom = max([bottom] + [r2 for r1, c1, r2, c2 in table.get("merges", []) if r1 <= row <= r2 and c1 <= column <= c2])
            located = bottom, found
            break
        if located is None:
            continue
        bottom, columns = located
        for row in range(bottom + 1, table["rows"] + 1):
            cells = {name: effective(table, row, col) for name, col in columns.items()}
            if all(value.get("state") == "known" and not _text(value.get("value")) for value in cells.values()):
                continue
            if len({value["anchor"] for value in cells.values()}) == 1 and re.fullmatch(r"[一二三四五六七八九十0-9]+[、.．].*类试验", _text(cells["test"].get("value"))):
                continue
            if any(_text(value.get("value")).startswith(("职责说明", "实验室选择说明", "试验报告书写要求", "填表说明")) for value in cells.values()):
                break
            if not _text(cells["test"].get("value")):
                raise UnknownFact("试验项目主键为空但同行有数据，不能跳过未归属行。")
            record = {name: facts.read(table, row, col, vertical_only=True) for name, col in columns.items()}
            result.append({name: value for name, value in record.items() if name in requested})
    if not result:
        raise UnknownFact("未定位完整试验项目及本项的实际原件数据列。")
    return result


_RESULT_HEADERS = {"实际试验结果", "试验结果"}
_EXECUTION_HEADERS = {"试验项计划执行状态", "计划执行状态", "试验执行状态", "豁免取消借用说明", "豁免/取消/借用说明", "豁免说明", "取消说明", "借用说明"}
_TRIAL_HEADERS = {
    "序号",
    "试验项目",
    "试验要求",
    "试验标准/方法",
    "试验类型",
    "完成节点",
    "试验地点",
    "试验样本描述",
    "数量",
    "状态",
    "外观属性",
    "计划开始",
    "计划结束",
    "试验实际开始日期",
    "试验实际结束日期",
    "实际试验结果",
    "试验报告编号",
    "备注",
    *_EXECUTION_HEADERS,
}


def _native_book(facts):
    from openpyxl import load_workbook

    from audit.document_tools import ReadLimits, preflight

    if Path(facts.audit.file_url).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise UnknownFact("当前原件不是可读取原生下拉、批注和底色的Excel载体，未推定这些对象不存在。")
    if not hasattr(facts, "_li_native_book"):
        path = Path(facts.audit.file_url)
        preflight(path, ReadLimits())
        facts._li_native_book = load_workbook(path, data_only=False, keep_links=False)
    return facts._li_native_book


def _is_trial_original(facts):
    return any(key(cell.get("value")) == key("试验项目") for table in facts.data["tables"] for cell in table["cells"].values())


def _native_regions(facts):
    """Read the actual two-tier trial header without assuming column numbers."""
    workbook = _native_book(facts)
    result = []
    for table in facts.data["tables"]:
        if table["id"] not in workbook.sheetnames or any(word in table["id"] for word in ("填写说明", "填写指南")):
            continue
        for top in range(1, min(table["rows"], 80) + 1):
            tests = [column for column in range(1, table["cols"] + 1) if key(effective(table, top, column).get("value")) == key("试验项目")]
            if not tests:
                continue
            anchors = {effective(table, top, column)["anchor"] for column in tests}
            if len(anchors) != 1:
                raise UnknownFact("同一行有多个试验项目表头，无法唯一定位原生字典/说明范围。")
            test_col = coordinate_to_tuple(next(iter(anchors)))[1]
            columns: dict[str, set[int]] = {}
            bottom = top
            for row in range(top, min(top + 1, table["rows"]) + 1):
                for column in range(1, table["cols"] + 1):
                    cell = effective(table, row, column)
                    token = key(cell.get("value"))
                    if token not in {key(name) for name in _TRIAL_HEADERS}:
                        continue
                    anchor_row, anchor_col = coordinate_to_tuple(cell["anchor"])
                    columns.setdefault(token, set()).add(anchor_col)
                    facts.read(table, anchor_row, anchor_col)
                    bottom = max([bottom, anchor_row] + [r2 for r1, c1, r2, c2 in table.get("merges", []) if r1 <= anchor_row <= r2 and c1 <= anchor_col <= c2])
            if not any(key(name) in columns for name in _RESULT_HEADERS):
                continue
            if any(len(values) != 1 for values in columns.values()):
                raise UnknownFact("业务字段有多个原生表头位置，需先确认范围。")
            flattened = {name: next(iter(values)) for name, values in columns.items()}
            rows = []
            for row in range(bottom + 1, table["rows"] + 1):
                project = effective(table, row, test_col)
                if not _text(project.get("value")) and project.get("state") == "known":
                    continue
                text = _text(project.get("value"))
                if key(text) in {key(name) for name in ("颜色说明", "底色说明", "颜色图例", "底色图例", "豁免取消借用说明清单", "豁免说明清单", "借用说明清单")}:
                    break
                if text.startswith(("职责说明", "实验室选择说明", "试验报告书写要求", "填表说明", "编制", "审核", "批准")):
                    break
                if re.fullmatch(r"[一二三四五六七八九十0-9]+[、.．].*类试验", text):
                    continue
                facts.read(table, row, test_col, vertical_only=True)
                rows.append(row)
            result.append((workbook[table["id"]], table, flattened, bottom, rows))
            break
    if not result:
        raise UnknownFact("未定位完整DVP试验项目及实际试验结果原生表头，不能猜字典或说明归属。")
    return result


def _native_witness(facts, table, row, column, attribute, value):
    """Attach native metadata to its actual original cell, including blank cells."""
    import json

    from audit.evidence import cell_locator

    locator = {"file_version_id": facts.audit.audit_id, **cell_locator(facts.data["source"]["format"], table["id"], row, column).model_dump(mode="json")}
    facts.sources.append({"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False), "excerpt": (attribute + ": " + str(value))[:500]})


def _validation_options(facts, workbook, worksheet, table, formula):
    """Read literal/static lists only. Never evaluate formulas or follow links."""
    from openpyxl.utils.cell import range_boundaries

    text = _text(formula)
    if len(text) > 32_767:
        raise UnknownFact("原生下拉表达式超过有界读取长度，未求值或截断为通过。")
    if text.startswith('"') and text.endswith('"'):
        options = [value.replace('""', '"').strip() for value in text[1:-1].split(",")]
        if not all(options) or len(options) > 10_000:
            raise UnknownFact("原生下拉字典含空选项或超过一万项预算，不能可靠还原值域。")
        return frozenset(_identity(value) for value in options)
    reference = text.removeprefix("=").strip()
    if any(mark in reference for mark in ("[", "]", "(", ")", ",", ";")):
        raise UnknownFact("原生下拉使用动态公式或外部引用，未执行公式或读取外部资料。")
    if "!" in reference:
        sheet_name, address = reference.rsplit("!", 1)
        sheet_name = sheet_name.strip("'").replace("''", "'")
        destinations = [(sheet_name, address)]
    elif re.fullmatch(r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?", reference, re.I):
        destinations = [(worksheet.title, reference)]
    else:
        definition = worksheet.defined_names.get(reference) or workbook.defined_names.get(reference)
        if definition is None or definition.type != "RANGE":
            raise UnknownFact("原生下拉名称未解析为本原件的静态单元格范围。")
        destinations = list(definition.destinations)
    if len(destinations) != 1:
        raise UnknownFact("原生下拉指向多个范围，未猜测选项拼接顺序。")
    sheet_name, address = destinations[0]
    if sheet_name not in workbook.sheetnames:
        raise UnknownFact("原生下拉的字典工作表在本原件中不存在。")
    try:
        c1, r1, c2, r2 = range_boundaries(address)
    except ValueError as exc:
        raise UnknownFact("原生下拉的范围表达式不能可靠读取。") from exc
    if any(value is None for value in (c1, r1, c2, r2)) or (c2 - c1 + 1) * (r2 - r1 + 1) > 10_000:
        raise UnknownFact("原生下拉字典范围未界定或超过一万单元格读取预算。")
    source_table = next((item for item in facts.data["tables"] if item["id"] == sheet_name), None)
    if source_table is None or r2 > source_table["rows"] or c2 > source_table["cols"]:
        raise UnknownFact("原生下拉字典范围超出已冻结读取的原件范围。")
    options = []
    for row in range(r1, r2 + 1):
        for column in range(c1, c2 + 1):
            value = facts.read(source_table, row, column)
            if not _text(value):
                continue
            options.append(_identity(value))
    if not options:
        raise UnknownFact("原生下拉字典的静态范围没有可读取选项。")
    return frozenset(options)


def _native_dictionaries(facts):
    workbook = _native_book(facts)
    dictionaries = []
    unresolved = []
    for worksheet, table, columns, bottom, rows in _native_regions(facts):
        result_cols = {columns[key(name)] for name in _RESULT_HEADERS if key(name) in columns}
        validations = list(worksheet.data_validations.dataValidation)
        if len(validations) > 4096:
            raise UnknownFact("原生数据验证超过有界读取预算。")
        for validation in validations:
            if validation.type != "list":
                continue
            for region in validation.sqref.ranges:
                overlap = result_cols & set(range(region.min_col, region.max_col + 1))
                if not overlap or region.max_row <= bottom:
                    continue
                column = min(overlap)
                row = max(region.min_row, bottom + 1)
                if rows and not any(region.min_row <= item <= region.max_row for item in rows):
                    continue
                _native_witness(facts, table, min(row, table["rows"]), column, "原生数据验证", f"范围={region}; 选项={validation.formula1}")
                try:
                    dictionaries.append(_validation_options(facts, workbook, worksheet, table, validation.formula1))
                except UnknownFact as exc:
                    unresolved.append(str(exc))
    # Explicit dictionary text is also actual source data; blank or missing
    # dictionaries are not rule-side defects.
    labels = {key(name) for name in ("允许结论取值", "判定字典")}
    if any(key(cell.get("value")) in labels for table in facts.data["tables"] for cell in table["cells"].values()):
        for value in facts.values(("允许结论取值", "判定字典"), required=False):
            if _text(value):
                dictionaries.append(frozenset(_identity(item) for item in re.split(r"[,，;；/、\n]", str(value)) if _text(item)))
    return dictionaries, unresolved


def _explanation_terms(value):
    text = _text(value)
    return {term for term in ("豁免", "取消", "借用") if term in text and not re.search(r"(?:不|未|无需|无须)" + term, text)}


def _legend(facts):
    mapping: dict[str, str] = {}
    value = _optional(facts, ("颜色说明", "底色说明", "颜色图例", "底色图例"))
    if value is None:
        return mapping
    colors = {"红色": "FF0000", "黄色": "FFFF00", "绿色": "00FF00", "蓝色": "0000FF"}
    for color, term in re.findall(r"(红色|黄色|绿色|蓝色|[0-9A-Fa-f]{6})\s*(?:=|:|：|表示|代表)\s*(豁免|取消|借用)", _text(value)):
        mapping[colors.get(color, color.upper())] = term
    return mapping


def _native_explanations(facts, *, declarations=()):
    regions = _native_regions(facts)
    if not any(rows for worksheet, table, columns, bottom, rows in regions):
        raise UnknownFact("未取得真实试验项目行，不能按空范围判定说明位置合规。")
    legend = _legend(facts)
    unresolved_color = False
    for worksheet, table, columns, bottom, rows in regions:
        left, right = min(columns.values()), max(columns.values())
        expected_columns = {columns[key(name)] for name in _EXECUTION_HEADERS if key(name) in columns}
        note_columns = {columns[key(name)] for name in ("备注",) if key(name) in columns}
        explicit_by_project = {}
        project_keys = [_identity(facts.read(table, row, columns[key("试验项目")])) for row in rows]
        if declarations and len(set(project_keys)) != len(project_keys):
            raise UnknownFact("存在同名试验项目，独立说明清单缺少可唯一对应的试验编号，不能全局豁免。")
        for row in rows:
            explicit = set()
            project = _text(facts.read(table, row, columns[key("试验项目")]))
            for declaration in declarations:
                for entry in re.split(r"[\n;；]", declaration):
                    # Match an explicit list key; a substring such as 振动2
                    # must not exempt 振动, nor a different trial in this file.
                    parts = re.split(r"[:：]", entry, maxsplit=1)
                    if len(parts) == 2 and _identity(parts[0]) == _identity(project):
                        explicit |= _explanation_terms(parts[1])
            for column in expected_columns:
                explicit |= _explanation_terms(facts.read(table, row, column))
            explicit_by_project[_identity(project)] = explicit
            for column in range(1, table["cols"] + 1):
                cell = worksheet.cell(row, column)
                if _native_metadata_pair(table, row, column):
                    continue
                comment_terms = _explanation_terms(cell.comment.text) if cell.comment else set()
                if comment_terms - explicit:
                    _native_witness(facts, table, row, column, "原生批注", cell.comment.text)
                    raise DefectFact("W03", "豁免/取消/借用只定位于原生批注，未提供可按业务栏读取的说明或独立清单。")
                text_terms = _explanation_terms(facts.read(table, row, column))
                if text_terms - explicit and (column in note_columns or column < left or column > right):
                    _native_witness(facts, table, row, column, "备注/表外说明", cell.value)
                    raise DefectFact("W03", "豁免/取消/借用只定位于备注或表外单元格，未提供独立说明清单。")
            colors = set()
            for column in range(left, right + 1):
                cell = worksheet.cell(row, column)
                if cell.fill.patternType != "solid":
                    continue
                color = cell.fill.fgColor
                if color.type == "rgb":
                    rgb = str(color.rgb)[-6:].upper()
                    if rgb not in {"000000", "FFFFFF"} or rgb in legend:
                        colors.add(rgb)
                        _native_witness(facts, table, row, column, "原生实心底色", rgb)
                else:
                    unresolved_color = True
            for color in colors:
                if color in legend and legend[color] not in explicit:
                    raise DefectFact("W03", "原件图例明确声明底色承载豁免/取消/借用，但业务栏未给出对应说明。")
                if color not in legend:
                    unresolved_color = True
            for formatting in worksheet.conditional_formatting:
                if any(region.min_row <= row <= region.max_row and region.min_col <= right and region.max_col >= left for region in formatting.sqref.ranges):
                    raise UnknownFact("试验行存在条件格式，当前未计算其视觉结果，不能推定底色是否承载状态。")
        projects = {_identity(facts.read(table, row, columns[key("试验项目")])) for row in rows}
        for row in range(bottom + 1, table["rows"] + 1):
            if row in rows:
                continue
            for column in range(1, table["cols"] + 1):
                if _native_metadata_pair(table, row, column):
                    continue
                cell = worksheet.cell(row, column)
                candidates = [("原生表外批注", cell.comment.text)] if cell.comment else []
                raw = effective(table, row, column)
                if raw.get("state") == "known" and _text(raw.get("value")):
                    candidates.append(("原生表外说明块", _text(raw.get("value"))))
                for attribute, text in candidates:
                    terms = _explanation_terms(text)
                    if not terms:
                        continue
                    _native_witness(facts, table, row, column, attribute, text)
                    parts = re.split(r"[:：]", str(text), maxsplit=1)
                    if len(parts) == 2 and _identity(parts[0]) in projects:
                        if terms <= explicit_by_project.get(_identity(parts[0]), set()):
                            continue
                        raise DefectFact("W03", "明确对应试验项目的豁免/取消/借用说明只位于表外说明块或批注。")
                    raise UnknownFact("表外原生文字或批注含豁免/取消/借用描述，但未明确归属试验项目，未推定为供应商问题。")
    if unresolved_color:
        raise UnknownFact("已读取原生底色；未提供可唯一解释该颜色的状态图例，未按红色等颜色猜测取消或豁免。")
    return "已逐项读取原生批注、备注、表外单元格及静态底色，未定位仅藏于这些位置的豁免/取消/借用说明。"


def _native_metadata_pair(table, row, column):
    labels = {key(value) for value in ("颜色说明", "底色说明", "颜色图例", "底色图例", "豁免取消借用说明清单", "豁免说明清单", "借用说明清单")}
    if key(effective(table, row, column).get("value")) in labels:
        return True
    return column > 1 and key(effective(table, row, column - 1).get("value")) in labels
