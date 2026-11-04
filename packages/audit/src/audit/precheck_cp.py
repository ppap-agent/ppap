"""CP preparation predicates from PPAP-10 v2.0; customer overlay stays explicit."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal
from typing import Any

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.precheck_facts import DefectFact, SourceFacts, UnknownFact, key, state_fact
from audit.precheck_sources import l1_criteria

_COLUMNS = {
    "number": ("工序号", "工序编号", "零件/工序号", "零件工序号", "零件/过程编号"),
    "name": ("工序名称", "操作描述", "工序名称/操作描述", "工序名称操作描述", "过程名称"),
    "equipment": ("生产设备", "生产设备/工装", "生产设备工装", "设备/工装", "生产设备/工装夹具"),
    "symbol": ("特殊特性分类", "特性分类", "特性类型", "特殊特性", "特殊特性类别"),
    "product": ("产品特性", "产品特殊特性"),
    "process": ("过程特性", "过程特殊特性"),
    "specification": ("产品/过程规范/公差", "产品过程规范公差", "规格/公差", "规范/公差", "规格公差", "规范公差", "技术要求"),
    "measurement": ("评价测量技术", "评价/测量技术", "评价测量方法", "评价/测量方法", "测量技术", "测量方法"),
    "sample": ("样本容量", "样本数", "样本数量", "容量", "抽样数", "样本容量/数量"),
    "frequency": ("频率", "频次", "频率/频次", "样本频率", "样本频次", "抽样频率"),
    "control": ("控制方法", "管控方法"),
    "reaction": ("反应计划", "反应措施"),
    "characteristic_number": ("特性编号", "特性序号"),
}
_HEADER_UAES = [("供应商名称",), ("供应商代码",), ("编制人",), ("版本号", "版本"), ("受控日期", "创建日期", "修订日期"), ("零件名称",), ("零件号", "零件编号")]
_HEADER_18 = [
    ("控制计划类型", "CP类型"),
    ("控制计划编号", "CP编号"),
    ("联系人",),
    ("电话",),
    ("编制日期",),
    ("修订日期",),
    ("版本", "版本号", "Rev."),
    ("页数", "总页数"),
    ("页码",),
    ("零件号", "零件编号"),
    ("零件名称",),
    ("核心小组", "多功能小组"),
    ("公司名称", "公司"),
    ("公司代码",),
    ("公司批准",),
    ("顾客工程批准",),
    ("顾客质量批准",),
    ("其它批准", "其他批准"),
]


def _number(value):
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise UnknownFact("数值原文尚不能唯一解析；未计算缺失的公式缓存。")
    return Decimal(text)


class CpFacts(SourceFacts):
    def __init__(self, audit):
        super().__init__(audit)
        candidates = []
        for table in self.data["tables"]:
            for header in range(1, min(60, table["rows"]) + 1):
                columns, end = {}, header
                for field, aliases in _COLUMNS.items():
                    matches = [column for column in range(1, min(160, table["cols"]) + 1) if key(effective(table, header, column).get("value")) in {key(value) for value in aliases}]
                    if len(matches) == 1:
                        columns[field] = matches[0]
                if len(columns) < 4:
                    continue
                if header < table["rows"]:
                    for column in range(1, table["cols"] + 1):
                        parent, child = key(effective(table, header, column).get("value")), key(effective(table, header + 1, column).get("value"))
                        if parent == "特性" and child in {"产品", "过程"}:
                            columns["product" if child == "产品" else "process"] = column
                            end = header + 1
                        if parent in {"样本", "抽样"} and child in {"容量", "数量", "频次", "频率"}:
                            columns["sample" if child in {"容量", "数量"} else "frequency"] = column
                            end = header + 1
                if "name" in columns and len(set(columns.values())) == len(columns):
                    count = sum(bool(key(effective(table, row, columns["name"]).get("value"))) for row in range(end + 1, table["rows"] + 1))
                    candidates.append((count, len(columns), table, header, end, columns))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not candidates or candidates[0][0] == 0:
            raise UnknownFact("控制计划主表或有效工序数据尚不能定位；未把空模板当作通过。")
        self.layouts: dict[str, tuple[Any, ...]] = {}
        for candidate in candidates:
            self.layouts.setdefault(candidate[2]["id"], candidate)
        self.multiple_tables = len(self.layouts) > 1
        _, _, self.table, self.header, self.end, self.columns = candidates[0]
        self.rows = []
        for row in range(self.end + 1, self.table["rows"] + 1):
            observed = effective(self.table, row, self.columns["name"])
            if observed["state"] != "known":
                raise UnknownFact("工序行归属所需单元格不可读取，不能静默漏行后声明通过。")
            if key(observed.get("value")):
                self.rows.append(row)

    def values(self, *fields):
        if self.multiple_tables:
            raise UnknownFact("原件有多个控制计划数据表；单表检查尚需确认范围，文档间元信息仍单独核对。")
        if any(field not in self.columns for field in fields):
            raise UnknownFact("受控模板的相关列尚未全部定位，需核对模板；未认定为供应商缺列。")
        for field in fields:
            self.read(self.table, self.header, self.columns[field])
        return [{field: self.read(self.table, row, self.columns[field], vertical_only=True) for field in fields} for row in self.rows]

    def header_values(self, labels):
        return [self.label(self.table, names, self.header) for names in labels]


def cp_precheck(audit, preset_key, group=None):
    customer = {"U08": "UAES", "L08": "理想", "N03": "蔚来"}[preset_key]
    criteria = [item["criterion_id"] for item in l1_criteria(preset_key)]
    disabled = {"M-14"} if customer != "UAES" else {"C-11", "C-22", "C-23", "C-09"}
    base = {identifier: state_fact(preset_key + ":" + identifier, "S02", "本客户差异层不要求此项，不按其他客户模板判缺陷。") for identifier in disabled}
    for identifier in ("P-04", "P-05", "P-06", "X-07", "X-08", "X-09"):
        base[identifier] = state_fact(preset_key + ":" + identifier, "S01", "未声明扩展提交范围，按分则缺省R1处理；未要求补齐整套PPAP。")
    try:
        facts = CpFacts(audit)
    except Exception as exc:
        message = str(exc) if isinstance(exc, UnknownFact) else "控制计划原件读取失败，尚无业务结论。"
        return [base.get(identifier) or state_fact(preset_key + ":" + identifier, None, message, status="unknown") for identifier in criteria]
    operations = {}
    operations["P-02"] = lambda: facts.values("name")
    operations["P-03"] = lambda: facts.values("number", "name")

    def capacity():
        for row in facts.values("sample"):
            text = str(row["sample"] or "").strip()
            if text.endswith("%"):
                number = _number(text[:-1])
                if not 0 < number <= 100:
                    raise DefectFact("B05", "样本容量百分比不在(0,100]范围。")
            elif re.fullmatch(r"0(?:\.0+)?\s*(?:件|pcs)?", text, re.I):
                raise DefectFact("B05", "卷面样本容量为0，不能取得有效样本。")
            elif not text:
                raise DefectFact("W02", "部分样本容量尚未填写。")
            elif re.fullmatch(r"(?:≥|>=)?\s*\d+(?:\.\d+)?\s*(?:件|pcs)?", text, re.I):
                match = re.search(r"\d+(?:\.\d+)?", text)
                assert match is not None
                number = Decimal(match[0])
                if number <= 0:
                    raise DefectFact("B05", "卷面样本容量不大于0。")
            elif text not in {"全检", "全部"}:
                raise UnknownFact("容量表达尚未确认是正数样本量或有效百分比，未按非空即通过。")

    operations["C-01"] = capacity

    def quantity_units():
        for row in facts.values("sample", "frequency"):
            if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:小时|分钟|秒|h|min|s)", str(row["sample"] or ""), re.I) or re.fullmatch(r"\d+(?:\.\d+)?\s*(?:件|pcs|支)", str(row["frequency"] or ""), re.I):
                raise DefectFact("B05", "样本容量与频次存在明确的数量/时间量纲互填。")

    operations["C-02"] = quantity_units

    def tolerance():
        seen = 0
        number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
        interval_pattern = rf"[\[(]\s*({number})\s*[,~～]\s*({number})\s*[\])]"
        symmetric_pattern = rf"({number})\s*±\s*({number})"
        for row in facts.values("specification"):
            text = unicodedata.normalize("NFKC", str(row["specification"] or "")).strip()
            if len(re.findall(interval_pattern, text)) > 1 or len(re.findall(symmetric_pattern, text)) > 1:
                raise UnknownFact("同格有多组公差表达，尚不能唯一配对上下限。")
            interval = re.search(interval_pattern, text)
            symmetric = re.search(symmetric_pattern, text)
            remaining = re.sub(symmetric_pattern, "", re.sub(interval_pattern, "", text)).strip()
            if remaining and not re.fullmatch(r"(?:mm|cm|N|MPa)", remaining):
                raise UnknownFact("本行规格含未解析的表达，不能因其它行可解析而声明整项已核实。")
            if interval:
                low, high = Decimal(interval[1]), Decimal(interval[2])
                if low >= high:
                    raise DefectFact("B06", "原件公差闭区间上限不大于下限。")
            if symmetric:
                nominal, tolerance = Decimal(symmetric[1]), Decimal(symmetric[2])
                if tolerance < 0:
                    raise DefectFact("B06", "对称公差幅度为负，卷面区间不自洽。")
                if interval and (nominal - tolerance != low or nominal + tolerance != high):
                    raise DefectFact("B06", "名义值±公差与同格标注上下限不一致。")
            if not (interval or symmetric):
                raise UnknownFact("未取得本行可核实的明确公差；未判断实测值合格性。")
            seen += 1
        if not seen:
            raise UnknownFact("未取得本项可核实的明确公差上下限；未判断实测值合格性。")

    operations["C-03"] = tolerance

    def double_empty():
        if any(not key(row["product"]) and not key(row["process"]) for row in facts.values("product", "process")):
            raise DefectFact("W02", "部分有效工序行的产品与过程特性列同时为空。")

    operations["C-09"] = double_empty

    def characteristic_numbers():
        groups = defaultdict(list)
        for row in facts.values("number", "characteristic_number"):
            if not key(row["characteristic_number"]):
                continue
            value = _number(row["characteristic_number"])
            if value != int(value):
                raise UnknownFact("特性编号不是可核实的整数序列，需确认编号口径。")
            groups[str(row["number"])].append(int(value))
        if not groups:
            raise UnknownFact("没有已填写的特性编号，不能按空序列通过。")
        for numbers in groups.values():
            if len(numbers) != len(set(numbers)) or any(after != before + 1 for before, after in zip(numbers, numbers[1:])):
                raise DefectFact("W03", "同一工序的特性编号重复、跳号或倒序。")

    operations["C-10"] = characteristic_numbers

    def dates():
        from audit.precheck_pairs import optional_label

        values = facts.header_values([("编制日期", "创建日期"), ("修订日期",)])
        approval = optional_label(facts, facts.table, ("批准日期", "公司批准日期", "公司批准/日期", "公司批准（日期）", "Company Approval/Date"), facts.header)
        if approval is None and any(
            re.search(r"^(?!.*(?:不含|无批准|无需批准|没有批准)).*(?:批准.*日期|approval.*date)", str(effective(facts.table, row, column).get("value") or ""), re.I)
            for row in range(1, facts.header)
            for column in range(1, facts.table["cols"] + 1)
        ):
            raise UnknownFact("原件存在批准日期相关标签但尚未可靠定位，未按字段不存在关闭。")
        if approval is not None:
            values.append(approval)
        if any(not key(value) for value in values):
            raise DefectFact("W02", "已定位的签署日期字段未全部填写。")
        normalized = [normalize_excel_date(value) for value in values]
        if any(value is None for value in normalized):
            raise DefectFact("B05", "签署日期已填写但不能解析为有效日期。")
        known = [value for value in normalized if value is not None]
        if any(after < before for before, after in zip(known, known[1:])):
            raise DefectFact("W06", "卷面编制、修订、批准日期链逆序；本项按源分则只记警告。")
        return "已核实存在的签署日期链。" if approval is not None else "批准日期栏不存在，按S02关闭该段；已核实编制日期≤修订日期。"

    operations["C-13"] = dates

    def process_identity():
        numbers, names = defaultdict(set), defaultdict(set)
        for row in facts.values("number", "name"):
            number, name = str(row["number"] or "").strip(), str(row["name"] or "").strip()
            if not number or not name:
                raise UnknownFact("工序编号或名称未填写，无法核实一一对应。")
            numbers[number].add(name)
            names[name].add(number)
        if any(len(values) > 1 for values in [*numbers.values(), *names.values()]):
            raise DefectFact("W06", "同一工序号对应多个名称，或同名工序对应不同编号。")

    operations["C-17"] = process_identity

    def process_order():
        values = []
        for row in facts.values("number"):
            text = str(row["number"] or "").strip()
            match = re.fullmatch(r"(?:OP)?(\d+(?:\.\d+)?)", text, re.I)
            if not match:
                raise UnknownFact("工序编号未使用可比较的纯数字序列，未按文本排序推断倒序。")
            values.append(Decimal(match[1]))
        if any(after < before for before, after in zip(values, values[1:])):
            raise DefectFact("W03", "原件工序数字序列出现逆序。")

    operations["C-18"] = process_order

    header = _HEADER_UAES if customer == "UAES" else _HEADER_18
    operations["M-01"] = lambda: facts.header_values(header)

    def header_empty():
        labels = header if customer == "UAES" else header[1:]
        values = facts.header_values(labels)
        if any(not key(value) for value in values):
            raise DefectFact("W02", "已存在的必填表头字段尚未全部填写；缺栏未在此重复计数。")

    operations["M-02"] = header_empty

    def team():
        value = facts.label(facts.table, ("核心小组", "多功能小组"), facts.header)
        if len([item for item in re.split(r"[,，、;；/\n]+", str(value or "")) if item.strip()]) < 2:
            raise UnknownFact("核心小组尚未能识别为至少两名人员或两个职能；未凭字数推定。")

    if customer != "UAES":
        operations["M-03"] = team
    else:
        base["M-03"] = state_fact(preset_key + ":M-03", "S02", "UAES七项表头基线不含核心小组，不要求供应商给受控模板增加字段。")
    required = ["number", "name", "equipment", "symbol", "specification", "measurement", "sample", "reaction"]
    if customer != "UAES":
        required += ["product", "process", "frequency", "control"]
    operations["M-04"] = lambda: facts.values(*required)

    def symbol():
        if not any(key(row["symbol"]) for row in facts.values("symbol")):
            raise DefectFact("B05", "已定位的特殊特性分类列整列为空，特性核对没有可用输入。")

    operations["M-05"] = symbol

    def equipment():
        rows = [row for row in facts.values("name", "equipment") if not any(token in str(row["name"] or "") for token in ("人工目视", "纯目视"))]
        if not rows:
            raise UnknownFact("本次均为目视例外工序，未假定需要设备。")
        ratio = sum(bool(key(row["equipment"])) for row in rows) / len(rows)
        if ratio < 0.7:
            raise DefectFact("B05" if ratio < 0.3 else "W02", "非纯人工目视工序设备/工装字段填写率为" + str(round(ratio * 100, 1)) + "%。")

    operations["M-06"] = equipment

    def measurement():
        rows = facts.values("measurement")
        ratio = sum(bool(key(row["measurement"])) for row in rows) / len(rows)
        if ratio < 0.7:
            raise DefectFact("W02", "评价测量技术字段填写率低于70%；此项仅记警告。")

    operations["M-07"] = measurement

    def empty_shell():
        fields = ["measurement", "sample", "reaction"] + (["frequency", "control"] if customer != "UAES" else [])
        rows = facts.values(*fields)
        if all(not key(value) for row in rows for value in row.values()):
            raise DefectFact("B05", "适用管控字段全部为空，构成无法审核的空壳；保密公差留空未单独判缺陷。")

    operations["M-10"] = empty_shell

    from audit.precheck_cp_extra import additional_operations

    operations.update(additional_operations(facts, customer, group))
    result = []
    for identifier in criteria:
        if identifier in base:
            result.append(base[identifier])
        elif facts.multiple_tables and identifier not in {"X-01", "X-02", "X-03", "C-07", "M-13", "X-04", "P-01"}:
            result.append(state_fact(preset_key + ":" + identifier, None, "原件有多个控制计划数据表；本项单表范围需确认。", status="unknown"))
        elif identifier in operations:
            result.append(facts.observe(preset_key + ":" + identifier, operations[identifier]))
        else:
            result.append(state_fact(preset_key + ":" + identifier, None, "本项准备事实尚未核实；未把正式工程结论或未执行规则视为通过。"))
    return result
