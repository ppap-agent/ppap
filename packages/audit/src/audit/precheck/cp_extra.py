"""Remaining fixed CP preparation predicates; no engineering verdict generation."""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.evidence import cell_locator
from audit.precheck.facts import DefectFact, StateFact, UnknownFact, key
from audit.precheck.pairs import history, identity, merge_sources, optional_label, pair

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_UNIT = r"N[·.]?m|Nm|MPa|kPa|Pa|mm|cm|µm|μm|kg|mg|°C|℃|min|sec|rpm|mL|min|N|g|s|h|V|A|%"
_DIMENSIONS = {
    "Nm": "torque",
    "N·m": "torque",
    "N.m": "torque",
    "N": "force",
    "MPa": "pressure",
    "kPa": "pressure",
    "Pa": "pressure",
    "mm": "length",
    "cm": "length",
    "µm": "length",
    "μm": "length",
    "kg": "mass",
    "mg": "mass",
    "g": "mass",
    "s": "time",
    "h": "time",
    "min": "time",
    "sec": "time",
    "°C": "temperature",
    "℃": "temperature",
    "V": "voltage",
    "A": "current",
    "%": "ratio",
    "rpm": "rotation",
    "mL": "volume",
}


def _text(value):
    return unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()


def _quantities(value):
    text = _text(value)
    unknown = [match[1] for match in re.finditer(rf"(?:{_NUMBER})\s*([A-Za-zµμ℃°]+)", text) if match[1] not in _DIMENSIONS]
    if unknown:
        raise UnknownFact("规格含尚未识别的单位表达，未把它当作无单位或已核实。")
    return [(Decimal(match[1]), match[2]) for match in re.finditer(rf"({_NUMBER})\s*({_UNIT})(?![A-Za-z])", text)]


def _stage(value):
    text = _text(value)
    aliases = {"样件": "prototype", "样品": "prototype", "试生产": "pilot", "试量产": "pilot", "量产": "production", "生产": "production", "SOP": "production"}
    english = {"prototype": "prototype", "pre-launch": "pilot", "preproduction": "pilot", "production": "production", "sop": "production"}
    if text.casefold() in english:
        return [english[text.casefold()]]
    if text in aliases:
        return [aliases[text]]
    selected = re.findall(r"[☑☒✓✔√■]\s*(试生产|试量产|量产|生产|样件|样品|SOP)|(试生产|试量产|量产|生产|样件|样品|SOP)\s*[:：]?\s*[☑☒✓✔√■]", text)
    return [aliases[left or right] for left, right in selected]


def _version(value):
    text = identity(value)
    if not re.fullmatch(r"[a-z]*\d+(?:[./-]\d+)*|[a-z]", text):
        raise UnknownFact("履历版本未使用可确定顺序的数字版本或单字母版本，需确认。")
    return tuple((0, int(token)) if token.isdecimal() else (1, token) for token in re.findall(r"\d+|[a-z]+", text))


def additional_operations(facts, customer, group):
    operations = {}

    def physical_rows():
        fields = [field for field in ("product", "process") if field in facts.columns]
        if not fields:
            raise UnknownFact("物理特性名称未定位，不能猜测规格的物理量纲。")
        return facts.values(*fields, "specification")

    def missing_units():
        for row in physical_rows():
            name = " ".join(str(value or "") for field, value in row.items() if field != "specification")
            spec = _text(row["specification"])
            if any(token in name for token in ("尺寸", "长度", "直径", "孔径", "力", "扭矩", "温度", "时间", "压力", "质量", "重量")) and re.search(r"\d", spec) and not _quantities(spec):
                raise DefectFact("W03", "数值规格未标物理单位；本项只记警告，不判断规格是否合理。")

    operations["C-04"] = missing_units

    def physical_domain():
        for row in physical_rows():
            spec = _text(row["specification"])
            quantities = _quantities(spec)
            if re.search(r"\d", spec) and not quantities:
                raise UnknownFact("规格有数值但物理单位尚未确认，不能以默认单位判断正负。")
            if "±" in spec or "[" in spec or "~" in spec:
                raise UnknownFact("组合公差与物理量取值尚未逐个对应，未把负公差当成负物理量。")
            for number, unit in quantities:
                dimension = _DIMENSIONS[unit]
                if dimension in {"force", "pressure", "mass", "time"} and number < 0 or dimension == "ratio" and not 0 <= number <= 100:
                    raise DefectFact("B06", "卷面力/压力/质量/时间为负或百分比超出0~100的物理范围。")

    operations["C-05"] = physical_domain

    def mixed_dimensions():
        for row in facts.values("specification"):
            quantities = _quantities(row["specification"])
            if len({_DIMENSIONS[unit] for _, unit in quantities}) >= 2:
                raise DefectFact("W03", "同一规格单元格混填两个及以上物理量纲，应分别说明。")

    operations["C-06"] = mixed_dimensions

    def displayed_errors():
        unreadable = False
        for original in facts.data["tables"]:
            for address, raw in original["cells"].items():
                value = str(raw.get("value") or "")
                if re.search(r"#{4,}|#(?:REF!|VALUE!|N/A|DIV/0!|NAME\?|NUM!|NULL!)", value):
                    match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                    if not match:
                        raise UnknownFact("报错显示所在原件坐标尚不能解析。")
                    column = 0
                    for letter in match[1]:
                        column = column * 26 + ord(letter) - 64
                    locator = {"file_version_id": facts.audit.audit_id, **cell_locator(facts.data["source"]["format"], original["id"], int(match[2]), column).model_dump(mode="json")}
                    facts.sources.append({"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False), "excerpt": value[:500]})
                    raise DefectFact("W03", "原件实际显示掩码或Excel错误文本；尚未把缺少缓存的公式当作报错显示。")
                if raw.get("state") != "known":
                    unreadable = True
                if value:
                    match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                    if match and len(facts.sources) < 10:
                        column = 0
                        for letter in match[1]:
                            column = column * 26 + ord(letter) - 64
                        facts.read(original, int(match[2]), column)

        if unreadable:
            raise UnknownFact("部分原件单元格没有可读取显示值，不能声明全文件无报错。")

    operations["C-07"] = displayed_errors

    def stage_value():
        return facts.label(facts.table, ("控制计划类型", "CP类型"), facts.header)

    def stage_count():
        value = stage_value()
        selected = _stage(value)
        if len(selected) != 1:
            if "□" in str(value) or len(selected) > 1:
                raise DefectFact("B05", "控制计划阶段选项未唯一勾选。")
            raise UnknownFact("阶段标注未能解析为一个已选阶段，须确认原件标记。")

    operations["C-11"] = stage_count

    def stage_consistency():
        selected = _stage(stage_value())
        if len(selected) != 1:
            raise UnknownFact("表头阶段尚未唯一确定，先确认阶段标记。")
        filename = facts.audit.file_metadata.get("original_filename") or Path(facts.audit.file_url).name
        declared = []
        for value in [filename, *[effective(facts.table, row, column).get("value") for row in range(1, min(facts.header, 4)) for column in range(1, facts.table["cols"] + 1)]]:
            text = _text(value)
            if "试生产" in text or "试量产" in text:
                declared.append("pilot")
            elif "量产" in text or re.search(r"\bSOP\b", text, re.I):
                declared.append("production")
            elif "样件" in text:
                declared.append("prototype")
        if any(stage != selected[0] for stage in declared):
            raise DefectFact("B06", "文件名或封面明确阶段声明与已选阶段不一致。")

    operations["C-12"] = stage_consistency

    def history_header():
        located, data, dates = history(facts)
        if "version" not in located[2]:
            raise UnknownFact("履历版本列未定位，不能推定最高版本。")
        versions = [_version(row["version"]) for row in data]
        latest = max(range(len(versions)), key=lambda index: (versions[index], dates[index]))
        header_version = facts.label(facts.table, ("版本", "版本号", "Rev."), facts.header)
        header_date = facts.label(facts.table, ("修订日期", "受控日期", "创建日期"), facts.header)
        normalized = normalize_excel_date(header_date)
        if normalized is None:
            raise UnknownFact("表头修订日期尚未核实为有效日期。")
        if _version(header_version) != versions[latest] or normalized != dates[latest]:
            raise DefectFact("W06", "表头版本/修订日期与履历最高版本对应记录不一致。")

    operations["C-14"] = history_header

    def history_processes():
        located, data, _ = history(facts)
        actual = {identity(row["number"]) for row in facts.values("number")}
        if "operation" not in located[2]:
            if "change" not in located[2]:
                raise UnknownFact("履历未提供可抽取工序编号的字段。")
            references = [match for row in data for match in re.findall(r"(?:工序|过程|OP)\s*([0-9]+(?:[.-][0-9]+)*)", str(row["change"] or ""), re.I)]
            if not references:
                raise StateFact("S01", "履历未明确引用工序编号，本项无引用对象。")
            actual = {re.sub(r"^op", "", value, flags=re.I) for value in actual}
        else:
            references = [identity(row["operation"]) for row in data if identity(row["operation"])]
        if any(identity(value) not in actual for value in references):
            raise DefectFact("B06", "履历明确引用的工序编号在正文中不存在。")

    operations["C-15"] = history_processes

    def history_order():
        located, data, dates = history(facts)
        if any(after <= before for before, after in zip(dates, dates[1:])):
            raise DefectFact("W06", "履历日期重复或倒序，时间序列不自洽。")
        if "version" in located[2]:
            versions = [_version(row["version"]) for row in data]
            if any(after < before for before, after in zip(versions, versions[1:])):
                raise DefectFact("W06", "履历版本推进方向与日期顺序相反。")

    operations["C-16"] = history_order

    def destructive_all():
        for row in facts.values("product", "process", "measurement", "sample"):
            content = " ".join(str(row[field] or "") for field in ("product", "process", "measurement"))
            if any(word in content for word in ("破坏", "解剖", "金相", "盐雾", "爆破", "拉脱", "撕裂")) and _text(row["sample"]).replace(" ", "") in {"100%", "全检", "全部"}:
                raise DefectFact("B06", "破坏性检验项目与100%/全检样本容量同时出现，卷面要求矛盾。")

    operations["C-21"] = destructive_all

    def attribute_chart():
        for row in facts.values("product", "process", "measurement", "control"):
            content = " ".join(str(row[field] or "") for field in ("product", "process", "measurement"))
            if any(word in content for word in ("目视", "通止规", "塞规", "有无判定")) and any(word in str(row["control"] or "") for word in ("均值极差图", "单值移动极差图", "过程能力指数")):
                raise DefectFact("B06", "属性型特性使用了卷面计量型控制图/过程能力指数。")

    operations["C-22"] = attribute_chart

    def subgroup():
        for row in facts.values("sample", "control"):
            if re.fullmatch(r"1(?:\.0+)?\s*(?:件|pcs)?(?:/次)?", _text(row["sample"]), re.I) and any(word in str(row["control"] or "") for word in ("子组极差", "均值极差图", "Xbar-R")):
                raise DefectFact("B06", "样本容量为1件，却要求子组极差控制图。")

    operations["C-23"] = subgroup

    def process_coverage():
        values = [str(row["name"] or "") for row in facts.values("name")]
        text = " ".join(values)
        required = [("来料", "进货", "首道加工"), ("最终检验", "终检", "出货检验"), ("包装",), ("标识",)]
        if any(not any(word in text for word in aliases) for aliases in required):
            raise DefectFact("W01", "工序名称中未发现全部来料/首道、最终检验、包装与标识环节，请确认本件适用范围。")

    operations["M-12"] = process_coverage

    def page_count():
        declared = []
        for _, _, original, header, _, _ in facts.layouts.values():
            value = optional_label(facts, original, ("总页数", "页数"), header)
            if value is not None:
                declared.append(_text(value))
        if not declared or any(not re.fullmatch(r"\d+", text) for text in declared):
            raise UnknownFact("声明页数尚未全部解析为整数。")
        if facts.data["source"]["format"] == ".pdf":
            actual = len(facts.data.get("pages") or [])
        elif facts.data["source"]["format"] == ".docx":
            raise UnknownFact("Word分页依赖排版，未用表格数量猜测实际页数。")
        else:
            raise UnknownFact("Excel实际打印页数取决于分页和打印配置，未按工作表数量替代页数。")
        if any(int(text) != actual for text in declared):
            raise DefectFact("W06", "原件声明总页数与实际PDF页面数不闭合。")

    operations["M-13"] = page_count

    def controlled():
        phase = optional_label(facts, facts.table, ("控制计划类型", "CP类型"), facts.header)
        if phase is None:
            raise UnknownFact("UAES量产/SOP阶段尚未明确，受控章条件项不能按文件名推定。")
        stages = _stage(phase)
        if len(stages) != 1:
            raise UnknownFact("UAES阶段不能唯一确认，须先核对适用受控状态。")
        if stages[0] != "production":
            raise StateFact("S02", "本原件明确非量产/SOP，受控章条件项关闭。")
        texts = []
        for original in facts.data["tables"]:
            for address, item in original["cells"].items():
                if item.get("state") != "known":
                    raise UnknownFact("部分原件文字不能读取，受控状态尚未核实。")
                if item.get("value"):
                    texts.append(str(item["value"]))
                    if "受控" in str(item["value"]) or any(word in str(item["value"]) for word in ("作废", "无效")):
                        match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                        if match:
                            column = 0
                            for letter in match[1]:
                                column = column * 26 + ord(letter) - 64
                            facts.read(original, int(match[2]), column)
        negative = [text for text in texts if re.fullmatch(r"(?:本文件|本表|文件状态)?[：:\s]*(?:已)?(?:非受控|作废|无效)", text.strip())]
        positive = [text for text in texts if text.strip() in {"受控", "受控章", "受控文件"} or re.fullmatch(r"[^\s]{2,40}公司受控章", text.strip())]
        if negative:
            raise DefectFact("B05", "原件存在明确非受控/作废/无效标识，优先于正向受控文字。")
        if not positive:
            raise UnknownFact("文字层未确认受控标识；须继续核对图像/矢量印章，未把受控日期标签当作印章。")

    if customer == "UAES":
        operations["M-14"] = controlled

    def page_numbers():
        if facts.data["source"]["format"] != ".pdf":
            raise UnknownFact("当前载体无已解析实际分页；不按行号/工作表序号当作页码。")
        values = []
        for page in facts.data.get("pages") or []:
            found = []
            for index, block in enumerate(page["blocks"], 1):
                text = _text(block["text"])
                match = re.fullmatch(r"第\s*(\d+)\s*页(?:\s*共\s*\d+\s*页)?|Page\s*(\d+)(?:\s*(?:of|/)\s*\d+)?", text, re.I)
                bbox = block.get("bbox") or []
                if not match and len(bbox) == 4 and (bbox[1] > page["height"] * 0.85 or bbox[3] < page["height"] * 0.15):
                    match = re.fullmatch(r"(\d+)\s*/\s*\d+", text)
                if match:
                    found.append(int(next(value for value in match.groups() if value is not None)))
                    locator = {"kind": "pdf_block", "page": page["page"], "block": index, "file_version_id": facts.audit.audit_id}
                    facts.sources.append({"kind": "structure", "locator": json.dumps(locator), "excerpt": text[:500]})
            if len(set(found)) != 1:
                raise UnknownFact("实际PDF页面未定位到唯一明确页码，不用正文分数或表格行号代替页码。")
            values.append(found[0])
        if not values:
            raise UnknownFact("没有可核实实际PDF页面，不能按空序列通过。")
        if values != list(range(1, len(values) + 1)):
            raise DefectFact("B06", "原件页码存在重复、断层、倒序或越界。")

    operations["X-04"] = page_numbers

    def filename_notice():
        filename = str(facts.audit.file_metadata.get("original_filename") or Path(facts.audit.file_url).name)
        facts.values("name")
        if any(word in filename for word in ("错误", "不完全对", "副本")):
            raise DefectFact("W03", "原始文件名带错误/副本提示，须确认是否为本次有效提交版本。")
        stage_consistency()

    operations["X-06"] = filename_notice

    def metadata():
        fields = {"part": ("零件号", "零件编号", "物料编号"), "name": ("零件名称", "物料名称"), "version": ("版本", "版本号", "Rev."), "stage": ("控制计划类型", "CP类型")}
        observed = []
        for _, _, original, header, _, _ in facts.layouts.values():
            record = {}
            for field, aliases in fields.items():
                values = []
                for row in range(1, header):
                    for column in range(1, original["cols"]):
                        if key(effective(original, row, column).get("value")) in {key(alias) for alias in aliases}:
                            facts.read(original, row, column)
                            right = column + 1
                            for r1, c1, r2, c2 in original.get("merges", []):
                                if r1 <= row <= r2 and c1 <= column <= c2:
                                    right = c2 + 1
                                    break
                            if right <= original["cols"]:
                                values.append(facts.read(original, row, right))
                record[field] = values
            observed.append(record)
        return observed

    def fixed_headers():
        data = metadata()
        seen = False
        for field in ("part", "name", "version"):
            values = {identity(value) for record in data for value in record[field] if identity(value)}
            seen |= bool(values)
            if len(values) > 1:
                raise DefectFact("B06", "同一控制计划文件的表头零件号、名称或版本出现不一致。")
        if not seen:
            raise UnknownFact("未读取到可比较表头元信息，未按空集合宣称一致。")

    operations["X-01"] = fixed_headers

    def repeated_stages():
        data = metadata()
        values = [_stage(value) for record in data for value in record["stage"]]
        if not values or any(len(value) != 1 for value in values):
            raise UnknownFact("原件各处阶段尚未全部唯一识别，不能宣称全篇一致。")
        if len({value[0] for value in values}) > 1:
            raise DefectFact("B06", "同一原件各处表头已选阶段不一致。")

    operations["X-02"] = repeated_stages

    def worksheet_consistency():
        if len(facts.layouts) == 1:
            raise StateFact("S01", "原件只定位到一个控制计划表，无跨表对象。")
        fixed_headers()
        repeated_stages()

    operations["X-03"] = worksheet_consistency

    def references():
        found = []
        for field in ("reaction", "control", "specification", "measurement"):
            if field not in facts.columns:
                continue
            for row in facts.values(field):
                text = _text(row[field])
                match = re.fullmatch(r"(?:见|参见|详见)?\s*((?:反应计划\s*\d+|附表\s*[A-Za-z0-9]+|SOP[-－]?[A-Za-z0-9.-]+))", text, re.I)
                if match:
                    found.append(identity(match[1]))
        return sorted(set(found))

    def pointer_definition():
        requested = references()
        if not requested:
            raise StateFact("S02", "正文没有仅填代号或引用名的空指针，本项关闭。")
        for reference in requested:
            located = False
            for original in facts.data["tables"]:
                if identity(original["id"]) == reference and len(original["cells"]) > 1:
                    for address in list(original["cells"])[:1]:
                        match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                        if match:
                            column = 0
                            for letter in match[1]:
                                column = column * 26 + ord(letter) - 64
                            facts.read(original, int(match[2]), column)
                    located = True
                for address, cell in original["cells"].items():
                    value = _text(cell.get("value"))
                    if identity(value).startswith(reference) and re.search(r"[:：]\s*\S", value):
                        if cell.get("state") != "known":
                            raise UnknownFact("引用定义所在单元格不可读取。")
                        located = True
            if not located:
                raise UnknownFact("已扫描本次正文，未确认引用定义/附件；须核对项目受控资料源：" + reference)

    operations["M-09"] = pointer_definition

    def attachment_present():
        requested = references()
        if not requested:
            raise StateFact("S02", "正文未声明附表或受控SOP引用，本项关闭。")
        pointer_definition()

    operations["M-11"] = attachment_present

    def declared_task_files():
        if not group or not group.task_group_id:
            raise StateFact("S01", "未提供本次冻结文件清单，未扩大到项目全量资料对账。")
        if group.unavailable_files or facts.audit.audit_id not in group.by_audit_id:
            raise UnknownFact("本次冻结文件清单存在未读回原件，尚不能完成实收对账。")
        facts.read(facts.table, facts.header, facts.columns["name"])
        return "已核对当前任务已选文件清单与已读回版本；未对未选或任务外资料下结论。"

    operations["P-01"] = declared_task_files

    def same_part_versions():
        if not group:
            raise StateFact("S01", "本次没有同源文件候选。")
        from audit.kinds import PpapDocumentType
        from audit.precheck.cp import CpFacts

        others = [audit for audit in group.get_files_by_type(PpapDocumentType.CONTROL_PLAN) if audit.audit_id != facts.audit.audit_id]
        own_part = facts.label(facts.table, ("零件号", "零件编号", "物料编号"), facts.header)
        if identity(own_part) in {"", "/", "na", "n/a", "xxx", "tbd", "待填", "待定", "无"}:
            raise UnknownFact("零件号是占位符，不能据此将多份原件归为同一零件。")
        result = []
        for audit in others:
            reader = CpFacts(audit)
            other_part = reader.label(reader.table, ("零件号", "零件编号", "物料编号"), reader.header)
            if identity(own_part) and identity(own_part) == identity(other_part):
                pair(facts, facts.table, facts.header, reader, reader.table, reader.header, customer, compare_version=False)
                result.append(reader)
        if not result:
            raise StateFact("S01", "本次未定位到同零件的其他控制计划版本，无同源比较对象。")
        return result

    def baseline():
        versions = same_part_versions()
        roles = [facts.audit.file_metadata.get("role"), *[reader.audit.file_metadata.get("role") for reader in versions]]
        if any(role not in {"target", "reference"} for role in roles):
            raise UnknownFact("任务未提供完整本体/比对资料角色，尚不能确认是否已经指明基准件。")
        if facts.audit.file_metadata.get("role") == "target" and all(reader.audit.file_metadata.get("role") == "reference" for reader in versions):
            return versions
        for reader in versions:
            for source in reader.sources[:20]:
                if len(facts.sources) < 100 and source not in facts.sources:
                    facts.sources.append(source)
        raise DefectFact("W04", "同零件存在多个控制计划原件；任务未以唯一本体/比对资料角色指明有效基准件，须确认本次版本。")

    def choose_baseline():
        versions = baseline()
        for reader in versions:
            for source in reader.sources[:20]:
                if len(facts.sources) < 100 and source not in facts.sources:
                    facts.sources.append(source)
        return "任务已将当前原件指定为唯一审核本体，其他同零件CP均为比对资料；不新增基准件入口。"

    operations["P-07"] = choose_baseline

    def row_amount():
        try:
            versions = baseline()
        except DefectFact as exc:
            if exc.code == "W04":
                raise UnknownFact("同源基准件尚未明确，已由P07提示；不据任意版本判本体行数异常。") from None
            raise
        current = len(facts.values("name"))
        amounts = [len(reader.values("name")) for reader in versions]
        merge_sources(facts, *versions)
        if current >= max(amounts):
            return "本体可核实工序行数不少于本次同源比对版本，未发现相对节选。"
        if current < 10 and max(amounts) >= 20:
            raise DefectFact("W05", "当前本体仅" + str(current) + "行，而同源比对件含" + str(max(amounts)) + "行，命中源分则个位数/数十行节选提示，请确认本次范围。")
        raise UnknownFact("已复算本体与同源行数差异，但源分则未定义其它‘异常少’阈值，须按提交范围确认。")

    operations["M-08"] = row_amount

    def canonical_content(reader):
        if reader.data["source"]["format"] not in {".xlsx", ".xlsm"}:
            raise UnknownFact("同源比对当前只核实原生Excel全部单元格；其它载体未按抽取文本相同推定内容一致。")
        result = []
        for original in reader.data["tables"]:
            if original.get("visual_evidence") or any(cell.get("state") != "known" for cell in original["cells"].values()):
                raise UnknownFact("原件仍有未核实图形/勾选或公式内容，不能仅按文本宣称同源一致。")
            result.append(
                (
                    original["id"],
                    original["rows"],
                    original["cols"],
                    original.get("merges", []),
                    sorted((address, cell.get("value"), cell.get("formula")) for address, cell in original["cells"].items()),
                )
            )
        return result

    def same_source_comparison():
        try:
            versions = baseline()
        except DefectFact as exc:
            if exc.code == "W04":
                raise UnknownFact("同源基准件尚未明确，已由P07提示；不据任意版本判内容差异。") from None
            raise
        own = canonical_content(facts)
        for reader in versions:
            if own != canonical_content(reader):
                merge_sources(facts, *versions)
                raise UnknownFact("已逐单元格识别同源原件内容差异；须依据变更说明确认是否可解释，未任意把新版差异判错。")
        facts.read(facts.table, facts.header, facts.columns["name"])
        merge_sources(facts, *versions)
        return "已核对本次唯一本体与全部同源比对原件的完整原生Excel内容；无未核实图形或公式，格式边框不参与比较。"

    operations["X-05"] = same_source_comparison

    def stage_adaptation():
        selected = _stage(stage_value())
        if len(selected) != 1:
            raise UnknownFact("原件阶段尚不能唯一识别，需确认标记或同义表达；未将读取能力缺口当作文件缺陷。")
        return "已识别本控制计划阶段，执行当前客户准备检查；未将未提交阶段加入审核范围。"

    operations["P-08"] = stage_adaptation

    def characteristic_columns():
        process_words = {"温度", "压力", "时间", "转速", "电流", "电压", "气压", "流量"}
        product_words = {"长度", "孔径", "位置度", "平面度", "外观", "气密", "粗糙度"}
        unresolved = False
        for row in facts.values("product", "process"):
            product, process = _text(row["product"]), _text(row["process"])
            if product in process_words or process in product_words:
                raise DefectFact("B05", "原件特性名称精确命中源分则列出的另一类词根，产品/过程列归属矛盾。")
            unresolved |= bool(product and product not in product_words or process and process not in process_words)
        if unresolved:
            raise UnknownFact("已检查源分则明确词根，其余特性名称尚需受控同义词/工程语义确认；未将未命中当作正确。")

    operations["C-08"] = characteristic_columns

    def operation_physics():
        names = [_text(row["name"]) for row in facts.values("name")]
        for earlier, later in [("下料", "总装"), ("去毛刺清洗", "包装入库")]:
            if earlier in names and later in names and names.index(later) < names.index(earlier):
                raise DefectFact("B06", "原件明确工序命中源分则反例：" + later + "先于" + earlier + "。")
        raise StateFact("S03", "已核对源分则明确先后反例；其余工艺物理先后须结合本件工艺路线人工判断，不以词序猜工程结论。")

    operations["C-19"] = operation_physics

    def physical_limits():
        facts.values("specification")
        raise StateFact("S03", "尚无本件材料、几何与额定物理极限的受控依据，不能仅凭数值量级推定规格失真；移交工程复核。")

    operations["C-20"] = physical_limits

    def gauge_resolution():
        reviewed = False
        for row in facts.values("measurement", "specification"):
            gauge, specification = _text(row["measurement"]), _text(row["specification"])
            resolution = re.search(rf"分辨率\s*[:：=]?\s*({_NUMBER})\s*(mm|cm|µm|μm)", gauge)
            symmetric = re.fullmatch(rf"({_NUMBER})\s*±\s*({_NUMBER})\s*(mm|cm|µm|μm)", specification)
            if not resolution or not symmetric:
                continue
            reviewed = True
            scale = {"mm": Decimal(1), "cm": Decimal(10), "µm": Decimal("0.001"), "μm": Decimal("0.001")}
            measured = Decimal(resolution[1]) * scale[resolution[2]]
            width = Decimal(symmetric[2]) * 2 * scale[symmetric[3]]
            if measured <= 0 or width <= 0:
                raise UnknownFact("分辨率或公差带非正，须先确认规格与量具参数。")
            if measured >= width:
                raise DefectFact("B06", "原件明确分辨率不小于该行完整公差带；已统一长度单位直接比较。")
        raise StateFact("S03", ("已复算部分量具分辨率小于公差带。" if reviewed else "原件没有完整可复算的分辨率/公差带参数。") + "源分则未提供‘接近’阈值及完整型号基准，整体量具适配移交工程复核。")

    operations["C-24"] = gauge_resolution

    def gauge_dimensions():
        unresolved = False
        for row in facts.values("product", "process", "measurement"):
            names = [_text(row[field]) for field in ("product", "process") if _text(row[field])]
            if len(names) != 1:
                unresolved = True
                continue
            gauge = _text(row["measurement"])
            mismatches = {"扭矩": ("卡尺", "塞规"), "温度": ("压力表",), "压力": ("钢直尺",)}
            if names[0] in mismatches and any(gauge == value for value in mismatches[names[0]]):
                raise DefectFact("B05", "单一明确物理量与单一量具精确命中源分则量纲错配反例。")
            unresolved = True
        if unresolved:
            raise StateFact("S03", "已检查源分则明确量纲错配反例，其余型号与物理量适配须受控量具矩阵或工程确认，不用量具名称猜测性能。")

    operations["C-25"] = gauge_dimensions
    return operations
