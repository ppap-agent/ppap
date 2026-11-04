"""Li Auto L1 preparation facts, separate from formal engineering verdicts."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.precheck_facts import DefectFact, SourceFacts, UnknownFact, key, state_fact
from audit.precheck_sources import l1_criteria

_PLACEHOLDERS = {"/", "n/a", "na", "-", "—", "--", "无", "xx", "xxx", "待填", "待定", "tbd", "详见报告", "合格，见报告"}
_FOOTERS = ("填写说明", "填表说明", "编制：", "审核：", "批准：", "备注：")


class L1Facts(SourceFacts):
    """Small native fact reader; source criteria select explicit operations."""

    def table_rows(self, aliases, *, driver=None):
        matches = []
        for table in self.data["tables"]:
            if any(word in str(table["id"]) for word in ("填写说明", "填写指南", "评分准则", "判定准则")):
                continue
            headers = None
            for row in range(1, min(table["rows"], 80) + 1):
                found = {}
                for logical, names in aliases.items():
                    matched_columns = [col for col in range(1, table["cols"] + 1) if key(effective(table, row, col).get("value")) in {key(n) for n in names}]
                    if len(matched_columns) > 1:
                        raise UnknownFact("存在同名业务列，需先确认表头归属。")
                    if matched_columns:
                        found[logical] = matched_columns[0]
                if len(found) == len(aliases):
                    headers = row, found
                    break
            if headers is None:
                continue
            header, columns = headers
            for col in columns.values():
                self.read(table, header, col)
            records = []
            for row in range(header + 1, table["rows"] + 1):
                raw = {name: effective(table, row, col) for name, col in columns.items()}
                known_text = [str(item.get("value") or "") for item in raw.values()]
                if any(text.startswith(_FOOTERS) for text in known_text):
                    break
                if not any(str(item.get("value") or "").strip() or item["state"].startswith("unknown") for item in raw.values()):
                    continue
                if driver and not str(raw[driver].get("value") or "").strip():
                    if str(raw[driver]["state"]).startswith("unknown"):
                        self.read(table, row, columns[driver], vertical_only=True)
                    raise UnknownFact("业务行主键为空，不能跳过该行并认定其余行代表完整范围。")
                records.append({name: self.read(table, row, col, vertical_only=True) for name, col in columns.items()})
            matches.extend(records)
        if not matches:
            raise UnknownFact("未取得本项可定位的业务表头及数据行。")
        return matches

    def all_cells(self):
        result = []
        for table in self.data["tables"]:
            if any(word in str(table["id"]) for word in ("填写说明", "填写指南", "评分准则")):
                continue
            for address, cell in table["cells"].items():
                if cell.get("value") is None and not str(cell.get("state", "")).startswith("unknown"):
                    continue
                row, col = coordinate_to_tuple(address)
                result.append(self.read(table, row, col))
        if not result:
            raise DefectFact("B05", "原件没有可核实的填写内容。")
        return result

    def values(self, aliases, *, required=True, consistent=False):
        result = []
        wanted = {key(value) for value in aliases}
        for table in self.data["tables"]:
            if any(word in str(table["id"]) for word in ("填写说明", "填写指南")):
                continue
            for row in range(1, min(table["rows"], 80) + 1):
                for column in range(1, table["cols"] + 1):
                    raw = effective(table, row, column)
                    if key(raw.get("value")) not in wanted:
                        continue
                    self.read(table, row, column)
                    right = column + 1
                    for first, left, last, rightmost in table.get("merges", []):
                        if first <= row <= last and left <= column <= rightmost:
                            right = rightmost + 1
                            break
                    if right <= table["cols"]:
                        value = self.read(table, row, right)
                        if key(value) in _NUMERIC_HEADERS:
                            raise UnknownFact("相邻单元格是另一业务表头，不能当作字段值；需按表格列绑定。")
                        result.append(value)
        if not result:
            raise UnknownFact("未定位到" + "/".join(aliases) + "字段，不能确定缺栏还是当前载体未读取。")
        if required and any(not str(value if value is not None else "").strip() for value in result):
            raise DefectFact("W02", "/".join(aliases) + "有栏无值。")
        if consistent and len({key(value) for value in result if str(value or "").strip()}) > 1:
            raise UnknownFact("同名字段有多个值；尚未证明它们属于同一零件或同一报告区块，不能把独立子报告判为冲突。")
        return result

    def placeholders(self):
        values = self.all_cells()
        if any(str(value).strip().casefold() in _PLACEHOLDERS for value in values):
            raise DefectFact("W02", "原件仍有占位值，需确认是否用于真实判定字段。")

    def fake_fill(self):
        values = self.all_cells()
        if any(isinstance(value, str) and value != "" and not value.strip() for value in values):
            raise DefectFact("W02", "原件存在仅空格或不可见空白组成的假填充。")

    def sequences(self):
        found = False
        original = self.data["tables"]
        try:
            for table in original:
                self.data["tables"] = [table]
                try:
                    rows = self.table_rows({"serial": ("序号", "NO", "No.")})
                except UnknownFact as exc:
                    if str(exc).startswith("未取得"):
                        continue
                    raise
                found = True
                values = [str(row["serial"]).strip() for row in rows]
                if any(key(value) in {key("序号"), key("NO")} for value in values):
                    raise UnknownFact("同一工作表有多个序号区段，需先确认切片边界。")
                if any(not re.fullmatch(r"\d+", value) for value in values):
                    raise DefectFact("W03", "业务序号存在非数字值。")
                parsed = [int(value) for value in values]
                if parsed != list(range(1, len(parsed) + 1)):
                    raise DefectFact("W03", "业务序号有重复、跳号或倒序。")
        finally:
            self.data["tables"] = original
        if not found:
            raise UnknownFact("没有可核实的业务序号区段。")

    def date_formats(self, fields):
        for aliases in fields:
            styles = set()
            values = self.values(aliases, required=False)
            bad = 0
            for value in values:
                if value is None or not str(value).strip():
                    continue
                if normalize_excel_date(value) is None:
                    bad += 1
                if isinstance(value, (date, datetime)):
                    styles.add("native")
                elif isinstance(value, (int, float)):
                    styles.add("serial")
                else:
                    styles.add("cn" if "年" in str(value) else "." if "." in str(value) else "/" if "/" in str(value) else "-")
            if bad:
                raise DefectFact("B05" if bad * 2 >= len(values) else "W03", "日期字段存在不可解析值。")
            if len(styles) > 1:
                raise DefectFact("W03", "同一日期列使用不同写法，需统一。")

    def date_pairs(self):
        found = False
        for first, last in [("计划开始", "计划结束"), ("试验实际开始日期", "试验实际结束日期")]:
            try:
                rows = self.table_rows({"first": (first,), "last": (last,)})
            except UnknownFact as exc:
                if str(exc).startswith("未取得"):
                    continue
                raise
            found = True
            for row in rows:
                a, b = normalize_excel_date(row["first"]), normalize_excel_date(row["last"])
                if a is None or b is None:
                    raise UnknownFact("起止日期不可解析，本项尚未核实。")
                if b < a:
                    raise DefectFact("B06", "同一行结束日期早于开始日期。")
        if not found:
            raise UnknownFact("未取得计划或实际起止日期列。")

    def strict_number(self, value):
        try:
            if isinstance(value, bool) or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", str(value).strip()):
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError
            return number
        except (ValueError, InvalidOperation):
            raise DefectFact("B05", "数值不是无单位、无说明的单一有限数值。") from None


def _unknown(message):
    def check():
        raise UnknownFact(message)

    return check


def _fact_list(agent, entry, operations, *, states=None):
    facts = L1Facts(agent.audit_file)
    result = []
    for item in l1_criteria(entry):
        identity = item["criterion_id"]
        if states and identity in states:
            code, message, status = states[identity]
            result.append(state_fact(item["id"], code, message, status=status))
        elif identity in operations:
            result.append(facts.observe(item["id"], lambda operation=operations[identity]: operation(facts)))
        else:
            result.append(state_fact(item["id"], None, "本分则判据尚未自动执行；其结论未核实，需要补充实现或核对所需的范围、关联及视觉事实。", status="not_checked"))
    return result


def _consistent_identifiers(facts, groups):
    for aliases in groups:
        facts.values(aliases, consistent=True)


def _result_words(facts, aliases=("实际试验结果", "试验结果")):
    try:
        rows = facts.table_rows({"result": aliases})
    except UnknownFact as exc:
        if not str(exc).startswith("未取得"):
            raise
        rows = [{"result": value} for value in facts.values(aliases)]
    known = {"ok", "pass", "符合", "合格", "不合格", "ng", "nok", "fail", "偏差认可", "未试验", "取消", "豁免"}
    invalid = sum(str(row["result"]).strip().casefold() not in known for row in rows)
    if invalid:
        raise DefectFact("B05" if invalid * 2 >= len(rows) else "W03", "判定结论存在无法归一的写法。")


def _quantity_groups(facts):
    rows = facts.table_rows({"quantity": ("数量", "样本数量", "样品数量", "试验样本描述")})
    for row in rows:
        text = str(row["quantity"] or "")
        match = re.search(r"(\d+)\s*件.*?(\d+)\s*组.*?每组\s*(\d+)\s*件", text)
        if match and int(match[1]) != int(match[2]) * int(match[3]):
            raise DefectFact("W03", "样本总数与分组数乘以每组数量不一致。")
        if not match and not re.fullmatch(r"\s*\d+\s*(?:件|pcs|个|套)?\s*", text, re.IGNORECASE):
            raise UnknownFact("数量描述尚不能还原为唯一总数及分组数。")


def _header_unique(facts):
    facts.table_rows({"serial": ("序号", "NO", "No."), "test": ("试验项目",), "type": ("试验类型",)})


def _plan_columns(facts):
    facts.table_rows(
        {
            "serial": ("序号",),
            "test": ("试验项目",),
            "requirement": ("试验要求",),
            "method": ("试验标准/方法", "试验标准方法"),
            "type": ("试验类型",),
            "start": ("计划开始",),
            "end": ("计划结束",),
            "quantity": ("数量", "试验样本描述"),
        }
    )


def _result_vs_summary(facts):
    values = facts.values(("试验结论", "实验结论", "结论"), required=False)
    normalized = set()
    for value in values:
        word = str(value or "").strip().casefold()
        if word in {"ok", "pass", "符合", "通过", "合格", "p"}:
            normalized.add("pass")
        elif word in {"ng", "nok", "fail", "不符合", "不通过", "不合格"}:
            normalized.add("fail")
        else:
            raise UnknownFact("结论文本无法归一，不能核实汇总和详情是否一致。")
    if len(normalized) > 1:
        raise DefectFact("B06", "原件的汇总与详情结论方向冲突。")


def _phase_value(facts):
    values = facts.values(("试验类型", "实验类型", "验证阶段", "试验阶段", "测试阶段"), required=False)
    for value in values:
        if not str(value or "").strip():
            raise DefectFact("B03", "试验阶段有栏无值。")
        if not re.fullmatch(r"\s*(DV|PV)\s*(测试|试验|阶段)?\s*", str(value), re.IGNORECASE):
            raise DefectFact("W03", "阶段取值不能归一为DV或PV。")


def _report_number(facts):
    facts.values(("文件编号", "报告编号", "编号", "Report No.", "Report Number"), consistent=True)


def _report_sample(facts):
    facts.values(("样品数量", "样本数量", "Sample quantity"))


def precheck_plan(agent):
    return _fact_list(
        agent,
        "L02",
        {
            "COR-01": lambda f: f.date_pairs(),
            "COR-02": lambda f: f.date_formats([("计划开始",), ("计划结束",), ("试验实际开始日期",), ("试验实际结束日期",)]),
            "COR-03": lambda f: f.placeholders(),
            "COR-04": lambda f: f.fake_fill(),
            "COR-06": _result_words,
            "COR-07": lambda f: f.sequences(),
            "COR-08": _quantity_groups,
            "COM-04": _report_number,
            "COM-05": _header_unique,
            "COM-06": lambda f: _consistent_identifiers(f, [("零件号", "零件编号"), ("零件名称", "零件总成名称")]),
            "COM-07": lambda f: f.table_rows({"method": ("试验标准/方法", "试验标准方法"), "requirement": ("试验要求",)}),
            "COM-09": _plan_columns,
            "COM-11": _plan_columns,
            "COM-12": lambda f: _consistent_identifiers(f, [("版本号", "版本"), ("零件号", "零件编号"), ("供应商名称",), ("适用车型", "车型")]),
            "CON-02": lambda f: _consistent_identifiers(f, [("报告编号", "文件编号"), ("零件号", "零件编号"), ("供应商名称",), ("适用车型", "车型")]),
            "CON-03": lambda f: _consistent_identifiers(f, [("质量等级",), ("外观属性",), ("修订部门",)]),
            "CON-09": _result_vs_summary,
        },
    )


def precheck_report(agent):
    return _fact_list(
        agent,
        "L03",
        {
            "COR-01": _report_number,
            "COR-02": lambda f: _result_words(f, ("判定", "试验判定", "试验结论", "实验判定", "实验结论", "结论")),
            "COR-03": lambda f: f.placeholders(),
            "COR-04": lambda f: f.fake_fill(),
            "COR-06": _phase_value,
            "COR-07": lambda f: f.date_formats([("试验开始日期", "试验开始"), ("试验结束日期", "试验结束"), ("签发日期",)]),
            "COR-11": _result_vs_summary,
            "COM-03": _phase_value,
            "COM-04": _report_number,
            "COM-06": _phase_value,
            "COM-08": lambda f: f.values(("判定", "试验判定", "试验结论", "实验结论", "结论")),
            "COM-09": _report_sample,
            "COM-14": lambda f: _consistent_identifiers(f, [("编制",), ("审核",), ("批准", "签发")]),
            "CON-02": _report_number,
            "CON-03": lambda f: _consistent_identifiers(f, [("零件号", "零件编号"), ("零件名称",), ("供应商内部件号", "供方料号")]),
            "CON-05": _result_vs_summary,
            "CON-11": _phase_consistency,
        },
    )


def _material_weights(facts):
    for row in facts.table_rows({"part": ("零件号", "零件/部件零件号", "零件/分部件零件号"), "weight": ("单件重量",)}, driver="part"):
        facts.strict_number(row["weight"])


def _material_status(facts):
    aliases = {"part": ("零件号", "零件/部件零件号", "零件/分部件零件号"), "made": ("自制",), "domestic": ("外协-国产", "国产"), "import": ("外协-进口", "进口"), "taken": ("领用件",)}
    for row in facts.table_rows(aliases, driver="part"):
        if sum(bool(str(row[name] or "").strip()) for name in ("made", "domestic", "import", "taken")) != 1:
            raise DefectFact("W06", "理想客户差异层：同一零件行的实现方式不是恰好一项。")


def _material_placeholder_parts(facts):
    for row in facts.table_rows({"part": ("零件号", "零件/部件零件号", "零件/分部件零件号")}, driver="part"):
        if str(row["part"]).strip().casefold() in {"123456", "111111", "000000", "待定", "tbd", "xxx"}:
            raise DefectFact("B03", "零件号是占位号，无法确认真实对象。")


def _material_same_part(facts):
    rows = facts.table_rows(
        {
            "part": ("零件号", "零件/部件零件号", "零件/分部件零件号"),
            "name": ("零件名称", "零件/部件名称"),
            "material": ("材料牌号",),
            "made": ("自制",),
            "domestic": ("外协-国产", "国产"),
            "import": ("外协-进口", "进口"),
            "taken": ("领用件",),
        },
        driver="part",
    )
    by_part: dict[str, tuple[str, ...]] = {}
    for row in rows:
        part = key(row["part"])
        values = tuple(key(row[name]) for name in ("name", "material", "made", "domestic", "import", "taken"))
        if part in by_part and by_part[part] != values:
            raise DefectFact("W04", "同一零件号的名称、材料或实现方式不一致。")
        by_part[part] = values


def _material_fill_rate(facts):
    rows = facts.table_rows(
        {
            "part": ("零件号", "零件/部件零件号", "零件/分部件零件号"),
            "standard": ("材料执行标准", "材料标准"),
            "type": ("材料类型",),
            "grade": ("材料牌号",),
            "size": ("材料规格/尺寸", "材料规格·尺寸"),
            "supplier": ("材料供应商名称", "材料供应商名称（含生产地点）"),
            "cert": ("通过何种质量管理体系认证",),
        },
        driver="part",
    )
    for field in ("standard", "type", "grade", "size", "supplier", "cert"):
        if sum(not str(row[field] or "").strip() for row in rows) / len(rows) >= 0.9:
            raise DefectFact("W01", "适用材料列有至少90%的业务行未填写。")


def precheck_materials(agent):
    return _fact_list(
        agent,
        "L04",
        {
            "PC-B4": lambda f: f.sequences(),
            "PC-C1": _material_swapped,
            "PC-C2": _material_placeholder_parts,
            "PC-C3": _material_weights,
            "PC-C5": _material_size,
            "PC-C6": _material_standard,
            "PC-C7": _material_process,
            "PC-C9": _material_certification,
            "PC-C10": _material_status,
            "PC-D3": _material_fill_rate,
            "PC-E5": _material_same_part,
            "PC-E6": _material_units,
        },
        states={"PC-D6": ("S02", "理想客户差异层无签字区要求。", "not_checked"), "PC-D7": ("S02", "理想客户差异层无图纸跨文件依赖。", "not_checked")},
    )


def _ccsc_rows(facts):
    return facts.table_rows({"process": ("工序流程编号",), "characteristic": ("特性名称",), "cpk": ("P阶段Cpk结果",), "decision": ("P阶段结果判定",)})


def _ccsc_complete(facts):
    checked = False
    for row in _ccsc_rows(facts):
        if not str(row["characteristic"] or "").strip():
            continue
        checked = True
        if any(not str(row[field] if row[field] is not None else "").strip() for field in ("cpk", "decision")):
            raise DefectFact("W02", "已填写特性名称的行缺少Cpk或结果判定。")
    if not checked:
        raise UnknownFact("没有已填写特性名称的数据行。")


def _ccsc_numeric_type(facts):
    for row in _ccsc_rows(facts):
        if str(row["cpk"]).strip().upper() != "NA":
            facts.strict_number(row["cpk"])


def _control_chart_set(facts):
    tables = facts.data["tables"]
    if not any("CCSC" in str(table["id"]).upper() for table in tables):
        raise UnknownFact("尚未确认是CCSC与控制图组合载体，不能套用该族必备图组。")
    for name in ("I-MR", "Xbar-R", "Xbar-S"):
        matches = [table for table in tables if name.casefold() in str(table["id"]).casefold()]
        if not matches:
            raise DefectFact("B05", "CCSC组合载体缺少" + name + "控制图。")
        facts.read(matches[0], 1, 1)


def precheck_capacity(agent):
    return _fact_list(
        agent,
        "L06",
        {
            "PRE-05": lambda f: _ccsc_rows(f),
            "PRE-07": _ccsc_complete,
            "PRE-08": _control_chart_set,
            "PRE-09": _capacity_placeholders,
            "PRE-10": _ccsc_numeric_type,
            "PRE-11": _capacity_specifications,
            "PRE-12": _capacity_printed_math,
        },
        states={"PRE-04": ("S02", "理想客户差异层无PGQP1依赖，也不要求独立目录页。", "not_checked")},
    )


def _msa_identifiers(facts):
    rows = facts.table_rows({"gauge_id": ("量具编号",), "gauge_name": ("量具名称",), "part": ("零件号",)})
    for field in ("gauge_id", "gauge_name", "part"):
        missing = sum(not str(row[field] or "").strip() for row in rows)
        if missing:
            raise DefectFact("B05" if missing == len(rows) else "W02", "MSA关键标识有整列或个别业务行未填写。")


def _msa_core_columns(facts):
    rows = facts.table_rows({"id": ("量具编号",), "range": ("测量范围",), "resolution": ("精度", "分辨率", "精度或分辨率"), "analysis": ("分析项目", "分析类型")})
    for field in ("id", "range", "resolution", "analysis"):
        if not any(str(row[field] or "").strip() for row in rows):
            raise DefectFact("B05", "MSA计划核心列整列为空。")


def _msa_gauge_identity(facts):
    rows = facts.table_rows({"id": ("量具编号",), "name": ("量具名称",)}, driver="id")
    seen: dict[str, str] = {}
    for row in rows:
        identity, name = key(row["id"]), key(row["name"])
        if identity in seen and seen[identity] != name:
            raise DefectFact("W04", "同一量具编号对应不同量具名称。")
        if any(delimiter in str(row["id"]) for delimiter in ("、", ";", "；")):
            raise DefectFact("W04", "一个量具编号栏填入多个编号。")
        if identity == name:
            raise DefectFact("W03", "量具编号与量具名称相同，需确认是否以名代码。")
        seen[identity] = name


def precheck_msa(agent):
    return _fact_list(
        agent,
        "L05",
        {
            "MSA-PRE-01": _msa_errors,
            "MSA-PRE-03": _msa_draft,
            "MSA-PRE-08": _msa_gauge_identity,
            "MSA-PRE-09": _msa_dimensions,
            "MSA-PRE-10": _msa_resolution,
            "MSA-PRE-11": _msa_design,
            "MSA-PRE-12": _msa_constants,
            "MSA-PRE-20": _msa_percentage,
            "MSA-PRE-17": _msa_identifiers,
            "MSA-PRE-18": _msa_core_columns,
            "MSA-PRE-19": _msa_controlled,
        },
    )


def _capacity_placeholders(facts):
    values = facts.all_cells()
    for value in values:
        text = str(value).strip()
        if text.casefold() in {"xx", "--", "待填", "tbd", "?", "＊"}:
            raise DefectFact("B05", "能力报告保留模板占位符。")
        if re.fullmatch(r"#{4,}", text):
            raise DefectFact("W05", "能力报告存在列宽溢出的显示占位符。")
    for row in _ccsc_rows(facts):
        if str(row["cpk"]).strip() in {"N/A", "n/a", "－", "/", "无"}:
            raise DefectFact("W03", "源允许的不适用标注应统一为NA。")
        if str(row["cpk"]).strip() in {"∞", "√", "≈"}:
            raise DefectFact("B05", "能力值位置存在非能力数值符号。")


def _msa_errors(facts):
    for value in facts.all_cells():
        if re.fullmatch(r"#{4,}|#(?:DIV/0!|VALUE!|REF!|NAME\?|N/A)", str(value).strip(), re.IGNORECASE):
            raise DefectFact("B05", "MSA原件存在电子表格报错符或截断显示。")


def precheck_inspection(agent):
    from audit.engine.checkers.dimension_measurement_policy import is_strict_ok_text

    from .facts import precheck_fact

    facts = _fact_list(
        agent,
        "L01",
        {},
        states={
            "IR-09": ("S03", "实测值与公差关系移交正式审核。", "handoff"),
            "IR-11": ("S03", "同行数值重复移交正式审核。", "handoff"),
            "IR-13": ("S02", "理想客户差异层无供应商签字区要求。", "not_checked"),
            "IR-15": ("S02", "理想客户差异层无气泡图跨文件依赖。", "not_checked"),
            "IR-16": ("S03", "贴近公差上限的能力提示移交正式审核。", "handoff"),
            "IR-21": ("S02", "理想单表全包型无附件递交区。", "not_checked"),
        },
    )

    def standard(ctx):
        for row, _ in agent._inspection_rows(ctx, ("判定标准",)):
            row.expect("判定标准", context="待检项目标准必填")
        return "PASS", "每个待检项均有标准文本。"

    def measurements(ctx):
        for row, _ in agent._inspection_rows(ctx, ("检查数据", "判定结果")):
            row.expect("判定结果", context="检查结果必填")
            values = row.values_between("检查数据", "判定结果")
            if not values or any(str(value or "").strip().upper() in {"", "/", "NA", "N/A"} for value in values):
                return "NG", "实测值为空或仍是占位符。"
        return "PASS", "实测值与结果已填写。"

    def results(ctx):
        for row, _ in agent._inspection_rows(ctx, ("检查数据", "判定结果")):
            if not is_strict_ok_text(row.value("判定结果")):
                return "NG", "判定结果只接受OK或合格。"
        return "PASS", "全部判定结果可归一为OK或合格。"

    def samples(ctx):
        required = set()
        for sheet in ctx.workbook.worksheets:
            for cells in sheet.iter_rows(max_row=min(sheet.max_row, 80)):
                for cell in cells:
                    if key(cell.value) in {key("应测样本量"), key("要求样本量")}:
                        merged = ctx.ops.get_detailed_merge_info(sheet, cell.row, cell.column)
                        column = merged.max_col + 1 if merged else cell.column + 1
                        value = sheet.cell(cell.row, column)
                        ctx.note_cell(sheet.title, cell.row, cell.column)
                        ctx.note_cell(sheet.title, value.row, value.column)
                        if value.data_type in {"e", "f"} or not str(value.value or "").strip().isdigit() or int(value.value) <= 0:
                            return "UNABLE", "声明的应测样本量无法可靠解析。"
                        required.add(int(value.value))
        if len(required) > 1:
            return "UNABLE", "应测样本量存在多个不同声明。"
        target = next(iter(required), 5)
        for row, _ in agent._inspection_rows(ctx, ("检查数据", "判定结果")):
            values = row.values_between("检查数据", "判定结果")
            count = sum(len(str(value).split("/")) for value in values if str(value if value is not None else "").strip())
            if count < target:
                return "NG", f"实填样本量少于本次要求的{target}件。"
        return "PASS", "每个待检项的实填样本量均达到本次要求。"

    checks = {
        "IR-02": (agent.rule_1, "B03"),
        "IR-04": (standard, "W02"),
        "IR-05": (agent.rule_8, "W02"),
        "IR-06": (agent.rule_2, "W02"),
        "IR-07": (agent.rule_4, "W03"),
        "IR-08": (measurements, "W02"),
        "IR-10": (results, "W03"),
        "IR-17": (samples, "W01"),
    }
    replacements = {"L01:" + criterion: precheck_fact(agent, "L01:" + criterion, operation, code=code) for criterion, (operation, code) in checks.items()}
    return [replacements.get(fact["id"], fact) for fact in facts]


def _phase_consistency(facts):
    values = facts.values(("试验类型", "验证阶段", "试验阶段", "测试阶段"))
    stages = set()
    for value in values:
        match = re.fullmatch(r"\s*(DV|PV)\s*(测试|试验|阶段)?\s*", str(value), re.IGNORECASE)
        if not match:
            raise UnknownFact("阶段写法不能唯一归一。")
        stages.add(match[1].upper())
    if len(stages) > 1:
        raise DefectFact("W04", "报告内部存在DV/PV阶段标识冲突。")


def _msa_controlled(facts):
    facts.sequences()
    for aliases in [("文件编号", "报告编号"), ("版本", "版本号"), ("日期",)]:
        try:
            values = facts.values(aliases, required=False)
        except UnknownFact as exc:
            if str(exc).startswith("未定位到"):
                continue
            raise
        if any(str(value or "").strip().casefold() in {"0", "待填", "待定", "tbd", "xxx"} for value in values):
            raise DefectFact("W02", "受控字段被占位值占用。")


def _msa_draft(facts):
    facts.placeholders()
    raise UnknownFact("未发现已读取文本中的占位符；底色和残稿标记尚未完整核实。")


_NUMERIC_HEADERS = {key(value) for value in ("LSL", "USL", "Cp", "Cpk", "Pp", "Ppk", "r", "k", "n", "重复次数", "评价人数", "零件数", "K1", "K2", "K3", "D4", "%R&R", "%GRR", "%P/T", "ndc")}


_PART = ("零件号", "零件/部件零件号", "零件/分部件零件号")


def _material_swapped(facts):
    for row in facts.table_rows({"part": _PART, "name": ("零件名称", "零件/部件名称")}, driver="part"):
        if re.fullmatch(r"[\u4e00-\u9fff]+", str(row["part"]).strip()) and re.fullmatch(r"(?=.*\d)[A-Za-z0-9_.-]+", str(row["name"]).strip()):
            raise DefectFact("B03", "零件号是纯中文名称，而零件名称是编码，两个字段明显颠倒。")


def _material_size(facts):
    for row in facts.table_rows({"part": _PART, "size": ("材料规格/尺寸", "材料规格·尺寸")}, driver="part"):
        if re.search(r"镀[锌铬镍铜银]|电泳|喷涂|粉末涂|涂层|胶水|粘合剂|注塑|冲压|焊接", str(row["size"])):
            raise DefectFact("W03", "尺寸列含镀层、涂层、胶水或工艺描述，不能作为单纯尺寸。")
    raise UnknownFact("已检查源列举的寄生语义；其他材料描述是否属于尺寸仍需语义核对。")


def _material_standard(facts):
    for row in facts.table_rows({"part": _PART, "standard": ("材料执行标准", "材料标准")}, driver="part"):
        if re.search(r"有限公司|有限责任公司|联系人|联系电话|手机|地址[：:]|@", str(row["standard"])):
            raise DefectFact("W03", "材料执行标准列填写了企业或联系信息。")
    raise UnknownFact("未发现明确企业和联系方式；其他文本的标准身份仍需受控标准目录核对。")


def _material_process(facts):
    for row in facts.table_rows({"part": _PART, "process": ("成型工艺", "成形工艺"), "surface": ("表面处理",)}, driver="part"):
        process, surface = key(row["process"]), key(row["surface"])
        if process and process == surface:
            raise DefectFact("W06", "成型工艺与表面处理两列填写同一词。")


def _material_certification(facts):
    for row in facts.table_rows({"part": _PART, "cert": ("通过何种质量管理体系认证",)}, driver="part"):
        text = key(row["cert"])
        if re.search(r"(?:isots16949|ts16949|iso9001)[:：]?(?:1994|2000|2002|2008|2009)", text) or "iatf1694" == text or "iso900" == text:
            raise DefectFact("W03", "认证栏保留源明确要求核对的旧版标准或截断拼写。")
        if any(word in text for word in ("代理商", "过渡", "正在认证", "iso9001")):
            raise UnknownFact("仅ISO9001、代理商或认证过渡态需确认豁免及证书，不能据文本判定有效。")
    raise UnknownFact("已核对明确旧版及拼写反例；认证真伪、有效期和完整标准身份仍需证书核对。")


def _material_units(facts):
    rows = facts.table_rows({"part": _PART, "weight": ("单件重量",), "size": ("材料规格/尺寸", "材料规格·尺寸")}, driver="part")
    observed: dict[str, set[str]] = {"weight": set(), "size": set()}
    for row in rows:
        for field, pattern in (("weight", r"(?<![a-z])(kg|g|千克|克)(?![a-z])"), ("size", r"(?<![a-z])(mm|cm|毫米|厘米)(?![a-z])")):
            units = re.findall(pattern, str(row[field]).casefold())
            aliases = {"千克": "kg", "克": "g", "毫米": "mm", "厘米": "cm"}
            observed[field].update(aliases.get(unit, unit) for unit in units)
    if any(len(units) > 1 for units in observed.values()):
        raise DefectFact("W03", "同一材料列出现不统一的重量或尺寸单位。")
    raise UnknownFact("已检查逐格显式单位混用；纯数值字段的单位须由受控表头和密度口径进一步确认。")


def _unique_number(facts, aliases):
    values = facts.values(aliases)
    numbers = {facts.strict_number(value) for value in values}
    if len(numbers) != 1:
        raise UnknownFact("同一数值字段有多个值，需先明确报告区块归属。")
    return next(iter(numbers))


def _msa_dimensions(facts):
    rows = facts.table_rows({"name": ("量具名称",), "range": ("测量范围",), "resolution": ("精度", "分辨率", "精度或分辨率")})
    for row in rows:
        name, value = str(row["name"]), str(row["range"]) + " " + str(row["resolution"])
        if (
            (re.search(r"粗糙度|\bRa\b|\bRz\b", name, re.I) and re.search(r"(?<![a-z])mm\b", value, re.I))
            or ("硬度" in name and re.search(r"\bmm\b", value, re.I))
            or ("扭矩" in name and re.search(r"\d\s*N\s*$", value, re.I))
        ):
            raise DefectFact("W03", "量具物理量与填写单位符合源列举的明确量纲反例。")
        if "游标卡尺" in name and re.fullmatch(r"\s*0\.001\s*mm\s*", str(row["resolution"]), re.I):
            raise DefectFact("W03", "游标卡尺分辨率填写0.001mm，需核对量具信息。")
    raise UnknownFact("已检查源列举的量纲反例；其余量具身份及分辨率需受控规格核对。")


def _length(value):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mm|cm|μm|µm|um|毫米|厘米|微米)?\s*", str(value), re.I)
    if not match:
        raise UnknownFact("尺寸值或单位不能唯一解析。")
    unit = (match[2] or "").casefold()
    scale = {"cm": Decimal(10), "厘米": Decimal(10), "μm": Decimal(".001"), "µm": Decimal(".001"), "um": Decimal(".001"), "微米": Decimal(".001")}.get(unit, Decimal(1))
    return Decimal(match[1]) * scale, bool(unit)


def _msa_resolution(facts):
    rows = facts.table_rows({"resolution": ("精度", "分辨率", "精度或分辨率"), "band": ("规格公差带", "公差带宽度", "公差带")})
    for row in rows:
        resolution, ru = _length(row["resolution"])
        band, bu = _length(row["band"])
        if ru != bu:
            raise UnknownFact("公差带与分辨率仅一方声明单位，不能默认两者量纲一致。")
        if band <= 0 or resolution <= 0:
            raise UnknownFact("公差带或分辨率非正值，应先核对输入定义。")
        if resolution > band / 10:
            raise DefectFact("W06", "原件声明的分辨率大于规格公差带的十分之一。")


def _msa_design(facts):
    # Explicit design declarations only: never infer r/k/n from page or column counts.
    r = _unique_number(facts, ("重复次数", "试验次数", "r"))
    k = _unique_number(facts, ("评价人数", "操作员人数", "k"))
    n = _unique_number(facts, ("零件数", "样品数", "n"))
    if any(number != number.to_integral_value() or number < 0 for number in (r, k, n)):
        raise UnknownFact("实验设计声明不是非负整数，需确认各计数定义。")
    if r < 2:
        raise DefectFact("B05", "计量型实验声明的重复次数r小于2。")
    if k < 2 or n < 5:
        raise DefectFact("W06", "计量型实验声明的评价人数小于2或零件数小于5。")
    raise UnknownFact("已核对计量型r/k/n声明；计数型样本序号及基准NG占比未在此声明中证明不适用。")


def _msa_constants(facts):
    r = _unique_number(facts, ("重复次数", "试验次数", "r"))
    k = _unique_number(facts, ("评价人数", "操作员人数", "k"))
    n = _unique_number(facts, ("零件数", "样品数", "n"))
    if (r, k, n) == (3, 3, 10):
        for aliases, expected in [(("K1", "K₁"), ".5908"), (("K2", "K₂"), ".5231"), (("K3", "K₃"), ".3146"), (("D4", "D₄"), "2.575")]:
            if _unique_number(facts, aliases) != Decimal(expected):
                raise DefectFact("B06", "3×3×10设计卷面常数" + aliases[0] + "与源指定常数不一致。")
    raise UnknownFact("已核对适用的3×3×10卷面常数；勾选结论、计划实测日期和跨报告人员日期尚需定位关联区块。")


def _msa_percentage(facts):
    found = False
    for aliases in [("%R&R", "%GRR", "%P/T")]:
        try:
            values = facts.values(aliases)
        except UnknownFact as exc:
            if str(exc).startswith("未定位到"):
                continue
            raise
        found = True
        for value in values:
            text = str(value).strip()
            number = facts.strict_number(text.removesuffix("%"))
            if isinstance(value, (int, float)) and 0 < number < 1:
                number = _displayed_percentage(facts, aliases, value, number)
            if not 0 <= number <= 100 or (not text.endswith("%") and 0 < number < 1):
                raise DefectFact("W03", "百分比指标超出0–100，或以0–1小数表达而未声明百分号。")
    try:
        ndc = facts.values(("ndc",))
    except UnknownFact as exc:
        if not str(exc).startswith("未定位到"):
            raise
        ndc = []
    for value in ndc:
        found = True
        number = facts.strict_number(value)
        if number < 0 or number > 50:
            raise DefectFact("W06", "ndc小于0或大于50，须人工确认。")
    if not found:
        raise UnknownFact("未定位到百分比指标或ndc数值。")


def _number_records(facts, aliases):
    try:
        rows = facts.table_rows(aliases)
    except UnknownFact as exc:
        if not str(exc).startswith("未取得"):
            raise
        return [{name: _unique_number(facts, labels) for name, labels in aliases.items()}]
    return [{name: facts.strict_number(value) for name, value in row.items()} for row in rows]


def _capacity_specifications(facts):
    # Single bounded report only. Multiple values require an explicit report key.
    for record in _number_records(facts, {"lower": ("LSL", "规格下限", "下规格限"), "upper": ("USL", "规格上限", "上规格限")}):
        lower, upper = record["lower"], record["upper"]
        if lower >= upper:
            raise DefectFact("B06", "卷面规格下限不小于上限，公差带不成立。")
    # A nominal metadata value must not be applied to multiple independent rows.
    try:
        nominal = _unique_number(facts, ("名义值", "Nominal"))
    except UnknownFact as exc:
        if not str(exc).startswith("未定位到"):
            raise
    else:
        if len(_number_records(facts, {"lower": ("LSL", "规格下限", "下规格限"), "upper": ("USL", "规格上限", "上规格限")})) != 1:
            raise UnknownFact("多个规格区块尚未与名义值逐一绑定。")
        if not lower <= nominal <= upper:
            raise DefectFact("B06", "卷面名义值落在规格限之外。")
    raise UnknownFact("已核对双边规格代数关系；标注公差与单边特性语义尚需在同一报告区块核对，不能将部分检查当作整项满足。")


def _capacity_printed_math(facts):
    found = False
    for potential, actual in (("Cp", "Cpk"), ("Pp", "Ppk")):
        try:
            records = _number_records(facts, {"base": (potential,), "value": (actual,)})
        except UnknownFact as exc:
            if str(exc).startswith("未定位到"):
                continue
            raise
        found = True
        for record in records:
            if record["value"] > record["base"]:
                raise DefectFact("B06", "卷面" + actual + "大于" + potential + "，指标内部矛盾。")
    if not found:
        raise UnknownFact("未定位到同一报告内成对的Cp/Cpk或Pp/Ppk卷面数值。")
    raise UnknownFact("已检查卷面Cp/Cpk与Pp/Ppk关系；极值、文件自印判定基准和控制限对称性未完全定位。")


def _displayed_percentage(facts, aliases, value, number):
    if facts.data["source"]["format"].lstrip(".") not in {"xlsx", "xlsm"}:
        raise UnknownFact("数值小于1，但当前载体未提供百分比显示格式，不能判断显示量纲。")
    book = load_workbook(facts.audit.file_url, read_only=True, data_only=False)
    try:
        styles = set()
        for table in facts.data["tables"]:
            for row in range(1, min(table["rows"], 80) + 1):
                for col in range(1, table["cols"]):
                    if key(effective(table, row, col).get("value")) in {key(alias) for alias in aliases} and effective(table, row, col + 1).get("value") == value:
                        facts.read(table, row, col + 1)
                        styles.add("%" in book[table["id"]].cell(row, col + 1).number_format)
        if len(styles) != 1:
            raise UnknownFact("相同指标值的百分比显示格式不唯一，需确认区块。")
        return number * 100 if True in styles else number
    finally:
        book.close()
