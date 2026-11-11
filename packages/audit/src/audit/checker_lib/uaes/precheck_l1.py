"""UAES L1 preparation predicates over frozen originals, separate from verdicts.

Each operation is an explicit source criterion. No formal engineering threshold
is promoted into a preparation defect. Missing controlled structure remains S05.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl.utils.cell import coordinate_to_tuple

from audit.document_tools import effective
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.checkers.dimension_measurement_policy import parse_quality_standard_range
from audit.evidence import cell_locator
from audit.precheck.facts import DefectFact, SourceFacts, UnknownFact, key, state_fact
from audit.precheck.sources import l1_criteria

from .native import _IDENTITY
from .native import header as native_header

_ERRORS = {"#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#NULL!"}
_PLACEHOLDERS = {"/", "-", "—", "--", "tbd", "待填", "待定", "xxx", "xx", "____"}
_MARK = re.compile(r"[☑✓√✔■★×xX]")
_FOOTERS = ("编制人", "批准人", "供应商负责人", "结论：", "结论:", "填写说明", "填表说明")


def header(value, aliases):
    actual = key(value)
    for alias in aliases:
        wanted = key(alias)
        if actual == wanted:
            return True
        if re.search(r"[\u4e00-\u9fff]", str(alias)):
            if native_header(value, (alias,)):
                return True
        elif re.sub(r"[^a-z0-9]", "", actual) == re.sub(r"[^a-z0-9]", "", wanted):
            chinese = "".join(re.findall(r"[\u4e00-\u9fff]", actual))
            if wanted in {"cp", "cpk", "cpl", "cpu", "pp", "ppk", "ppl", "ppu"} and chinese not in {"", "值", "过程能力指数", "工程能力指数", "性能指数"}:
                continue
            return True
    return False


_FIELD_LABELS = {
    key(value)
    for value in (
        "零件号",
        "零件名称",
        "零件编号",
        "物料编号",
        "供应商名称",
        "供应商Supplier",
        "部件供应商名称",
        "材料名称和材料牌号",
        "单件重量",
        "序号",
        "特性名称",
        "量具名称",
        "量具编号",
        "上公差",
        "下公差",
        "公差带",
        "日期",
        "版本",
    )
}


def compact(value):
    return "".join(unicodedata.normalize("NFKC", str(value if value is not None else "")).split()).casefold()


def empty(value):
    return value is None or isinstance(value, str) and not value.strip()


def numeric(value, *, code="B05", strict=False):
    if empty(value):
        raise DefectFact("W02", "已定位的数值字段为空。")
    if strict and isinstance(value, str):
        raise DefectFact(code, "计量型数值字段以文本存储，须转换为原生数值。")
    try:
        if isinstance(value, bool):
            raise ValueError
        value = unicodedata.normalize("NFKC", str(value)).strip()
        result = Decimal(value.rstrip("%"))
        if not result.is_finite():
            raise ValueError
        return result / 100 if value.endswith("%") else result
    except (ValueError, InvalidOperation):
        raise DefectFact(code, "已读取的数值字段含不可解析文本。") from None


class MissingStructure(UnknownFact):
    """No matching native field, distinct from a present but unreadable field."""


_IDENTITY_FIELDS = {
    key(value)
    for value in (
        "零件号",
        "零件编号",
        "物料编号",
        "Part number",
        "Part No",
        "零件名称",
        "物料名称",
        "Part name",
        "供应商名称",
        "Supplier name",
        "供应商代码",
        "Supplier number",
        "项目",
        "项目名称",
        "日期",
        "测量日期",
        "Date of measuring",
        "版本",
        "版本号",
        "量具编号",
        "量具名称",
    )
}


class Facts(SourceFacts):
    context: Any = None

    def read(self, table, row, column, *, vertical_only=False):
        value = super().read(table, row, column, vertical_only=vertical_only)
        locator = {"file_version_id": self.audit.audit_id, **cell_locator(self.data["source"]["format"], table["id"], row, column).model_dump(mode="json")}
        source = {"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False, separators=(",", ":")), "excerpt": str(value if value is not None else "空单元格")[:500]}
        if source not in self.sources:
            self.sources.append(source)
            self._prune_sources()
        return value

    def _prune_sources(self):
        unique = []
        for source in self.sources:
            if source not in unique:
                unique.append(source)
        while len(unique) > 100:
            versions = [json.loads(source["locator"])["file_version_id"] for source in unique]
            redundant = next((index for index, version in enumerate(versions[:-1]) if versions.count(version) > 1), None)
            if redundant is None:
                raise UnknownFact("原件数超出证据预算，不能丢失一份原件后声称比对通过。")
            unique.pop(redundant)
        self.sources[:] = unique

    def evidence(self):
        self._prune_sources()
        return super().evidence()

    def tables(self):
        return [
            table
            for table in self.data["tables"]
            if not any(term in key(table["id"]) for term in ("填写指南", "填写说明", "填表说明", "userinstruction", "templatechangehistory", "评分准则", "判定准则"))
        ]

    def touch(self, table, row, column):
        """Retain an explicit Excel error literal without calling it numeric."""
        item = effective(table, row, column)
        locator = {**cell_locator(self.data["source"]["format"], table["id"], row, column).model_dump(mode="json"), "file_version_id": self.audit.audit_id}
        source = {"kind": "structure", "locator": json.dumps(locator, ensure_ascii=False, separators=(",", ":")), "excerpt": str(item.get("value"))[:500]}
        if source not in self.sources:
            self.sources.append(source)
        return item

    def cells(self):
        for table in self.tables():
            seen = set()
            for address in table["cells"]:
                row, column = coordinate_to_tuple(address)
                item = effective(table, row, column)
                anchor = item.get("anchor", address)
                if anchor in seen:
                    continue
                seen.add(anchor)
                if empty(item.get("value")) and item.get("state") == "known":
                    continue
                yield table, row, column, item

    def field_presence(self, aliases):
        found = False
        for table, row, column, item in self.cells():
            if item.get("state") == "known" and header(item.get("value"), aliases):
                self.read(table, row, column)
                found = True
        if not found:
            raise MissingStructure("未定位到结构字段" + aliases[0] + "，须核对受控模板（S05）。")

    def values(self, aliases, *, required=True, before=None):
        values = []
        self.value_positions = []
        for table in self.tables():
            for row in range(1, min(before or 80, table["rows"]) + 1):
                for column in range(1, table["cols"] + 1):
                    item = effective(table, row, column)
                    if item.get("state") != "known" or not header(item.get("value"), aliases):
                        continue
                    if item.get("anchor") != effective(table, row, column).get("cell"):
                        continue
                    self.read(table, row, column)
                    span = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
                    right = span[3] + 1 if span else column + 1
                    if right <= table["cols"]:
                        value = self.reference_value(table, row, right) if any(key(alias) in _IDENTITY_FIELDS for alias in aliases) else self.read(table, row, right)
                        if key(value) not in _FIELD_LABELS:
                            values.append(value)
                            self.value_positions.append((table, row, right))
        if not values:
            raise MissingStructure("未能定位字段" + aliases[0] + "；须核对受控模板结构（S05），不能推定供应商缺栏。")
        if required and any(empty(value) for value in values):
            raise DefectFact("W02", "已定位字段" + aliases[0] + "有栏无值。")
        return values

    def reference_value(self, table, row, column, seen=None):
        seen = set() if seen is None else seen
        identity = (table["id"], row, column)
        if identity in seen or len(seen) >= 16:
            raise UnknownFact("标识/汇总引用链循环或超出有界回溯范围。")
        seen.add(identity)
        value = self.read(table, row, column)
        formula = effective(table, row, column).get("formula")
        if not formula:
            return value
        match = re.fullmatch(r"=\s*(?:(?:'((?:[^']|'')+)'|([^'!]+))!)?\$?([A-Z]+)\$?(\d+)\s*", str(formula), re.I)
        if not match:
            raise UnknownFact("标识/汇总公式不是可安全回溯的单一内部引用，未采信无法验证的缓存值。")
        target_name = (match.group(1) or match.group(2) or table["id"]).replace("''", "'")
        if "[" in target_name or "]" in target_name:
            raise UnknownFact("标识公式指向外部工作簿，当前不能回溯其真实值。")
        targets = [target for target in self.tables() if target["id"] == target_name]
        if len(targets) != 1:
            raise UnknownFact("引用目标不在当前可读工作簿中，未虚构源值。")
        target_row, target_column = coordinate_to_tuple(match.group(3).upper() + match.group(4))
        return self.reference_value(targets[0], target_row, target_column, seen)

    def percentage(self, value, index=0):
        if isinstance(value, str) and value.strip().endswith("%"):
            self.percentage_unit = True
            return numeric(value) * 100
        table, row, column = self.value_positions[index]
        suffix = Path(self.audit.file_url).suffix.lower()
        display = ""
        if suffix in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook

            book = load_workbook(self.audit.file_url, read_only=True, data_only=False)
            try:
                display = book[table["id"]].cell(row, column).number_format
            finally:
                book.close()
        elif suffix == ".xls":
            import xlrd

            book = xlrd.open_workbook(self.audit.file_url, formatting_info=True)
            try:
                sheet = book.sheet_by_name(table["id"])
                xf = book.xf_list[sheet.cell_xf_index(row - 1, column - 1)]
                display = book.format_map[xf.format_key].format_str
            finally:
                book.release_resources()
        else:
            raise UnknownFact("百分比显示单位未能从原件可靠读取。")
        cleaned = re.sub(r'"[^"]*"', "", display)
        self.percentage_unit = "%" in cleaned
        return numeric(value) * 100 if re.search(r"(?<!\\)%", cleaned) else numeric(value)

    def one(self, aliases, *, required=True, before=None):
        values = self.values(aliases, required=required, before=before)
        if len({compact(value) for value in values}) != 1:
            raise UnknownFact("字段" + aliases[0] + "有多个不同值，不能任取一个。")
        return values[0]

    def columns(self, table, aliases):
        layouts = []
        best = 0
        for row in range(1, min(80, table["rows"]) + 1):
            columns = {}
            for name, names in aliases.items():
                found = []
                anchors = set()
                for column in range(1, table["cols"] + 1):
                    item = effective(table, row, column)
                    if item.get("state") == "known" and header(item.get("value"), names) and item["anchor"] not in anchors:
                        found.append(column)
                        anchors.add(item["anchor"])
                if len(found) > 1:
                    raise UnknownFact("同一表头含多个" + name + "候选，需确认列语义。")
                if found:
                    columns[name] = found[0]
            if len(columns) > best:
                best, layouts = len(columns), [(row, columns)]
            elif len(columns) == best and best:
                layouts.append((row, columns))
        if best != len(aliases):
            # The UAES material form has real leaf headers on rows 6-8.
            # Resolve exact leaf meanings within a bounded three-row band;
            # never treat the merged parent "部件状态" as every leaf value.
            composite = []
            for first in range(1, min(80, table["rows"]) + 1):
                columns = {}
                positions = {}
                for name, names in aliases.items():
                    candidates: dict = {}
                    for observed_row in range(first, min(first + 2, table["rows"]) + 1):
                        for column in range(1, table["cols"] + 1):
                            item = effective(table, observed_row, column)
                            if item.get("state") == "known" and header(item.get("value"), names):
                                candidates.setdefault(item["anchor"], (observed_row, column))
                    if len(candidates) == 1:
                        observed_row, column = next(iter(candidates.values()))
                        columns[name], positions[name] = column, observed_row
                if len(columns) == len(aliases) and len(set(columns.values())) == len(columns):
                    composite.append((first, columns, positions))
            if not composite:
                return None
            if len({tuple(sorted(columns.items())) for _, columns, _ in composite}) > 1:
                raise UnknownFact("多层表头存在多套不同语义列布局，需确认区块边界。")
            first, columns, positions = composite[0]
            for name, column in columns.items():
                self.read(table, positions[name], column)
            return first, columns
        if len({tuple(sorted(columns.items())) for _, columns in layouts}) > 1:
            raise UnknownFact("同一工作表存在多套业务列布局，需确认区块边界。")
        start, columns = layouts[0]
        if len(set(columns.values())) != len(columns):
            raise DefectFact("B06", "同一原件列被映射为两个不同语义字段。")
        for column in columns.values():
            self.read(table, start, column)
        return start, columns

    def regions(self, aliases, *, titles=()):
        result = []
        for table in self.tables():
            if titles and not any(
                key(term) in key(table["id"]) or any(key(term) in key(item.get("value")) for address, item in table["cells"].items() if coordinate_to_tuple(address)[0] <= 5) for term in titles
            ):
                continue
            binding = self.columns(table, aliases)
            if binding:
                result.append((table, *binding))
        if not result:
            raise MissingStructure("本项业务列尚未可靠定位；须先核对受控模板及别名（S05）。")
        return result

    def records(self, aliases, *, driver=None, titles=()):
        result = []
        for table, start, columns in self.regions(aliases, titles=titles):
            bottom = max([start] + [span[2] for span in table.get("merges", []) if span[0] <= start <= span[2] and any(span[1] <= column <= span[3] for column in columns.values())])
            for row in range(bottom + 1, table["rows"] + 1):
                raw = {name: effective(table, row, column) for name, column in columns.items()}
                if any(str(item.get("value") or "").startswith(_FOOTERS) for item in raw.values()):
                    break
                if all(empty(item.get("value")) and item.get("state") == "known" for item in raw.values()):
                    continue
                populated = [(name, item) for name, item in raw.items() if not empty(item.get("value")) or item.get("state") != "known"]
                if populated and all(item.get("state") == "known" and header(item.get("value"), aliases[name]) for name, item in populated):
                    continue
                values = Row(self, table, row, columns)
                if driver:
                    item = raw[driver]
                    if item.get("state") != "known" or empty(item.get("value")):
                        # Retain an ambiguous physical row lazily. A proven
                        # earlier defect remains observable, while a positive
                        # whole-scope check cannot silently drop this row.
                        values.ambiguous = True
                result.append((table, row, columns, values))
        if not result:
            raise DefectFact("B04", "已定位的业务表没有实际数据行，不能按空表通过。")
        return result

    def all_errors(self):
        unknown = False
        for table, row, column, item in self.cells():
            value = str(item.get("value") or "")
            if value.upper() in _ERRORS or re.fullmatch(r"#{3,}", value):
                self.touch(table, row, column)
                raise DefectFact("B05", "原件存在明确电子表格错误或井号截断值。")
            if item.get("state") != "known":
                unknown = True
            else:
                self.read(table, row, column)
        if unknown:
            raise UnknownFact("存在未计算公式或未知视觉内容，尚不能完成本项检查。")

    def text(self, phrase):
        found = False
        for table, row, column, item in self.cells():
            if item.get("state") == "known" and key(phrase) in key(item.get("value")):
                self.read(table, row, column)
                found = True
        return found


def execute(agent, entry, operations, *, states=None):
    try:
        facts = Facts(agent.audit_file)
    except Exception:
        return [state_fact(item["id"], None, "原件解析失败，尚无业务结论。", status="error") for item in l1_criteria(entry)]
    facts.context = agent.group_context
    result = []
    for item in l1_criteria(entry):
        identifier = item["criterion_id"]
        if states and identifier in states:
            code, message, status = states[identifier]
            result.append(state_fact(item["id"], code, message, status=status))
        elif identifier in operations:
            observed = facts.observe(item["id"], lambda operation=operations[identifier]: operation(facts))
            if observed["status"] == "unknown" and "S05" in observed["message"]:
                observed["code"] = "S05"
            result.append(observed)
        else:
            result.append(state_fact(item["id"], None, "本项尚缺可确认的范围、关联或视觉事实：" + item["criterion"], status="unknown"))
    return result


_MCR = {
    "id": ("序号", "测点号", "Pos. ID", "Pos ID"),
    "name": ("特性描述", "characteristic description"),
    "nominal": ("特性值", "characteristic value", "名义值"),
    "upper": ("公差上限", "upper tolerance", "上公差"),
    "lower": ("公差下限", "lower tolerance", "下公差"),
    "band": ("公差带", "tolerance range"),
    "uaes_method": ("UAES测量方法", "measuring process UAES"),
    "supplier_method": ("供应商测量方法", "measuring process supplier"),
}


def _mcr_columns(facts, table, start, side):
    found = []
    for column in range(1, table["cols"] + 1):
        observed = effective(table, start, column)
        value = compact(observed.get("value"))
        if value == side or re.fullmatch(side + r"[1-9]\d*", value) or value in {side + "实测值", side + "测量结果"}:
            facts.read(table, start, column)
            found.append(column)
    if not found:
        raise UnknownFact("未定位" + side + "独立样本列，不能借用另一侧的数据。")
    return found


class Row(dict):
    def __init__(self, facts, table, row, columns):
        super().__init__()
        self.facts, self.table, self.row, self.columns = facts, table, row, columns
        self.ambiguous = False

    def __getitem__(self, name):
        if self.ambiguous:
            raise UnknownFact("物理行主键未知或为空但有内容，需确认父子/拆分归属，不能按空行跳过。")
        if name in self.columns:
            return self.facts.read(self.table, self.row, self.columns[name], vertical_only=True)
        value = super().__getitem__(name)
        return value() if callable(value) else value

    def __iter__(self):
        return iter(dict.fromkeys([*self.columns, *super().keys()]))

    def __len__(self):
        return len(set(self.columns) | set(super().keys()))

    def keys(self):
        return tuple(iter(self))

    def items(self):
        return ((name, self[name]) for name in self)

    def values(self):
        return (self[name] for name in self)


def _mcr_records(facts):
    result = []
    for table, start, columns in facts.regions(_MCR):
        uaes = _mcr_columns(facts, table, start, "uaes")
        supplier = _mcr_columns(facts, table, start, "supplier")
        if set(uaes) & set(supplier):
            raise DefectFact("B06", "双方样本列发生重叠，不能完成独立比较。")
        differences: list[int] = []
        for column in range(1, table["cols"] + 1):
            value = compact(effective(table, start, column).get("value"))
            if "双方测量差异" in value or "differenceinpercentageoftolerance" in value or re.fullmatch(r"(?:差异率|diffpct)[1-9]\d*", value):
                span = next((span for span in table.get("merges", []) if span[0] <= start <= span[2] and span[1] <= column <= span[3]), None)
                differences.extend(range(span[1], span[3] + 1) if span else [column])
        differences = sorted(set(differences))
        for row in range(start + 1, table["rows"] + 1):
            identity = effective(table, row, columns["id"])
            description = effective(table, row, columns["name"])
            if any(str(item.get("value") or "").startswith(_FOOTERS) for item in (identity, description)):
                break
            text = str(identity.get("value") or "") + " " + str(description.get("value") or "")
            if re.search(r"\bi\.e\.|example|示例", text, re.I):
                continue
            if identity.get("state") != "known" or description.get("state") != "known":
                facts.read(table, row, columns["id"])
                facts.read(table, row, columns["name"])
            # An indexed blank template row has no measured object. Keep
            # partially filled objects; don't turn unknown drivers into blanks.
            activity = [effective(table, row, column) for column in uaes + supplier]
            if empty(description.get("value")) and all(empty(item.get("value")) and item.get("state") == "known" for item in activity):
                continue
            if header(identity.get("value"), _MCR["id"]) or not empty(description.get("value")) and header(description.get("value"), _MCR["name"]):
                continue
            if empty(identity.get("value")):
                raise UnknownFact("测量比较表存在不能确认测点归属的内容行。")
            facts.read(table, row, columns["id"])
            facts.read(table, row, columns["name"])
            values = Row(facts, table, row, columns)
            values["uaes"] = lambda table=table, row=row, uaes=uaes: [facts.read(table, row, column, vertical_only=True) for column in uaes]
            values["supplier"] = lambda table=table, row=row, supplier=supplier: [facts.read(table, row, column, vertical_only=True) for column in supplier]
            values["difference_columns"] = differences
            values["uaes_columns"] = uaes
            values["supplier_columns"] = supplier
            result.append((table, row, columns, values))
    if not result:
        raise DefectFact("B04", "排除示例、表头和未填索引行后，没有真实测量数据行。")
    return result


def _mcr_family(facts):
    signatures = 0
    if facts.text("测量比较报告") or facts.text("Measure Comparison Report") or facts.text("F-PU-166"):
        signatures += 1
    records = _mcr_records(facts)
    if all(len(row["uaes"]) >= 5 and len(row["supplier"]) >= 5 for _, _, _, row in records):
        signatures += 1
    labels = ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师")
    if all(facts.text(label) for label in labels):
        signatures += 1
    if signatures < 2:
        raise DefectFact("W01", "测量报告对象可读，但UAES模板族签名不足两项；族特定项需人工确认。")


def _mcr_numeric(facts):
    for _, _, _, row in _mcr_records(facts):
        for name in ("nominal", "upper", "lower"):
            if compact(row[name]) not in {"na", "n/a"}:
                numeric(row[name])
        for value in row["uaes"] + row["supplier"]:
            if not empty(value):
                numeric(value)
        if not empty(row["band"]) and compact(row["band"]) not in {"na", "n/a"}:
            numeric(row["band"])


def _mcr_direction(facts):
    for _, _, _, row in _mcr_records(facts):
        if compact(row["upper"]) in {"na", "n/a"} or compact(row["lower"]) in {"na", "n/a"}:
            continue
        if numeric(row["upper"]) < numeric(row["lower"]):
            raise DefectFact("B05", "公差上限小于公差下限，方向颠倒。")


def _mcr_width(facts):
    valid = 0
    for _, _, _, row in _mcr_records(facts):
        if compact(row["upper"]) in {"na", "n/a"} or compact(row["lower"]) in {"na", "n/a"}:
            continue
        valid += numeric(row["upper"]) - numeric(row["lower"]) > 0
    if not valid:
        raise DefectFact("B05", "整表没有大于零的可复算公差带；单行零宽度没有单独记缺陷。")


def _mcr_unique(facts):
    seen = set()
    for table, _, _, row in _mcr_records(facts):
        identity = (table["id"], compact(row["id"]))
        if identity in seen:
            raise DefectFact("B03", "同一测量页测点号重复。")
        seen.add(identity)


def _mcr_band(facts):
    for _, _, _, row in _mcr_records(facts):
        if compact(row["upper"]) in {"na", "n/a"} or compact(row["lower"]) in {"na", "n/a"}:
            continue
        expected = numeric(row["upper"]) - numeric(row["lower"])
        if expected and abs(numeric(row["band"]) - expected) > Decimal("0.000001"):
            raise DefectFact("B06", "公差带与上公差减下公差不一致；未采信模板上加下的说明。")


def _mcr_samples(facts):
    for _, _, _, row in _mcr_records(facts):
        for side in ("uaes", "supplier"):
            actual = sum(not empty(value) for value in row[side])
            if actual < 5:
                raise DefectFact("W01", "UAES测量比较双方每侧至少5件；" + side + "实填不足5件。")
            if actual < len(row[side]):
                raise DefectFact("W01", side + "已达最少5件，但实填样本列少于模板声明列数。")


def _mcr_required(facts):
    for _, _, _, row in _mcr_records(facts):
        if any(empty(row[name]) for name in ("name", "nominal", "upper", "lower", "uaes_method", "supplier_method")):
            raise DefectFact("W02", "测点已填写，但同类必填字段为空。")


def _mcr_pseudo(facts):
    failures = 0
    rows = _mcr_records(facts)
    for _, _, _, row in rows:
        values = row["uaes"] + row["supplier"]
        tokens = [compact(value) for value in values if not empty(value)]
        qualitative = any(token in {"ok", "nok", "ng", "合格", "不合格", "pass", "fail", "y", "n"} for token in tokens)
        numeric_values = any(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token) for token in tokens)
        attribute = numeric(row["upper"]) == numeric(row["lower"]) or any(term in str(row["name"]) + str(row["uaes_method"]) + str(row["supplier_method"]) for term in ("通止规", "Go-NoGo", "go-nogo"))
        if qualitative and numeric_values or attribute and any(token in {"0", "1", "1.0", "0.0"} for token in tokens):
            failures += 1
    if failures:
        raise DefectFact("B05" if failures == len(rows) else "W02", "测量行混有定性与数值，或属性特性用0/1伪装定量。")


def _mcr_qualitative(facts):
    for _, _, _, row in _mcr_records(facts):
        if any(compact(value) in {"合格", "不合格", "nok", "pass", "fail", "y", "n"} for value in row["uaes"] + row["supplier"]):
            raise DefectFact("W03", "定性样本列存在未标准化混用的结论值。")


def _mcr_diff(facts):
    """Recompute UAES absolute measurements; never use NIO centering."""
    found = False
    for table, row_number, _, row in _mcr_records(facts):
        differences = row["difference_columns"]
        if not differences:
            continue
        found = True
        width = numeric(row["upper"]) - numeric(row["lower"])
        if len(differences) != len(row["uaes"]) or len(row["uaes"]) != len(row["supplier"]):
            raise UnknownFact("双方实测与差异率列不能按样本一一对应。")
        for index, column in enumerate(differences):
            value = facts.read(table, row_number, column)
            if width == 0:
                if not empty(value):
                    raise DefectFact("B06", "零公差带的差异率格应为空，不能写定量差异率。")
                continue
            expected = (numeric(row["uaes"][index]) - numeric(row["supplier"][index])) / width
            if abs(numeric(value) - expected) > Decimal("0.000001"):
                raise DefectFact("W06", "UAES差异率与双方绝对实测差除以公差带的复算不一致。")
    if not found:
        raise UnknownFact("原件未定位到差异率列，不能声称派生值已复算。")


def _mcr_signatures(facts):
    for label in ("供应商", "UAES技术中心", "UAES测量部门", "UAES供应商质量开发工程师"):
        if not facts.text(label):
            raise UnknownFact("签字结构标签未能定位：" + label + "；须核对受控模板（S05）。")


def _mcr_dates(facts):
    values = facts.values(("测量日期", "date of measuring"))
    for value in values:
        parsed = normalize_excel_date(value)
        if parsed is None:
            raise DefectFact("B05", "测量日期不是合法日期。")
        if parsed == date(2017, 1, 10):
            raise DefectFact("W03", "测量日期仍为已知模板默认值2017-01-10，需确认。")
    return "日期可解析；提交日期未随冻结上下文提供，未执行晚于提交日期比较。"


def precheck_measurement(agent):
    operations = {
        "C-01": _mcr_numeric,
        "C-02": _mcr_direction,
        "C-03": _mcr_width,
        "C-05": _mcr_unique,
        "C-04": _mcr_magnitude,
        "C-08": _mcr_dates,
        "C-09": _mcr_numeric,
        "C-10": _mcr_errors,
        "C-11": _mcr_units,
        "C-12": _mcr_type_fields,
        "C-13": _mcr_band,
        "C-17": lambda f: _mcr_errors(f, derived=True),
        "C-14": _mcr_pseudo,
        "C-15": _mcr_qualitative,
        "M-01": _mcr_family,
        "M-02": _mcr_mapping_elements,
        "M-11": _mcr_version,
        "M-03": _mcr_meta,
        "M-04": lambda f: f.regions(_MCR),
        "M-05": _mcr_required,
        "M-06": _mcr_records,
        "M-07": _mcr_samples,
        "M-08": _mcr_records,
        "M-10": _mcr_signatures,
        "M-12": _mcr_result_region,
        "M-14": _mcr_family,
        "M-16": _mcr_examples,
        "M-15": _mcr_two_sides,
        "M-17": _mcr_records,
        "S-01": _mcr_internal_metadata,
        "S-02": _mcr_index,
        "S-03": _mcr_filename,
        "S-05": _mcr_mean_range,
        "S-09": _mcr_version,
        "S-11": _mcr_references,
        "S-12": _mcr_zero_empty,
        "S-16": _mcr_model_phase,
        "S-10": _mcr_samples,
        "S-14": lambda f: _mcr_batch(f, agent),
        "S-15": lambda f: _mcr_batch(f, agent, history=True),
        "S-17": _mcr_state_carrier,
        "S-18": _mcr_preservation,
        "S-19": lambda f: f.regions(_MCR),
    }
    states: dict[str, tuple[str | None, str, str]] = {
        identifier: ("S01", "UAES_MCR无方向码、Centering/Range或测点图要求；未套用NIO规则。", "not_checked") for identifier in ("C-06", "C-07", "C-16", "M-09", "S-04", "S-06")
    }
    states["S-07"] = ("S03", "合格率与超差结论移交正式审核，预检不评通过方向。", "handoff")
    result = execute(agent, "U02", operations, states=states)
    result.extend(_mcr_element_facts(agent))
    result = _mcr_context_states(agent, result)
    paired = _paired_original_fact(agent, "U02:S-08", measurement=True)
    result = [paired if item["id"] == "U02:S-08" else item for item in result]
    # A separate derived-value fact preserves exact root criterion semantics:
    # S-05 is only means/ranges; UAES diff_pct is not NIO Centering.
    facts = Facts(agent.audit_file)
    result.append(facts.observe("U02:diff_pct", lambda: _mcr_diff(facts)))
    return result


def _mcr_errors(facts, *, derived=False):
    for table, row_number, columns, row in _mcr_records(facts):
        audited = row["difference_columns"] if derived else [columns[name] for name in ("nominal", "upper", "lower", "band")] + row["uaes_columns"] + row["supplier_columns"]
        for column in audited:
            item = effective(table, row_number, column)
            if str(item.get("value") or "").upper() in _ERRORS:
                facts.touch(table, row_number, column)
                raise DefectFact("W05" if derived else "B05", "派生差异率错误须重算。" if derived else "判定依据列含明确电子表格错误。")
            facts.read(table, row_number, column)


def _mcr_meta(facts):
    for aliases in [("零件号", "Part number"), ("零件名称", "Part name"), ("供应商名称", "Supplier name")]:
        value = facts.one(aliases, before=13)
        if compact(value) in _PLACEHOLDERS or compact(value) in {"na", "n/a", "000000", "123456"}:
            raise DefectFact("B03", "测量比较报告关键标识仍为占位值。")


def _mcr_two_sides(facts):
    for _, _, _, row in _mcr_records(facts):
        for side in ("uaes", "supplier"):
            if not any(not empty(value) for value in row[side]):
                raise DefectFact("B05", "双边测量比较的" + side + "侧没有任何实测值。")
    for aliases in [("测量员", "measured by"), ("测量日期", "date of measuring"), ("测量程序名称", "program name")]:
        values = facts.values(aliases, before=13)
        if len(values) != 2:
            raise UnknownFact("双方" + aliases[0] + "尚不能分别定位，不能借用一侧信息。")


def _mcr_magnitude(facts):
    for _, _, _, row in _mcr_records(facts):
        upper, lower = numeric(row["upper"]), numeric(row["lower"])
        width = upper - lower
        if width > 0:
            for left, right in zip(row["uaes"], row["supplier"], strict=True):
                if abs(numeric(left) - numeric(right)) > 10 * width:
                    raise DefectFact("B05", "双方测量偏差超过正公差带十倍，量级异常。")
    return "已检查UAES双边偏差量级；本族不设坐标列。"


def _mcr_units(facts):
    groups = defaultdict(set)
    for _, _, _, row in _mcr_records(facts):
        for side in ("uaes", "supplier"):
            for index, value in enumerate(row[side]):
                unit = re.search(r"(mm|[μµu]m)\s*$", str(value), re.I)
                if unit:
                    groups[side, index].add(unit.group(1).casefold().replace("µ", "μ").replace("um", "μm"))
    if any(len(units) > 1 for units in groups.values()):
        raise DefectFact("B05", "同一测量列中混有mm与μm单位。")


def _mcr_type_fields(facts):
    name = facts.one(("零件名称", "Part name"), before=13)
    part = facts.one(("零件号", "Part number"), before=13)
    if normalize_excel_date(name) is not None:
        raise DefectFact("W03", "零件名称位置填入日期。")
    if re.fullmatch(r"[\u4e00-\u9fff]+", str(part).strip()):
        raise DefectFact("B03", "零件号位置填入纯中文名称，关键类型错位。")


def _mcr_result_region(facts):
    if not facts.text("可以接受") and not facts.text("accepted") and not facts.text("结论"):
        raise UnknownFact("未定位判定区；须核对受控模板（S05），不能推定实际签批方向。")
    return "判定区结构可定位；实际接受/否决方向移交正式审核。"


_MSA_PLAN = ("测量系统分析计划", "MSA计划表", "MSA plan")
_MSA_GAUGES = {"id": ("量具编号", "测量设备编号", "检具编号"), "name": ("量具名称", "测量设备名称", "检具名称")}


def _msa_plan(facts, extra=None):
    return facts.records({"serial": ("序号", "NO", "No."), **(extra or {})}, driver="serial", titles=_MSA_PLAN)


def _msa_blank(facts):
    reports = False
    for table in facts.tables():
        if any(term in key(table["id"]) for term in ("msa计划", "测量系统分析计划")):
            reports = True
            _msa_plan(facts, _MSA_GAUGES)
        elif any(term in key(table["id"]) for term in ("grr", "kappa", "重复性", "再现性")):
            reports = True
            child = _table_facts(facts, table)
            values = child.values(("实测值", "测量值", "测量结果"), required=False)
            if all(empty(value) for value in values):
                raise DefectFact("B05", "测量系统分析报告的测量区整体为空。")
    if not reports:
        raise UnknownFact("尚不能确定全部MSA计划/报告表单及真实测量区域。")


def _table_facts(facts, table):
    child = object.__new__(Facts)
    child.audit = facts.audit
    child.data = {**facts.data, "tables": [table]}
    child.sources = facts.sources
    return child


def _msa_draft(facts):
    for table, row, column, item in facts.cells():
        if item.get("state") != "known":
            facts.read(table, row, column)
        value = str(facts.read(table, row, column) or "")
        if compact(value) in _PLACEHOLDERS or re.search(r"\bdraft\b|草稿|待填|待定|\bDEMO\b", value, re.I):
            raise DefectFact("W02", "原件存在未清除的草稿或占位内容。")
    if Path(facts.audit.file_url).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise UnknownFact("文字残稿已检查；当前载体的高亮底色含义尚未核实。")
    from openpyxl import load_workbook

    book = load_workbook(facts.audit.file_url, read_only=False, data_only=False)
    try:
        for sheet in book.worksheets:
            populated = [cell for cells in sheet.iter_rows() for cell in cells if cell.value is not None]
            if (
                populated
                and sum(cell.fill.patternType == "solid" and cell.fill.fgColor.type == "rgb" and cell.fill.fgColor.rgb not in {"00000000", "00FFFFFF", "FFFFFFFF", "00000000"} for cell in populated)
                > len(populated) // 2
            ):
                raise UnknownFact("存在大面积底色；须先确认受控模板配色，未直接当成草稿缺陷。")
    finally:
        book.close()


def _msa_numeric_cells(facts):
    rows = facts.records({"value": ("实测值", "测量值", "测量结果", "试验值")})
    bad = 0
    for _, _, _, row in rows:
        value = row["value"]
        if empty(value):
            continue
        try:
            numeric(value)
        except DefectFact:
            bad += 1
    if bad:
        raise DefectFact("B05" if bad == len(rows) else "W03", "MSA数值区存在不可解析的数值字符；未把文字说明当数据。")


def _msa_conclusion(facts):
    reports = [table for table in facts.tables() if any(word in key(table["id"]) for word in ("kappa", "grr", "重复性", "再现性"))]
    if not reports:
        raise UnknownFact("当前未定位到报告表；须确认声明范围是否包含报告。")
    for table in reports:
        child = _table_facts(facts, table)
        try:
            values = child.values(("结论", "最终结论", "conclusion"), required=False)
        except MissingStructure:
            values = []
        if any(
            str(value or "").strip() and (any(term in compact(value) for term in ("acceptable", "接受", "不可接受", "不接受", "测量系统可以接受")) or bool(_MARK.search(str(value))))
            for value in values
        ):
            continue
        grr = child.one(("%GRR", "%R&R", "GRR%", "R&R%"))
        ndc = child.one(("ndc", "可区分类别数"))
        numeric(grr)
        numeric(ndc)
    return "各报告至少有一种可判定结论表达；没有判断接受方向是否正确。"


def _msa_identity(facts):
    rows = _msa_plan(facts, {**_MSA_GAUGES, "part": ("零件号", "零件编号", "物料编号")})
    for field in ("id", "name", "part"):
        empties = sum(empty(row[field]) for _, _, _, row in rows)
        if empties == len(rows):
            raise DefectFact("B03" if field == "id" else "B05", "MSA关键标识" + field + "整列为空。")
        if empties:
            raise DefectFact("W02", "MSA关键标识" + field + "部分行为空。")


def _msa_core(facts):
    aliases = {"range": ("测量范围", "量程"), "resolution": ("精度或分辨率", "分辨率", "精度"), "analysis": ("分析项目", "分析类型"), "id": _MSA_GAUGES["id"]}
    rows = _msa_plan(facts, aliases)
    for name in aliases:
        if all(empty(row[name]) for _, _, _, row in rows):
            raise DefectFact("B05", "MSA计划核心列" + name + "整列为空。")


def _msa_controlled(facts):
    rows = _msa_plan(facts)
    for _, _, _, row in rows:
        if not re.fullmatch(r"\d+(?:\.0)?", str(row["serial"]).strip()):
            raise DefectFact("B04", "MSA序号列被非序号内容占用，行归属不可确认。")
    for aliases in [("文件编号",), ("版本号", "版本"), ("日期",)]:
        try:
            values = facts.values(aliases, required=False, before=15)
        except MissingStructure:
            continue
        if any(compact(value) in _PLACEHOLDERS | {"0"} for value in values):
            raise DefectFact("W02", "MSA受控字段仍为占位值。")


def _msa_gauge_map(facts):
    rows = _msa_plan(facts, _MSA_GAUGES)
    names: dict[str, str] = {}
    described = 0
    for _, _, _, row in rows:
        identity, name = compact(row["id"]), compact(row["name"])
        if identity in names and names[identity] != name:
            raise DefectFact("W04", "同一量具编号对应不同量具名称。")
        names[identity] = name
        if identity == name or re.fullmatch(r"[\u4e00-\u9fff]+", identity):
            described += 1
    if described:
        raise DefectFact("B03" if described == len(rows) else "W03", "量具编号字段填入量具名称或描述，不能用于编号匹配。")
    for table in facts.tables():
        if any(word in key(table["id"]) for word in ("grr", "kappa", "重复性", "再现性")):
            value = _table_facts(facts, table).one(_MSA_GAUGES["id"])
            if re.search(r"[,，;；/]", str(value)):
                raise DefectFact("W04", "单张报告表头存在多个量具编号。")


def _msa_units(facts):
    rows = _msa_plan(facts, {**_MSA_GAUGES, "resolution": ("精度或分辨率", "分辨率", "精度"), "range": ("测量范围", "量程"), "object": ("被测对象", "测量对象"), "unit": ("单位", "测量单位")})
    for _, _, _, row in rows:
        unit = compact(row["unit"])
        obj = str(row["object"])
        if ("Ra" in obj or "Rz" in obj or "粗糙度" in obj) and unit not in {"μm", "µm", "um"} or "硬度" in obj and unit == "mm" or "扭矩" in obj and unit == "n":
            raise DefectFact("W03", "MSA被测对象与物理量单位明显不匹配。")
        if "游标卡尺" in str(row["name"]) and numeric(row["resolution"]) == Decimal(".001") and unit == "mm":
            raise DefectFact("W03", "游标卡尺分辨率填为0.001mm，需核对。")
        for name in ("resolution", "range"):
            if compact(row[name]) == compact(row["name"]) or compact(row[name]) == compact(row["object"]):
                raise DefectFact("W03", "MSA测量范围/分辨率填入对象名称。")


def _msa_resolution(facts):
    rows = _msa_plan(facts, {"resolution": ("分辨率", "精度或分辨率", "精度"), "upper": ("上公差", "公差上限"), "lower": ("下公差", "公差下限")})
    for _, _, _, row in rows:
        band = numeric(row["upper"]) - numeric(row["lower"])
        if band <= 0:
            raise UnknownFact("规格公差带非正，尚不能执行分辨率十分之一比较。")
        if numeric(row["resolution"]) > band / 10:
            raise DefectFact("W06", "量具分辨率大于规格公差带的十分之一。")


def _msa_design(facts):
    found = False
    for table in facts.tables():
        child = _table_facts(facts, table)
        if any(word in key(table["id"]) for word in ("grr", "重复性", "再现性")):
            found = True
            repeats = numeric(child.one(("重复次数", "试验次数", "r")))
            operators = numeric(child.one(("评价人数", "操作者人数", "k")))
            parts = numeric(child.one(("零件数", "样件数", "n")))
            if repeats < 2:
                raise DefectFact("B05", "计量型MSA重复次数r小于2。")
            if operators < 2 or parts < 5:
                raise DefectFact("W06", "计量型MSA评价人数k小于2或零件数n小于5。")
        elif "kappa" in key(table["id"]):
            found = True
            rows = child.records({"serial": ("样本序号", "样件序号", "样本编号"), "reference": ("基准结果", "基准值", "标准结果")}, driver="serial")
            total = max(numeric(row["serial"]) for _, _, _, row in rows)
            if total < 50:
                raise DefectFact("W06", "计数型MSA最大样本序号小于50。")
            if sum(compact(row["reference"]) in {"ng", "不合格", "nok"} for _, _, _, row in rows) / len(rows) < 0.15:
                raise DefectFact("W06", "计数型MSA基准NG件占比小于15%。")
    if not found:
        raise UnknownFact("未定位到可验证实验设计的GRR/Kappa报告。")


def _msa_percent(facts):
    found = False
    for aliases in [("%R&R", "%GRR", "%P/T"), ("ndc", "可区分类别数")]:
        try:
            values = facts.values(aliases)
        except MissingStructure:
            continue
        found = True
        for index, value in enumerate(values):
            number = numeric(value) if aliases[0] == "ndc" else facts.percentage(value, index)
            if aliases[0] == "ndc":
                if number < 0 or number > 50:
                    raise DefectFact("W06", "ndc为负值或超过50，需人工确认。")
            else:
                if 0 < number < 1 and not facts.percentage_unit:
                    raise DefectFact("W03", "百分比字段以0至1的小数填写，需核对百分比量纲。")
                if number < 0 or number > 100:
                    raise DefectFact("W03", "百分比字段超出0至100。")
    if not found:
        raise UnknownFact("未定位到百分比或ndc指标字段。")


def precheck_msa(agent):
    result = execute(
        agent,
        "U05",
        {
            "MSA-PRE-01": lambda f: f.all_errors(),
            "MSA-PRE-02": _msa_blank,
            "MSA-PRE-03": _msa_draft,
            "MSA-PRE-04": _msa_numeric_cells,
            "MSA-PRE-05": _msa_sets,
            "MSA-PRE-06": lambda f: _msa_sets(f, attributes=True),
            "MSA-PRE-07": _msa_metadata,
            "MSA-PRE-08": _msa_gauge_map,
            "MSA-PRE-09": _msa_units,
            "MSA-PRE-10": _msa_resolution,
            "MSA-PRE-11": _msa_design,
            "MSA-PRE-12": _msa_static,
            "MSA-PRE-13": _msa_scope,
            "MSA-PRE-14": _msa_declared_types,
            "MSA-PRE-21": _msa_versions,
            "MSA-PRE-16": _msa_conclusion,
            "MSA-PRE-17": _msa_identity,
            "MSA-PRE-18": _msa_core,
            "MSA-PRE-19": _msa_controlled,
            "MSA-PRE-20": _msa_percent,
        },
    )
    scope = _msa_relation_scope(agent)
    return [scope if item["id"] == "U05:MSA-PRE-15" else item for item in result]


def _msa_static(facts):
    found = False
    personnel_dates = set()
    for table in facts.tables():
        if not any(word in key(table["id"]) for word in ("grr", "重复性", "再现性")):
            continue
        found = True
        child = _table_facts(facts, table)
        r = numeric(child.one(("重复次数", "试验次数", "r")))
        k = numeric(child.one(("评价人数", "操作者人数", "k")))
        n = numeric(child.one(("零件数", "样件数", "n")))
        if (r, k, n) == (3, 3, 10):
            for aliases, expected in [(("K1", "K₁"), ".5908"), (("K2", "K₂"), ".5231"), (("K3", "K₃"), ".3146"), (("D4", "D₄"), "2.575")]:
                if numeric(child.one(aliases)) != Decimal(expected):
                    raise DefectFact("B06", "3×3×10卷面静态常数与源规定值不一致：" + aliases[0])
        grr = child.one(("%GRR", "%R&R", "GRR%"))
        percentage = child.percentage(grr)
        ndc = numeric(child.one(("ndc", "可区分类别数")))
        conclusion = compact(child.one(("结论", "最终结论", "conclusion")))
        accepted = conclusion in {"可接受", "测量系统可以接受", "acceptable", "☑可接受", "☑acceptable"}
        if accepted and (percentage > 30 or ndc < 5):
            raise DefectFact("W06", "卷面%GRR>30%或ndc<5，却明确勾选/填写可接受。")
        measured = normalize_excel_date(child.one(("实测日期", "测量日期", "分析日期")))
        planned = normalize_excel_date(child.one(("计划日期",)))
        if measured is None or planned is None:
            raise UnknownFact("实际/计划日期无法可靠解析，尚不能核对时间顺序。")
        if measured < planned:
            raise DefectFact("W06", "实测日期早于计划日期。")
        personnel = compact(child.one(("评价人姓名", "评价人", "操作者")))
        identity = (personnel, measured)
        if identity in personnel_dates:
            raise DefectFact("W06", "同组人员在同一天出具多份报告，需确认记录真实性。")
        personnel_dates.add(identity)
    if not found:
        raise UnknownFact("未定位到适用静态常数及准则的计量型MSA报告。")


def _msa_scope(facts):
    value = compact(facts.one(("提交范围", "本次范围")))
    if not value:
        raise DefectFact("W01", "未填写可辨识的MSA提交范围声明。")
    known_plan = any("计划" in str(table["id"]) for table in facts.tables())
    known_report = any(any(term in key(table["id"]) for term in ("grr", "kappa", "重复性", "再现性")) for table in facts.tables())
    if "计划" in value and not known_plan or any(term in value for term in ("报告", "grr", "kappa")) and not known_report:
        raise UnknownFact("声明含相关表单，但其可能由同批独立文件承载；本项未把当前文件缺表判为批次缺件。")
    return "已核对本文件中的范围声明与实际表单；未推定本批应收范围。"


def _msa_sets(facts, *, attributes=False):
    scope = compact(facts.one(("提交范围", "本次范围")))
    if "计划" not in scope or not any(term in scope for term in ("报告", "grr", "kappa")):
        raise UnknownFact("计划与报告未同时明确处于声明范围，不能强制双向齐套。")
    plans = {}
    for _, _, _, row in _msa_plan(facts, {**_MSA_GAUGES, "part": ("零件号", "零件编号"), "analysis": ("分析项目", "分析类型")}):
        identity = compact(row["id"])
        if identity in plans:
            raise UnknownFact("计划内量具编号重复，需明确同编号的适用范围。")
        plans[identity] = (compact(row["part"]), compact(row["analysis"]))
    reports = {}
    for table in facts.tables():
        if not any(word in key(table["id"]) for word in ("grr", "kappa", "重复性", "再现性")):
            continue
        child = _table_facts(facts, table)
        identity = compact(child.one(_MSA_GAUGES["id"]))
        if identity in reports:
            raise UnknownFact("同一量具编号存在多个报告，须确认版本与分析用途。")
        kind = "计数型" if "kappa" in key(table["id"]) else "计量型"
        reports[identity] = (compact(child.one(("零件号", "零件编号"))), kind)
    if facts.context is None or len(facts.context.by_audit_id) != 1:
        raise UnknownFact("集合属性已读取；本项需核对全部冻结报告原件后做双向求差，未把当前文件之外的报告判缺失。")
    if set(plans) - set(reports):
        raise DefectFact("B02", "声明范围内计划有量具而报告未覆盖：" + ",".join(sorted(set(plans) - set(reports))))
    if set(reports) - set(plans):
        raise DefectFact("W04", "声明范围内报告有量具而计划未登记：" + ",".join(sorted(set(reports) - set(plans))))
    if attributes:
        for identity, planned in plans.items():
            reported = reports[identity]
            if planned[0] != reported[0] or planned[1] not in {reported[1], "kappa" if reported[1] == "计数型" else "grr"}:
                raise DefectFact("W04", "同编号量具在计划与报告中的被测零件或分析类型冲突。")


_MATERIAL = {"serial": ("序号", "NO", "No."), "part": ("零件号", "零件编号", "Part No"), "name": ("零件/部件名称", "零件名称", "部件名称", "Part/Component Name")}
_WEIGHT = ("单件重量", "单重", "Mass", "Mass (Kg)")
_MATERIAL_FIELDS = {
    "material": ("材料名称和材料牌号", "材料名称及牌号", "材料牌号"),
    "raw_supplier": ("材料供应商名称(生产地点)", "材料供应商名称", "材料生产商"),
    "supplier": ("部件供应商名称(生产地点)", "部件供应商名称", "部件供应商"),
}


def _material_rows(facts, extra=None):
    return facts.records({**_MATERIAL, **(extra or {})}, driver="serial", titles=("材料", "分供方", "material"))


def _material_ids(facts):
    for _, _, _, row in _material_rows(facts):
        part, name = str(row["part"] or "").strip(), str(row["name"] or "").strip()
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) or re.fullmatch(r"(?=.*\d)[A-Za-z0-9_.-]+", name):
            raise DefectFact("B03", "材料清单零件号/名称存在明显类型颠倒。")


def _material_placeholders(facts):
    for _, _, _, row in _material_rows(facts):
        if compact(row["part"]) in _PLACEHOLDERS | {"123456", "111111", "000000"} or re.search(r"[xX*]{3,}", str(row["part"] or "").strip()):
            raise DefectFact("B03", "材料清单零件号为占位或脱敏假号。")


def _material_weights(facts):
    for _, _, _, row in _material_rows(facts, {"weight": _WEIGHT}):
        if empty(row["weight"]):
            continue
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(row["weight"]).strip()):
            raise DefectFact("B05", "单件重量不是单一数值，含单位、说明或约数。")
        numeric(row["weight"])


def _material_numbers(facts):
    groups = defaultdict(list)
    for table, _, _, row in _material_rows(facts):
        groups[table["id"]].append(str(row["serial"]).strip())
    for values in groups.values():
        if len(set(values)) != len(values):
            raise DefectFact("B04", "同一材料切片序号重复。")
        if not all(re.fullmatch(r"\d+(?:\.\d+)*", value) for value in values):
            raise UnknownFact("序号体系不能可靠解释为层级序号，需确认拆分行。")
        previous = None
        for value in values:
            path = tuple(int(part) for part in value.split("."))
            if previous is not None:
                if path <= previous:
                    raise DefectFact("B04", "材料清单序号倒序。")
                if len(path) == len(previous) and path[:-1] == previous[:-1] and path[-1] != previous[-1] + 1:
                    raise DefectFact("B04", "材料清单同层序号跳号。")
                if len(path) > len(previous) + 1:
                    raise DefectFact("B04", "材料清单层级序号跳级。")
            previous = path


def _material_levels(facts):
    rows = _material_rows(facts, {"level": ("装配级别", "部件装配级别", "Component ASM Level", "层级")})
    prior = None
    for _, _, _, row in rows:
        value = row["level"]
        if not re.fullmatch(r"\d+(?:\.0)?", str(value).strip()):
            raise UnknownFact("装配级别为多列勾选或非标层级，需先解码其实际选择。")
        level = int(numeric(value))
        if prior is None and level not in {0, 1}:
            raise DefectFact("B04", "材料清单没有可识别根节点层级。")
        if prior is not None and level > prior + 1:
            raise DefectFact("B04", "材料清单相邻行层级跳级。")
        prior = level


def _material_primary(facts):
    facts.one(("零件号", "零件编号", "Part number", "Part No"), before=5)
    facts.one(("零件名称", "Part name"), before=5)
    try:
        values = facts.values(("供应商名称", "Supplier name"), before=5)
        if any(not empty(value) for value in values):
            return
    except MissingStructure:
        pass
    try:
        supplier_rows = facts.records({"supplier": ("供应商Supplier", "供应商")})
        if any(not empty(row["supplier"]) for _, _, _, row in supplier_rows):
            return "供应商由UAES明细Supplier列承载，未强制表头供应商栏。"
    except MissingStructure:
        pass
    rows = _material_rows(facts, {"supplier": _MATERIAL_FIELDS["supplier"]})
    if not any(not empty(row["supplier"]) for _, _, _, row in rows):
        raise DefectFact("B03", "材料清单表头及明细部件供应商两处均无供应商内容。")
    return "零件身份已读取；供应商允许由明细供应商列承载，没有强制表头供应商栏。"


def _material_root(facts):
    rows = _material_rows(facts)
    groups = defaultdict(list)
    for table, _, _, row in rows:
        groups[table["id"]].append(row)
    for table in facts.tables():
        if table["id"] not in groups:
            continue
        child = _table_facts(facts, table)
        part = child.one(("零件号", "零件编号", "Part number", "Part No"), before=5)
        name = child.one(("零件名称", "Part name"), before=5)
        first = groups[table["id"]][0]
        if compact(part) != compact(first["part"]) or compact(name) != compact(first["name"]):
            raise UnknownFact("表头与根节点使用不同编码或名称，须确认是否有合法编码体系说明，未直接判为错误。")


def _material_same(facts, *, across=False):
    rows = _material_rows(facts, _MATERIAL_FIELDS)
    seen: dict = {}
    for table, _, _, row in rows:
        identity = compact(row["part"]) if across else (table["id"], compact(row["part"]))
        values = tuple(compact(row[name]) for name in ("name", "material", "raw_supplier", "supplier"))
        if identity in seen and seen[identity] != values:
            raise DefectFact("W04", "同零件号的名称、材料牌号或供应商存在冲突。")
        seen[identity] = values


def _material_suppliers(facts):
    for _, _, _, row in _material_rows(facts, {"supplier": _MATERIAL_FIELDS["supplier"], "raw": _MATERIAL_FIELDS["raw_supplier"]}):
        for name in ("supplier", "raw"):
            value = str(row[name] or "").strip()
            if not value or re.fullmatch(r"[\u4e00-\u9fff]{1,4}(?:省|市|县|区)", value):
                raise DefectFact("W01", "供应商名称为空或仅为明确的行政地名，须填写主体名称。")
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value):
                raise UnknownFact("短中文供应商名称是否仅为地名尚未确认，不能凭字数判缺陷。")


def _material_semantics(facts, kind):
    if kind == "size":
        rows = _material_rows(facts, {"value": ("尺寸", "零件尺寸", "材料尺寸")})
        for _, _, _, row in rows:
            if re.search(r"镀层|涂层|胶水|注塑|电泳|喷涂|压铸", str(row["value"])):
                raise DefectFact("W03", "尺寸字段混入材料表面处理或工艺描述。")
    elif kind == "standard":
        rows = _material_rows(facts, {"value": ("执行标准", "材料标准", "Material Specification")})
        for _, _, _, row in rows:
            if re.search(r"有限公司|公司地址|联系人|联系电话|\b\d{11}\b|@", str(row["value"])):
                raise DefectFact("W03", "执行标准字段填入企业或联系信息。")
    else:
        rows = _material_rows(facts, {"forming": ("成型工艺", "制造工艺"), "surface": ("表面处理", "表面处理工艺")})
        for _, _, _, row in rows:
            if compact(row["forming"]) == compact(row["surface"]) and any(term in str(row["surface"]) for term in ("电泳", "电镀", "喷涂", "阳极氧化", "磷化")):
                raise DefectFact("W06", "成型工艺与表面处理填入同一表面处理词。")


def _material_status(facts):
    aliases = {"self": ("自制", "Homemade"), "local": ("外协-国产", "外协国产", "国产 Local"), "import": ("外协-进口", "外协进口", "进口 Import"), "direct": ("领用件", "指定件", "Direct Buy")}
    rows = _material_rows(facts, aliases)
    for _, _, _, row in rows:
        count = 0
        for name in aliases:
            value = str(row[name] or "").strip()
            if _MARK.fullmatch(value) or compact(value) in {"1", "yes", "y", "true", "是"}:
                count += 1
            elif value and compact(value) not in {"0", "no", "n", "false", "否", "/", "-"}:
                raise UnknownFact("实现方式包含未知选择符，不能推定未勾选。")
        if count != 1:
            raise DefectFact("B06", "非拆分材料行的产品实现方式没有恰好选择一项。")


def _material_signatures(facts):
    for phrase in ("供应商负责工程师", "UAES NE工程师", "UAES SQD/SQE工程师"):
        if not facts.text(phrase):
            raise UnknownFact("UAES三方签字栏尚未全部定位，须核对受控模板（S05）。")
    return "三方签字栏结构已定位；没有检查是否已实际签署。"


def _material_leaf_weights(facts):
    rows = _material_rows(facts, {"level": ("装配级别", "部件装配级别", "层级"), "weight": _WEIGHT})
    for index, (_, _, _, row) in enumerate(rows):
        level = numeric(row["level"])
        leaf = index == len(rows) - 1 or numeric(rows[index + 1][3]["level"]) <= level
        if leaf and empty(row["weight"]):
            raise DefectFact("W02", "叶子节点单件重量为空，存在父重断链风险。")


def precheck_materials(agent):
    result = execute(
        agent,
        "U04",
        {
            "PC-A1": _material_filelist,
            "PC-A2": _material_current_scope,
            "PC-A3": lambda f: _material_current_scope(f, part=True),
            "PC-A4": _material_effective_version,
            "PC-A8": _material_declared_dependencies,
            "PC-A5": _material_generation,
            "PC-A6": lambda f: _material_rows(f),
            "PC-A7": _material_valid,
            "PC-B1": _material_primary,
            "PC-B2": _material_root,
            "PC-B3": _material_levels,
            "PC-B4": _material_numbers,
            "PC-B6": lambda f: _material_rows(f, _MATERIAL_FIELDS),
            "PC-B7": lambda f: _material_rows(f),
            "PC-B5": _material_assembly_children,
            "PC-B8": lambda f: f.regions(_UAES_MATERIAL_COLUMNS),
            "PC-C1": _material_ids,
            "PC-C2": _material_placeholders,
            "PC-C3": _material_weights,
            "PC-C4": _material_weight_scale,
            "PC-C5": lambda f: _material_semantics(f, "size"),
            "PC-C6": lambda f: _material_semantics(f, "standard"),
            "PC-C7": lambda f: _material_semantics(f, "forming"),
            "PC-C8": _material_suppliers,
            "PC-C9": _material_certifications,
            "PC-C10": _material_status,
            "PC-D3": _material_fill_uaes,
            "PC-D4": _material_leaf_weights,
            "PC-D5": _material_suppliers,
            "PC-D1": _material_keys,
            "PC-D2": _material_split,
            "PC-D6": _material_signatures,
            "PC-E1": lambda f: _material_same(f, across=True),
            "PC-E2": _material_header_supplier,
            "PC-E4": _material_filename,
            "PC-E5": _material_same,
            "PC-E6": _material_units,
            "PC-E7": _material_placeholders_consistent,
        },
    )
    facts = Facts(agent.audit_file)
    related = _related_fact(agent, facts, ("图纸", "drawing"), "U04:PC-D7", same=("project", "part"), customer_owned=True)
    paired = _paired_original_fact(agent, "U04:PC-E3", measurement=False)
    return [related if row["id"] == "U04:PC-D7" else paired if row["id"] == "U04:PC-E3" else row for row in result]


def _material_valid(facts):
    rows = _material_rows(facts)
    real = []
    for _, _, _, row in rows:
        part = str(row["part"] or "").strip()
        name = str(row["name"] or "").strip()
        if part and name and not re.search(r"XXX|示例|样例|Example|DEMO", part + " " + name, re.I):
            real.append(row)
    if not real:
        raise DefectFact("B02", "材料清单仅为空白、占位或样例行，没有真实填报对象。")


def _material_keys(facts):
    for _, _, _, row in _material_rows(facts):
        if empty(row["part"]):
            raise DefectFact("B03", "已填写序号的非拆分行零件号为空。")


def _material_units(facts):
    aliases = {"weight_unit": ("重量单位", "单重单位"), "size_unit": ("尺寸单位",), "density_unit": ("密度单位",)}
    rows = _material_rows(facts, aliases)
    for name in aliases:
        units = {compact(row[name]) for _, _, _, row in rows if not empty(row[name])}
        if len(units) > 1:
            raise DefectFact("W03", "同一材料切片的" + name + "单位口径混用。")


def _material_placeholders_consistent(facts):
    rows = _material_rows(facts, _MATERIAL_FIELDS)
    for name in _MATERIAL_FIELDS:
        modes = set()
        for _, _, _, row in rows:
            value = row[name]
            if empty(value):
                modes.add("blank")
            elif compact(value) in _PLACEHOLDERS:
                modes.add("placeholder")
            elif _MARK.fullmatch(str(value).strip()):
                modes.add("mark")
        if len(modes) > 1:
            raise DefectFact("W03", "同列占位符、空白或勾选符混用，须确认使用规律。")


def _material_certifications(facts):
    rows = _material_rows(facts, {"cert": ("通过何种质量体系认证", "质量体系认证", "QS Passed")})
    for _, _, _, row in rows:
        value = compact(row["cert"])
        if value in {"iso/ts16949", "ts16949", "qs9000", "iso16949", "iatf16994", "iatf16946"}:
            raise DefectFact("W03", "体系认证字段为明确废止或拼写错误的标准。")
        if value in {"iso9001", "iso9001:2015", "代理商", "过渡态"}:
            raise UnknownFact("仅ISO9001、代理商或过渡态需正式审核确认豁免，预检不决定认证资格。")
        if value not in {"iatf16949", "iatf16949:2016", "iso14001", "iso14001:2015"}:
            raise UnknownFact("体系认证表述未能按源标准可靠归一化，需人工核对。")


def _related_fact(agent, facts, terms, source_id, *, same, customer_owned=False):
    context = agent.group_context
    if context is None:
        return state_fact(source_id, None, "当前冻结上下文没有关联文件集合，尚不能判断是否已到。", status="unknown")
    if context.unavailable_files:
        return state_fact(source_id, None, "本批已有文件无法读取，关联核对未执行。", status="error")
    candidates = [
        item
        for item in context.by_audit_id.values()
        if item.audit_id != agent.audit_file.audit_id and any(key(term) in key(item.file_metadata.get("original_filename", Path(item.file_url).name)) for term in terms)
    ]
    if not candidates and any(item.audit_id != agent.audit_file.audit_id for item in context.by_audit_id.values()):
        return state_fact(source_id, None, "本批还有其他原件，其对端类别尚未可靠识别；未因文件名不同宣称对端未到。", status="unknown")
    if not candidates:
        return state_fact(
            source_id,
            "S04-C" if customer_owned else "S04-S",
            "本批未识别到" + terms[0] + ("，请向甲方索取或确认受控提供源；不计供应商缺件。" if customer_owned else "，请提交方确认并补充；不据此阻断。"),
            status="not_checked",
        )
    if len(candidates) != 1:
        return state_fact(source_id, None, "关联文件有多个候选，尚不能确认唯一同件原件。", status="unknown")
    try:
        other = Facts(candidates[0])
    except Exception:
        return state_fact(source_id, None, "已提供关联原件无法读取，未取得比对结论。", status="error")

    def compare():
        # Only the identities the clause names (同零件号、同项目、同名称) pair the two originals.
        for name in same:
            label, aliases = _IDENTITY[name]
            current = facts.one(aliases, before=13)
            reference = other.one(aliases, before=13)
            facts.sources.extend(other.sources)
            if compact(current) != compact(reference):
                raise UnknownFact("关联原件" + label + "不一致，不能用于当前对象核对。")
        return "已在本批冻结集合定位" + "、".join(_IDENTITY[name][0] for name in same) + "相同的" + terms[0] + "原件；未用文件名替代零件身份。"

    return facts.observe(source_id, compare)


_CAP_NAMES = ("特性名称", "特性描述", "产品/过程特性", "Characteristic")
_CAP_SUMMARY = {
    "name": _CAP_NAMES,
    "target": ("目标(CPK)", "CPK目标值", "CPK目标", "目标值"),
    "actual": ("结果(CPK)", "CPK结果值", "CPK结果", "Cpk"),
    "decision": ("判断", "结果判定", "判定"),
    "reaction": ("反应计划", "备注", "反应计划或备注"),
}
_CAP_REPORT_FIELDS = {
    "usl": ("USL", "规格上限", "Upper specification limit"),
    "lsl": ("LSL", "规格下限", "Lower specification limit"),
    "size": ("子组容量", "Subgroup size"),
    "groups": ("子组数", "Number of subgroups"),
    "readings": ("读数次数", "Number of readings", "总读数"),
    "cpk": ("Cpk", "Cpk值", "Process Capability (Cpk)"),
    "ppk": ("Ppk", "Ppk值", "Performance index (Ppk)"),
}


def _capacity_reports(facts):
    tables = []
    for table in facts.tables():
        title = key(table["id"])
        if any(term in title for term in ("工艺能力认证", "过程能力认证", "单特性能力", "processcapabilityreport")):
            tables.append(table)
    return tables


def _capacity_plan(facts):
    return facts.records(
        {
            "stage": ("过程阶段",),
            "name": _CAP_NAMES,
            "cpk_target": ("CPK目标值", "目标值(CPK)"),
            "cpk_actual": ("CPK实际值", "结果值(CPK)"),
            "ppk_target": ("PPK目标值", "目标值(PPK)"),
            "ppk_actual": ("PPK实际值", "结果值(PPK)"),
            "spec": ("规格", "规格要求"),
        },
        driver="stage",
        titles=("过程能力研究计划", "过程能力计划"),
    )


def _capacity_summary(facts):
    return facts.records(_CAP_SUMMARY, driver="name", titles=("能力值汇总", "能力汇总", "capabilitysummary"))


def _capacity_family(facts):
    reports = _capacity_reports(facts)
    if reports:
        for table in reports:
            child = _table_facts(facts, table)
            child.field_presence(_CAP_REPORT_FIELDS["cpk"])
        return 3 if any("认证" in str(table["id"]) for table in reports) else 4
    _capacity_plan(facts)
    return 1


def _capacity_shape(facts):
    if not facts.tables():
        raise UnknownFact("原件打开后没有可读取的能力表；图像或PDF解析状态需人工核对。")
    family = _capacity_family(facts)
    return "已从实际结构识别能力载体族" + str(family) + "，没有由客户选择反推载体。"


def _capacity_readable(facts):
    family = _capacity_family(facts)
    if family == 1:
        _capacity_plan(facts)
        return
    for table in _capacity_reports(facts):
        child = _table_facts(facts, table)
        for name in ("cpk", "ppk", "readings"):
            child.one(_CAP_REPORT_FIELDS[name])


def _capacity_main(facts):
    family = _capacity_family(facts)
    if family == 1:
        _capacity_plan(facts)
    elif family == 3:
        _capacity_summary(facts)
    else:
        for table in _capacity_reports(facts):
            child = _table_facts(facts, table)
            for name in ("usl", "cpk", "readings"):
                child.values(_CAP_REPORT_FIELDS[name], required=False)


def _capacity_fields(facts):
    family = _capacity_family(facts)
    if family == 1:
        _capacity_plan(facts)
        return
    if family == 3:
        _capacity_summary(facts)
    for table in _capacity_reports(facts):
        child = _table_facts(facts, table)
        for name in ("usl", "lsl", "size", "cpk", "ppk"):
            child.field_presence(_CAP_REPORT_FIELDS[name])
        try:
            child.field_presence(_CAP_REPORT_FIELDS["readings"])
        except MissingStructure:
            child.field_presence(_CAP_REPORT_FIELDS["groups"])
        child.field_presence(("零件号", "Part number"))
        child.field_presence(_CAP_NAMES)
        for aliases in [("Tool number", "模具号"), ("# Cavities", "型腔数")]:
            try:
                values = child.values(aliases, required=False)
            except MissingStructure:
                continue
            if any(empty(value) for value in values):
                raise DefectFact("W02", "能力报告工具信息区有栏无值：" + aliases[0] + "，未当缺栏B05。")


def _capacity_complete(facts):
    family = _capacity_family(facts)
    if family == 1:
        for _, _, _, row in _capacity_plan(facts):
            if any(empty(value) for value in row.values()):
                raise DefectFact("B04", "能力计划已有过程阶段的行存在必填空值；实际值不因计划阶段而豁免。")
    elif family == 3:
        for _, _, _, row in _capacity_summary(facts):
            if any(empty(row[name]) for name in ("name", "target", "actual", "decision")):
                raise DefectFact("B04", "能力汇总逐特性目标、结果或判断有空值。")
    if family != 1:
        for table in _capacity_reports(facts):
            child = _table_facts(facts, table)
            values = []
            for name in ("size", "cpk", "ppk", "readings"):
                values.extend(child.values(_CAP_REPORT_FIELDS[name], required=False))
            if any(not empty(value) for value in values) and any(empty(value) for value in values):
                raise DefectFact("B04", "能力报告区块已开始填写，但样本信息或能力指数中途留空。")


def _capacity_placeholders(facts):
    for table, row, column, item in facts.cells():
        if item.get("state") != "known" or not header(item.get("value"), ("Cp", "Cpk", "Pp", "Ppk", "均值", "极差", "标准差", "UCL", "LCL")):
            continue
        span = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
        right = span[3] + 1 if span else column + 1
        if right <= table["cols"]:
            target = effective(table, row, right)
            if str(target.get("value") or "").upper() in _ERRORS:
                facts.touch(table, row, right)
                raise DefectFact("B05", "能力统计/结论区含明确电子表格错误。")
    family = _capacity_family(facts)
    values = []
    if family == 1:
        values = [value for _, _, _, row in _capacity_plan(facts) for name, value in row.items() if name.endswith(("target", "actual"))]
    else:
        for table in _capacity_reports(facts):
            child = _table_facts(facts, table)
            for name in ("cpk", "ppk", "readings", "size"):
                values.extend(child.values(_CAP_REPORT_FIELDS[name], required=False))
    for value in values:
        token = compact(value)
        if token in _PLACEHOLDERS or str(value).upper() in _ERRORS or re.fullmatch(r"#{3,}", str(value)):
            raise DefectFact("B05", "能力判定取值区存在占位符、错误值或井号截断；文本NA与#N/A未混淆。")
    if not values:
        raise UnknownFact("未定位到能力取值单元格。")


def _capacity_types(facts):
    family = _capacity_family(facts)
    if family == 1:
        for _, _, _, row in _capacity_plan(facts):
            for name in ("cpk_target", "cpk_actual", "ppk_target", "ppk_actual"):
                if compact(row[name]) == "na":
                    continue
                numeric(row[name], strict=True)
        return
    for table in _capacity_reports(facts):
        child = _table_facts(facts, table)
        for name in ("usl", "lsl", "size", "readings", "cpk", "ppk"):
            value = child.one(_CAP_REPORT_FIELDS[name])
            if compact(value) == "na" and name in {"cpk", "ppk"}:
                continue
            numeric(value, strict=True)


def _capacity_specs(facts):
    reports = _capacity_reports(facts)
    if not reports:
        for _, _, _, row in _capacity_plan(facts):
            limits = parse_quality_standard_range(row["spec"])
            if limits is None:
                raise UnknownFact("计划规格文本暂不能可靠结构化为边界，需确认单边/双边模型。")
            if limits[0] >= limits[1]:
                raise DefectFact("B05", "计划规格下限不小于上限。")
        return
    for table in reports:
        child = _table_facts(facts, table)
        upper = numeric(child.one(_CAP_REPORT_FIELDS["usl"]))
        lower = numeric(child.one(_CAP_REPORT_FIELDS["lsl"]))
        if lower >= upper:
            raise DefectFact("B05", "能力报告规格下限不小于上限。")
        try:
            nominal = child.one(("名义值", "Nominal", "目标值"))
        except MissingStructure:
            nominal = None
        if nominal is not None and not lower <= numeric(nominal) <= upper:
            raise DefectFact("B05", "名义值不在卷面规格上下限之间。")
        try:
            specification = child.one(("规格", "规格要求", "公差要求"))
        except MissingStructure:
            specification = None
        if specification is not None:
            parsed = parse_quality_standard_range(specification)
            if parsed is None:
                raise UnknownFact("卷面规格文本与上下限的算术关系未能可靠解析。")
            if abs(Decimal(str(parsed[0])) - lower) > Decimal(".000001") or abs(Decimal(str(parsed[1])) - upper) > Decimal(".000001"):
                raise DefectFact("B05", "卷面规格文本与填写的上下限不一致。")
        name = str(child.one(_CAP_NAMES))
        if any(term in name for term in ("平面度", "同轴度", "位置度", "垂直度", "平行度", "跳动", "圆度", "圆柱度", "轮廓度")):
            try:
                lower_index = child.one(("Cpl", "Ppl"), required=False)
            except MissingStructure:
                lower_index = None
            if lower == 0 and not empty(lower_index):
                raise DefectFact("B05", "单边几何特性填写LSL=0且输出下限能力指数，模型不自洽。")


def _capacity_consistency(facts):
    reports = _capacity_reports(facts)
    if not reports:
        raise UnknownFact("当前没有可互证卷面指标的能力报告页。")
    for table in reports:
        child = _table_facts(facts, table)
        values = {}
        for name in ("Cp", "Cpk", "Pp", "Ppk", "USL", "LSL", "最大值", "最小值", "均值", "标准差"):
            try:
                value = child.one((name,), required=False)
            except MissingStructure:
                continue
            if not empty(value) and compact(value) != "na":
                values[name] = numeric(value)
        if {"Cp", "Cpk"} <= values.keys() and values["Cpk"] > values["Cp"] or {"Pp", "Ppk"} <= values.keys() and values["Ppk"] > values["Pp"]:
            raise DefectFact("B06", "卷面Cpk大于Cp或Ppk大于Pp，指标内部矛盾。")
        decision = compact(child.one(("判定", "判断", "结论")))
        accepted = decision in {"合格", "达标", "能力足够", "☑合格", "☑达标"}
        if accepted and ({"最大值", "USL"} <= values.keys() and values["最大值"] > values["USL"] or {"最小值", "LSL"} <= values.keys() and values["最小值"] < values["LSL"]):
            raise DefectFact("B06", "卷面极值已超出卷面规格界限，却填写合格/达标。")
        if {"Cp", "Cpk", "均值", "USL", "LSL"} <= values.keys() and values["均值"] == (values["USL"] + values["LSL"]) / 2 and values["Cp"] != values["Cpk"]:
            raise DefectFact("B06", "卷面均值位于规格中心但Cp与Cpk不同。")
        try:
            printed = child.one(("判定标准", "卷面判定标准"))
        except MissingStructure:
            printed = None
        if printed is not None:
            match = re.fullmatch(r"\s*(Cpk|Ppk)?\s*(>=|≥|>)\s*(\d+(?:\.\d+)?)\s*(?:合格|达标)?\s*", str(printed), re.I)
            if not match:
                raise UnknownFact("卷面自带判定标准未能可靠解析，没有引用外部能力阈值。")
            name = (match.group(1) or "Cpk").capitalize().replace("pk", "pk")
            name = "Cpk" if name.casefold() == "cpk" else "Ppk"
            if accepted and (name not in values or values[name] < Decimal(match.group(3)) or match.group(2) == ">" and values[name] == Decimal(match.group(3))):
                raise DefectFact("B06", "卷面已印标准与卷面能力结果/结论冲突。")
    return "已检查可互证卷面内部关系；Cpk/Ppk是否达到客户准入阈值未在预检判断。"


def _capacity_sample_metadata(facts):
    _capacity_constants(facts)
    for table in _capacity_reports(facts):
        child = _table_facts(facts, table)
        size = numeric(child.one(_CAP_REPORT_FIELDS["size"]))
        readings = numeric(child.one(_CAP_REPORT_FIELDS["readings"]))
        if size <= 0 or size != size.to_integral_value() or readings < 0 or readings != readings.to_integral_value():
            raise DefectFact("B05", "能力报告子组容量或总读数不是合法整数。")
        minimum = 30 if size == 1 else 100
        if readings < minimum:
            raise DefectFact("W06", "有效读数低于通用最低支撑量，仅记警告；UAES125门槛移交正式审核。")
        try:
            groups = numeric(child.one(_CAP_REPORT_FIELDS["groups"]))
        except MissingStructure:
            groups = None
        if groups is not None and size * groups != readings:
            raise DefectFact("W06", "卷面子组容量×子组数与总读数不一致。")
    if not _capacity_reports(facts):
        raise UnknownFact("计划表须按其声明子组容量与组数核对，当前未定位相应字段。")


def _capacity_dates(facts):
    dates = facts.records({"time": ("抽样时间", "子组时间", "采集时间"), "group": ("子组号", "子组序号", "组号")}, driver="group")
    parsed = []
    for _, _, _, row in dates:
        value = row["time"]
        try:
            stamp = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            raise UnknownFact("采样时间无法可靠归一化，未猜测其顺序。") from None
        parsed.append((stamp, str(value)))
    if any(right[0] < left[0] for left, right in zip(parsed, parsed[1:])):
        raise DefectFact("B06", "子组采样时间逆序。")
    if len(parsed) > 1 and all("T" in raw or ":" in raw for _, raw in parsed) and len({value for value, _ in parsed}) == 1:
        raise DefectFact("W06", "全部子组带时间部分的时间戳完全相同，需确认复制痕迹。")
    signed = normalize_excel_date(facts.one(("报告签署日期", "签署日期", "批准日期")))
    if signed is None:
        raise UnknownFact("报告签署日期未能可靠识别。")
    if parsed[-1][0].date() > signed:
        raise DefectFact("B06", "数据采集完成日期晚于报告签署日期。")
    try:
        production = normalize_excel_date(facts.one(("生产起始日期", "试产起始日期")))
    except MissingStructure:
        raise UnknownFact("原件内采样顺序和签署日期已核对；项目生产起始日期未提供，需人工补核，未推定通过。") from None
    if production is None:
        raise UnknownFact("生产起始日期不可解析，未猜测日期。")
    if parsed[0][0].date() < production:
        raise DefectFact("B06", "采样日期早于文件内明确的生产/试产起始日期。")
    return "已核对原件内日期关系；提交日期未提供，其子项S01。"


def _capacity_internal(facts):
    reports = _capacity_reports(facts)
    seen: dict = {}
    for table in reports:
        child = _table_facts(facts, table)
        identity = tuple(compact(child.one(aliases)) for aliases in [("零件号", "Part number"), ("零件名称", "Part name"), ("项目号", "项目"), ("顾客名", "客户名称"), ("版本号", "版本")])
        if seen and identity not in seen:
            raise DefectFact("B06", "同文件能力报告的零件、项目、客户或版本身份不一致。")
        seen[identity] = True
    summary = _capacity_summary(facts)
    detail = {}
    for table in reports:
        child = _table_facts(facts, table)
        name = compact(child.one(_CAP_NAMES))
        if name in detail:
            raise UnknownFact("同名特性有多份报告，需确认页序/唯一标识，未按页序任意配对。")
        detail[name] = numeric(child.one(_CAP_REPORT_FIELDS["cpk"]))
    actual = {compact(row["name"]): numeric(row["actual"]) for _, _, _, row in summary}
    if set(detail) != set(actual):
        raise DefectFact("B06", "同文件能力明细与汇总清单的特性集合不等价。")
    if any(detail[name] != actual[name] for name in detail):
        raise DefectFact("B06", "同文件能力明细与汇总清单的Cpk结果不一致。")


def precheck_capacity(agent):
    operations = {
        "PRE-01": _capacity_shape,
        "PRE-02": _capacity_readable,
        "PRE-03": _capacity_submission,
        "PRE-04": _capacity_directory,
        "PRE-08": _capacity_chart_set,
        "PRE-14": _capacity_trace,
        "PRE-05": _capacity_main,
        "PRE-06": _capacity_fields,
        "PRE-07": _capacity_complete,
        "PRE-09": _capacity_placeholders,
        "PRE-10": _capacity_types,
        "PRE-11": _capacity_specs,
        "PRE-12": _capacity_consistency,
        "PRE-13": _capacity_sample_metadata,
        "PRE-15": _capacity_dates,
        "PRE-16": _capacity_internal,
    }
    result = execute(agent, "U06", operations)
    facts = Facts(agent.audit_file)
    chart_declared = any(any(term in str(table["id"]).casefold() for term in ("控制图", "i-mr", "xbar-r", "xbar-s")) for table in facts.tables())
    chart_objects = any(table.get("visual_evidence") for table in facts.tables())
    if not chart_declared and not chart_objects:
        result = [state_fact("U06:PRE-08", "S01", "本能力载体不含控制图；UAES没有强制三图要求，未套用理想要求。", status="not_checked") if item["id"] == "U06:PRE-08" else item for item in result]
    result.append(_related_fact(agent, facts, ("采购件质量计划", "PGQP1"), "U06:PRE-04:PGQP1", same=("part",)))
    result.append(state_fact("U06:customer_thresholds", "S03", "UAES读数125及Cpk>1.33、Ppk>1.67属于正式审核；预检不将不达标转成阻断。", status="handoff"))
    return result


_IR = {"id": ("序号", "No.", "编号", "检查项目"), "standard": ("要求", "要求值", "判定标准", "质量标准", "技术要求", "Requirements"), "result": ("结果", "判定", "评价", "Evaluation", "判定结果")}
_IR_ACTUAL = ("供应商实测值", "供应商实测结果", "实测值", "检查数据", "测量值", "Actual-Value Supplier/供应商实测值/Actual-Value Customer 客户实测值")


def _inspection_records(facts):
    rows = []
    for table, start, columns in facts.regions(_IR):
        actual = []
        for column in range(1, table["cols"] + 1):
            item = effective(table, start, column)
            if item.get("state") == "known" and header(item.get("value"), _IR_ACTUAL):
                actual.append(column)
        if not actual:
            raise MissingStructure("检验报告实测样本列尚未定位，须核对受控模板（S05）。")
        result_column = columns["result"]
        split = key(effective(table, start + 1, result_column).get("value")) == "ok" and key(effective(table, start + 1, result_column + 1).get("value")) in {"notok", "nok", "ng"}
        bottom = max([start] + [span[2] for span in table.get("merges", []) if span[0] <= start <= span[2] and span[1] <= columns["id"] <= span[3]])
        for row in range(bottom + 1, table["rows"] + 1):
            identity = effective(table, row, columns["id"])
            standard = effective(table, row, columns["standard"])
            if str(identity.get("value") or "").startswith(_FOOTERS) or str(standard.get("value") or "").startswith(_FOOTERS):
                break
            relevant = [effective(table, row, column) for column in [*columns.values(), *actual]]
            if all(empty(item.get("value")) and item.get("state") == "known" for item in relevant):
                continue
            if header(identity.get("value"), _IR["id"]) or header(standard.get("value"), _IR["standard"]):
                continue
            values = Row(facts, table, row, columns)
            values["actual"] = lambda table=table, row=row, actual=actual: [facts.read(table, row, column, vertical_only=True) for column in actual]
            values["actual_columns"] = actual
            values["split_result"] = split
            rows.append((table, row, columns, values))
    if not rows:
        raise DefectFact("B04", "检验报告业务区没有有效数据行。")
    return rows


def _inspection_result(facts, table, row_number, columns, row):
    value = row["result"]
    if row["split_result"]:
        rejected = facts.read(table, row_number, columns["result"] + 1)
        selected = bool(_MARK.fullmatch(str(value or "").strip()))
        negative = bool(_MARK.fullmatch(str(rejected or "").strip()))
        if selected and not negative:
            return "OK"
        if negative and not selected:
            return "NOK"
        if selected and negative:
            raise DefectFact("W02", "检验报告OK与NOT OK同时选择。")
        raise UnknownFact("检验报告结果分栏尚无可识别的唯一实际选择。")
    return value


def _inspection_standards(facts):
    for _, _, _, row in _inspection_records(facts):
        if empty(row["standard"]):
            raise DefectFact("W02", "待检项的要求/判定标准为空。")


def _inspection_values(facts):
    for table, number, columns, row in _inspection_records(facts):
        if any(empty(value) or compact(value) in _PLACEHOLDERS | {"na", "n/a"} for value in row["actual"]):
            raise DefectFact("W02", "检验报告实测样本存在空值或占位值。")
        if empty(_inspection_result(facts, table, number, columns, row)):
            raise DefectFact("W02", "检验报告同行结果为空。")


def _inspection_result_words(facts):
    for table, number, columns, row in _inspection_records(facts):
        values = row["actual"]
        standard = str(row["standard"] or "")
        numeric_mode = parse_quality_standard_range(standard) is not None
        result = compact(_inspection_result(facts, table, number, columns, row))
        if result in {"nok", "不合格", "pass", "正确"}:
            raise DefectFact("W03", "供应商判定结果存在分则明确禁止的取值；未把PASS当OK。")
        if result != "ok":
            raise UnknownFact("供应商结果值暂不能归一化为标准OK。")
        if numeric_mode:
            for value in values:
                numeric(value, code="W03")
        elif any(re.search(r"\d", standard) for _ in [0]):
            raise UnknownFact("要求包含数值但模态未能可靠解析，不能当定性通过。")


def _inspection_samples(facts):
    try:
        required = int(numeric(facts.one(("应测样本量", "应测样本数", "样本量要求"))))
    except MissingStructure:
        required = 5
    if required <= 0:
        raise UnknownFact("声明应测样本量不是正整数。")
    for _, _, _, row in _inspection_records(facts):
        count = sum(not empty(value) and compact(value) not in _PLACEHOLDERS | {"na", "n/a"} for value in row["actual"])
        if count < required:
            raise DefectFact("W01", f"实填样本量{count}小于应测样本量{required}。")


def _inspection_signature_text(facts):
    names = [facts.one(aliases) for aliases in [("检验人", "检验员"), ("审核人", "审核员"), ("批准人", "批准")]]
    if any(empty(value) or compact(value) in _PLACEHOLDERS for value in names):
        raise DefectFact("W02", "三级签批姓名有空白或占位值。")
    if compact(names[0]) == compact(names[1]):
        raise DefectFact("W06", "检验人与审核人相同，职责未分离。")


def _inspection_signature_dates(facts):
    values = [facts.one(aliases) for aliases in [("检验日期",), ("审核日期",), ("批准日期",)]]
    dates = [normalize_excel_date(value) for value in values]
    if any(value is None for value in dates):
        raise DefectFact("W03", "检验/审核/批准日期存在不可解析值。")
    first, middle, last = dates
    if first is None or middle is None or last is None:
        raise UnknownFact("签批日期仍有未知值。")
    if first > middle or middle > last or (last - first).days > 30:
        raise DefectFact("W06", "签批日期未按检验≤审核≤批准，或首尾跨度大于30天。")


def _inspection_sequence(facts):
    seen = defaultdict(list)
    parent = {}
    seen_anchors = set()
    for table, physical_row, columns, row in _inspection_records(facts):
        identity = str(row["id"] or "").strip()
        standard = row["standard"]
        if empty(standard):
            if table["id"] not in parent:
                raise DefectFact("W03", "要求为空的子行没有上游父行，存在孤立行。")
            continue
        if not re.fullmatch(r"\d+(?:\.0)?", identity):
            raise UnknownFact("气泡序号不是可可靠解释的整数，需核对父子行归属。")
        value = int(numeric(identity))
        parent[table["id"]] = value
        anchor = (table["id"], effective(table, physical_row, columns["id"])["anchor"])
        if anchor not in seen_anchors:
            seen[table["id"]].append(value)
            seen_anchors.add(anchor)
    for values in seen.values():
        if values != list(range(1, len(values) + 1)):
            raise DefectFact("W03", "检验报告序号不连续或重复。")


def _inspection_modalities(facts):
    for _, _, _, row in _inspection_records(facts):
        values = [value for value in row["actual"] if not empty(value)]
        if not values:
            raise DefectFact("W02", "实测行没有可识别的数据模态。")
        numeric_count = sum(bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(value).strip())) for value in values)
        qualitative_count = sum(compact(value) in {"ok", "ng", "nok", "合格", "不合格", "pass", "fail"} for value in values)
        external_count = sum(any(term in str(value) for term in ("见报告", "附件", "参见")) for value in values)
        if sum(count > 0 for count in (numeric_count, qualitative_count, external_count)) != 1:
            raise DefectFact("W03", "同行实测混有多种数据模态或不可识别文本。")
        if parse_quality_standard_range(row["standard"]) is not None and qualitative_count:
            raise DefectFact("W03", "定量特征被填写为定性实测结果。")


def _inspection_raw(facts):
    for _, _, _, row in _inspection_records(facts):
        for value in row["actual"]:
            token = str(value or "").strip()
            if re.search(r"\d\.\.\d|#{3,}|[\u200b-\u200f\ufeff]", token):
                raise DefectFact("W03", "原始实测单元格含非法字符或截断标记。")
            if "\n\n" in str(value):
                raise DefectFact("W03", "原始实测单元格混入多余空行。")


def _inspection_tolerances(facts):
    for _, _, _, row in _inspection_records(facts):
        value = row["standard"]
        if empty(value):
            raise DefectFact("W02", "原始要求文本为空。")
        if re.search(r"\d", str(value)):
            limits = parse_quality_standard_range(value)
            if limits is None:
                raise UnknownFact("定量标准暂不能可靠解析为闭区间，未将其当作定性。")
            if not all(Decimal(str(number)).is_finite() for number in limits):
                raise UnknownFact("单边规格尚需明确另一边界或受控标准，未推定闭区间。")
            if limits[0] > limits[1]:
                raise DefectFact("W03", "标准上下界代数倒挂。")


def precheck_inspection(agent):
    operations = {
        "IR-01a": _inspection_scope,
        "IR-01b": lambda f: _inspection_scope(f, project=True),
        "IR-04": _inspection_standards,
        "IR-08": _inspection_values,
        "IR-10": _inspection_result_words,
        "IR-17": _inspection_samples,
        "IR-18": lambda f: _inspection_records(f),
        "IR-22": _inspection_signature_dates,
        "IR-23": _inspection_signature_text,
        "IR-26": _inspection_sequence,
        "IR-28": _inspection_standards,
        "IR-31": _inspection_tolerances,
        "IR-33": _inspection_modalities,
        "IR-34": _inspection_raw,
        "IR-35": _inspection_normalize_result,
        "IR-24": _inspection_gauges,
        "IR-32": _inspection_composite,
        "IR-19": _inspection_supplier,
        "IR-20": _inspection_part_contract,
        "IR-21": _inspection_attachment,
        "IR-25": _inspection_tolerance_standard,
        "IR-27": _inspection_feature_class,
        "IR-29": _inspection_special_marks,
        "IR-30": _inspection_occurrences,
    }
    states: dict[str, tuple[str | None, str, str]] = {identifier: ("S03", "实测与公差、重复值或贴近上限的判断属于正式审核，预检仅移交。", "handoff") for identifier in ("IR-09", "IR-11", "IR-16")}
    states.update({"IR-13": (None, "四报告供应商负责人实际签字图像尚未核实；签字栏标签不代表已签署。", "unknown"), "IR-14": (None, "原件印章图像尚未核实；没有把盖章栏文字当真实印章。", "unknown")})
    states.update({identifier: ("S02", "01-1 §7.7 UAES客户差异层没有此理想专属结构/联动要求，未跨客户套用。", "not_checked") for identifier in ("IR-02", "IR-05", "IR-06", "IR-07", "IR-12")})
    result = execute(agent, "U01", operations, states=states)
    original = Facts(agent.audit_file)
    result.append(original.observe("U01:four_reports", lambda: _inspection_quartet(original)))
    related = _related_fact(agent, Facts(agent.audit_file), ("气泡图", "bubble"), "U01:IR-15", same=("part", "name"), customer_owned=False)
    return [related if row["id"] == "U01:IR-15" else row for row in result]


def _inspection_supplier(facts):
    supplier = facts.one(("供应商名称", "供应商", "Supplier name"))
    code = facts.one(("供应商代码", "供应商编号", "Supplier number"))
    part = facts.one(("客户零件号", "零件号", "物料编号", "Part number"))
    if empty(supplier) or empty(code):
        raise DefectFact("W02", "供应商名称或代码为空。")
    if compact(code) == compact(part):
        raise DefectFact("W03", "供应商代码与客户零件号相同，存在串位。")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(code).strip()):
        raise DefectFact("W03", "供应商代码含不能归一化的已知字符。")


def _inspection_part_contract(facts):
    customer = facts.one(("客户零件号", "UAES零件号"))
    supplier = facts.one(("供应商零件号", "供方零件号"))
    facts.one(("图纸版本号", "图纸版本", "Drawing index"))
    facts.one(("零件名称", "物料名称", "Part name"))
    weight = numeric(facts.one(("重量", "单件重量", "零件重量")))
    if weight <= 0:
        raise DefectFact("W03", "零件重量不是正数。")
    if compact(customer) == compact(supplier):
        raise UnknownFact("客户件号与供应商件号相同，须确认是否为合法同编码，未推定互串。")


def _inspection_tolerance_standard(facts):
    value = facts.one(("未注公差标准", "未注公差", "一般公差标准"))
    if not str(value).strip():
        raise DefectFact("W02", "未注公差标准有栏无值。")
    return "已读取原件未注公差声明；未虚构挂载ISO兜底。"


def _inspection_feature_class(facts):
    rows = facts.records({"id": _IR["id"], "category": ("特征分类", "特征类别", "工程特征分类"), "unit": ("单位", "测量单位")}, driver="id")
    allowed = {
        "线性尺寸": {"mm", "cm", "m"},
        "直径": {"mm", "cm"},
        "角度": {"°", "deg", "度"},
        "形位公差": {"mm", "μm", "um"},
        "表面粗糙度": {"μm", "um"},
        "倒角": {"mm", "°", "deg", "度"},
        "材料性能": None,
        "外观": {"", "/", "na"},
    }
    for _, _, _, row in rows:
        category = str(row["category"]).strip()
        if category not in allowed:
            raise UnknownFact("工程特征类别尚不能映射到八类之一，未用自由文本猜测。")
        units = allowed[category]
        if units is not None and compact(row["unit"]) not in units:
            raise DefectFact("W03", "已声明工程特征类别与单位不匹配。")


def _inspection_occurrences(facts):
    groups: dict = {}
    previous: dict = {}
    for table, row_number, columns, row in _inspection_records(facts):
        value = str(row["standard"] or "").strip()
        observed = effective(table, row_number, columns["standard"])
        if not value:
            if table["id"] not in previous:
                raise UnknownFact("空要求行没有可确认的上游父特征。")
            identity = previous[table["id"]]
        else:
            identity = (table["id"], observed["anchor"])
            previous[table["id"]] = identity
            if identity not in groups:
                match = re.match(r"(\d+)\s*[-×xX]\s*(?:[ΦφØM]|\d)", value)
                groups[identity] = [int(match.group(1)) if match else 1, 0]
        groups[identity][1] += 1
    for expected, actual in groups.values():
        if expected != actual:
            raise DefectFact("W02", f"特征处数{expected}与展开物理子行数{actual}不一致；单格样本斜杠数未计为处数。")


def _inspection_scope(facts, *, project=False):
    scope = compact(facts.one(("提交范围", "本次提交范围")))
    if project:
        if scope not in {"full", "全部", "全量"}:
            return "本次非FULL，未判项目全量齐套；范围原件已定位。"
        wanted = facts.one(("项目应有检验报告清单", "项目应有报告清单"))
    else:
        wanted = facts.one(("本次报告类别", "提交报告类别", "提交报告清单"))
    terms = [term for term in ("尺寸", "材料", "功能", "可靠性") if term in str(wanted)]
    if not terms:
        raise UnknownFact("范围声明未能可靠映射报告类别。")
    for term in terms:
        if not any(
            term in str(table["id"]) or any(term + "报告" in str(item.get("value") or "") for address, item in table["cells"].items() if coordinate_to_tuple(address)[0] <= 5)
            for table in facts.tables()
        ):
            raise DefectFact("W01", "声明范围内未定位到" + term + "报告；未推定范围外文件缺失。")


def _inspection_attachment(facts):
    value = str(facts.one(("递交原因", "提交原因")))
    selected = re.findall(r"[▣■☒☑√þ█R]", value)
    if not selected:
        raise DefectFact("W02", "递交原因未见实际选择。")
    attachments = str(facts.one(("附件索引", "提交附件", "附件清单")))
    if "____" in value + attachments:
        raise DefectFact("W02", "递交契约/附件索引仍有未替换填空符。")
    selected_items = re.findall(r"[▣■☒☑√þ█R]\s*([^□☐▣■☒☑√þ█R,，;；\n]+)", attachments)
    for text in selected_items:
        if not any(key(text) in key(table["id"]) for table in facts.tables()):
            raise DefectFact("W01", "附件索引已选项目在本文件中未定位到对应子表：" + text)


def _inspection_special_marks(facts):
    rows = facts.records({"id": _IR["id"], "mark": ("特殊特性", "特性标识", "特殊特性标识")}, driver="id")
    valid = {"★", "◇", "cc", "sc", "kpc", "[3,5]", "[5]", "[f|3]", "[s]"}
    has_marks = False
    for _, _, _, row in rows:
        value = compact(row["mark"])
        if not value:
            continue
        has_marks = True
        if value not in valid:
            raise DefectFact("W03", "行级特殊特性标识不在缺省CP-G符号并集中。")
    if has_marks:
        attachments = str(facts.one(("附件索引", "提交附件", "附件清单")))
        if not re.search(r"[▣■☒☑√þ█R]\s*特殊特性", attachments):
            raise UnknownFact("已有特殊特性标识，但封面附件实际选择未能可靠对应。")


_UAES_MATERIAL_COLUMNS = {
    "classification": ("材料分类", "Material Classification"),
    "standard": ("材料标准", "Material Specification"),
    "grade": ("材料名称和牌号", "材料名称和材料牌号", "Material Name and Grade Code"),
}


def _material_generation(facts):
    facts.values(("UAES项目编号", "UAES Project No"), required=False, before=5)
    facts.values(("IMDS表单ID号", "IMDS data sheet ID"), required=False, before=5)
    facts.regions(_UAES_MATERIAL_COLUMNS)
    return "按UAES项目号、IMDS及UAES三材料列识别本分支，没有按理想列数推代际。"


def _material_fill_uaes(facts):
    rows = _material_rows(facts, _UAES_MATERIAL_COLUMNS)
    for name in _UAES_MATERIAL_COLUMNS:
        if sum(empty(row[name]) for _, _, _, row in rows) / len(rows) >= 0.9:
            raise DefectFact("W01", "UAES适用材料列" + name + "至少90%为空。")


def _material_weight_scale(facts):
    rows = _material_rows(facts, {"level": ("装配级别", "部件装配级别", "层级"), "category": ("零件类别", "部件类别"), "weight": _WEIGHT})
    groups = defaultdict(list)
    for table, _, _, row in rows:
        weight = numeric(row["weight"])
        if weight <= 0:
            raise UnknownFact("非正单重不能用于量级比值，需核对原件。")
        groups[table["id"], compact(row["level"]), compact(row["category"])].append(weight)
    if any(max(values) / min(values) >= 1000 for values in groups.values()):
        raise DefectFact("W06", "同切片同层级同类零件单重跨度达到1000倍；未跨层比较总成与叶子件。")


def _material_assembly_children(facts):
    rows = _material_rows(facts, {"level": ("装配级别", "部件装配级别", "层级"), "self": ("自制", "Homemade"), "category": ("零件类别", "部件类别")})
    for index, (table, _, _, row) in enumerate(rows):
        marked = _MARK.fullmatch(str(row["self"] or "").strip()) or compact(row["self"]) in {"1", "yes", "y", "true", "是"}
        assembly = any(term in str(row["name"]) for term in ("总成", "组件"))
        exempt = any(term in str(row["category"]) for term in ("标准电子黑盒", "标准紧固件"))
        if marked and assembly and not exempt:
            level = numeric(row["level"])
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            if next_row is None or next_row[0]["id"] != table["id"] or numeric(next_row[3]["level"]) <= level:
                raise DefectFact("B04", "已选自制总成/组件没有下级，且不属于明确豁免类。")


def _material_header_supplier(facts):
    supplier = facts.one(("供应商名称", "Supplier name"), before=5)
    first = _material_rows(facts, {"supplier": _MATERIAL_FIELDS["supplier"]})[0][3]
    if compact(supplier) != compact(first["supplier"]):
        raise UnknownFact("表头供应商与根节点供应商不同，需确认是否有合法说明，未直接判冲突。")


def _material_filename(facts):
    filename = str(facts.audit.file_metadata.get("original_filename", Path(facts.audit.file_url).name))
    part = facts.one(("零件号", "零件编号", "Part number", "Part No"), before=5)
    match = re.search(r"(?:零件号|PN)[_ -]?([A-Za-z0-9_.-]+)", filename, re.I)
    if match and compact(match.group(1)) != compact(part):
        raise DefectFact("W04", "文件名明确标注零件号与表头零件号不一致。")
    if not match:
        raise UnknownFact("文件名不含可可靠解释的零件/总成/版本声明，未从普通文件名猜测。")


def _capacity_directory(facts):
    family = _capacity_family(facts)
    if family == 1:
        return "当前为纯能力计划表，不承载目录结构；原件载体已定位。"
    if any("目录" in str(table["id"]) for table in facts.tables()) or facts.text("目录"):
        return
    reports = _capacity_reports(facts)
    if len(reports) == 1:
        child = _table_facts(facts, reports[0])
        for aliases in [("文件名", "报告名称"), ("零件号", "Part number"), ("版本号", "版本"), ("日期", "报告日期"), ("页码", "区块索引")]:
            child.one(aliases)
        return "单区块报告首页信息表承载完整文件、零件、版本、日期和页索引，作为目录等效形态。"
    raise UnknownFact("报告未定位到目录或可确认的等效首页信息表，须核对完整原件结构（S05）。")


def _capacity_trace(facts):
    family = _capacity_family(facts)
    if family == 1:
        rows = _capacity_plan(facts)
        targets = [(table, row, columns[name]) for table, row, columns, _ in rows for name in ("cpk_actual", "ppk_actual")]
    else:
        targets = []
        for table in _capacity_reports(facts):
            for address, item in table["cells"].items():
                row, column = coordinate_to_tuple(address)
                if header(item.get("value"), ("Cp", "Cpk", "Pp", "Ppk", "均值", "极差", "标准差", "UCL", "LCL")):
                    right = column + 1
                    if right <= table["cols"]:
                        targets.append((table, row, right))
    if not targets:
        raise UnknownFact("未定位到参与判定的统计取值区。")
    formulas = False
    for table, row, column in targets:
        item = effective(table, row, column)
        formula = str(item.get("formula") or "")
        if formula:
            formulas = True
            facts.touch(table, row, column)
            if re.search(r"\b(?:RAND|RANDBETWEEN|RANDARRAY)\s*\(", formula, re.I):
                raise DefectFact("B06", "能力统计/结果区使用随机数生成公式。")
            if "[" in formula and "]" in formula and item.get("state") == "unknown_formula_cache":
                raise UnknownFact("取值公式指向外部工作簿且没有缓存；当前尚未核实随批同源替代，未直接判供应商缺陷。")
        facts.read(table, row, column)
    documented = any(facts.text(term) for term in ("计算过程说明", "系统导出凭证", "原始测量记录"))
    if not formulas and not documented:
        raise UnknownFact("统计值为常量，未确认全部追溯依据是否已随批提供；需核对原始测量记录/计算说明/系统凭证。")
    if not formulas:
        raise DefectFact("W05", "当前文件为数值粘贴版，已有追溯说明但计算过程未在文件中留存。")


def _capacity_submission(facts):
    _capacity_shape(facts)
    _capacity_complete(facts)
    for table, row, column, item in facts.cells():
        text = str(item.get("value") or "")
        if re.search(r"\bDEMO\b|\bDRAFT\b|草稿|待填|示例", text, re.I):
            facts.read(table, row, column)
            raise DefectFact("B04", "能力填报件仍明确标记为草稿、待填或示例。")
    context = facts.context
    if context is None:
        raise UnknownFact("载体和填报区域已读取；未提供完整冻结文件集合，无法核对自述清单或重复副本。")
    if context.unavailable_files:
        raise OSError("本批已有文件无法读取，不能完成提交对账。")
    hashes = set()
    for item in context.by_audit_id.values():
        digest = Facts(item).data["source"]["sha256"]
        if digest in hashes:
            raise DefectFact("W04", "本批存在相同内容的重复副本，请确认唯一审核对象；两副本仍须参与检查。")
        hashes.add(digest)
    try:
        declared = facts.records({"file": ("文件名", "提交文件名")}, titles=("文件清单", "提交清单"))
    except MissingStructure:
        return "载体和填报区域已读取；本文件没有自述清单，该子项S01，无须补清单。"
    actual = {str(item.file_metadata.get("original_filename", Path(item.file_url).name)) for item in context.by_audit_id.values()}
    names = {str(row["file"]).strip() for _, _, _, row in declared}
    if names != actual:
        raise DefectFact("W04", "自述清单与本批实际文件双向不一致；固定警告，未把自述沿用模板当应交承诺。")


def _capacity_chart_set(facts):
    charts = []
    for table in facts.tables():
        title = str(table["id"])
        if any(term in title.casefold() for term in ("控制图", "i-mr", "xbar-r", "xbar-s")):
            charts.append(table)
    if not charts:
        raise MissingStructure("本能力载体未声明控制图，UAES没有强制三图要求；不套用理想要求。")
    for table in charts:
        child = _table_facts(facts, table)
        size = numeric(child.one(_CAP_REPORT_FIELDS["size"]))
        expected = ("i-mr", "单值", "移动极差") if size == 1 else ("xbar-r", "均值极差") if 2 <= size <= 8 else ("xbar-s", "均值标准差")
        if not any(term in key(table["id"]) or child.text(term) for term in expected):
            raise DefectFact("B05", "控制图声明类型与子组容量所需图型不匹配。")
        child.one(_CAP_REPORT_FIELDS["cpk"])
        child.one(("UCL", "控制上限"))
        child.one(("LCL", "控制下限"))
        child.one(("CL", "中心线"))
        child.records({"group": ("子组号", "子组序号", "组号"), "value": ("控制图数据", "统计值", "均值")}, driver="group")
        if not table.get("visual_evidence"):
            raise UnknownFact("控制图数值区域可读，但完整图本体尚未核实，不能用数值代替图。")
    raise UnknownFact("控制图元数据及序列已读取；完整图形和短期能力区块的视觉对应仍需人工确认。")


def _inspection_normalize_result(facts):
    for table, row_number, columns, row in _inspection_records(facts):
        token = compact(_inspection_result(facts, table, row_number, columns, row))
        if token not in {"", "ok", "nok", "ng", "合格", "不合格", "pass", "fail", "通过", "不通过"}:
            raise DefectFact("W03", "供应商结论无法归一化为通过、不通过或空值。")
    return "结论文本可归一化；取值是否合法由IR-10单独判，未把负结论当不可解析。"


def _mcr_mean_range(facts):
    found = False
    for table, row_number, _, row in _mcr_records(facts):
        for side in ("uaes", "supplier"):
            aliases = {"mean": (side + "均值", side + "平均值", side + " mean"), "range": (side + "极差", side + " range")}
            binding = facts.columns(table, aliases)
            if not binding:
                continue
            found = True
            _, columns = binding
            values = [numeric(value) for value in row[side] if not empty(value)]
            if not values:
                raise UnknownFact("均值/极差存在，但逐件样本不可读，尚不能复算。")
            expected = {"mean": sum(values) / len(values), "range": max(values) - min(values)}
            for name, column in columns.items():
                value = facts.read(table, row_number, column)
                if abs(numeric(value) - expected[name]) > Decimal(".005"):
                    raise DefectFact("W06", "UAES逐件绝对实测复算的均值/极差与卷面不一致。")
    if not found:
        raise MissingStructure("UAES_MCR没有均值/极差列时该项无对象；未套用NIO指标。")


def _mcr_mapping_elements(facts):
    _mcr_family(facts)
    _mcr_meta(facts)
    _mcr_dates(facts)
    _mcr_two_sides(facts)
    _mcr_signatures(facts)
    _mcr_result_region(facts)
    # UAES does not carry directions, Centering/Range or point diagrams. Its
    # source-specific E01-E23 existence matrix keeps these as absent by design.
    facts.one(("零件图纸版本", "Drawing change date and number", "图纸版本", "版本号", "版本"), before=13)
    return "UAES要素矩阵已读取：类型/身份/日期/供应商/两侧样本/四方栏/版本；方向、Centering/Range、测点图按族无对象。"


def _mcr_version(facts):
    version = facts.one(("零件图纸版本", "Drawing change date and number", "图纸版本", "版本号", "版本"), before=13)
    history = facts.records({"version": ("Version", "版本"), "date": ("Date", "日期")}, driver="version", titles=("变更履历", "Change History"))
    last = history[-1][3]
    if compact(version) != compact(last["version"]):
        raise DefectFact("W04", "表头当前版本与变更履历最后一条版本不一致。")


def _mcr_filename(facts):
    filename = str(facts.audit.file_metadata.get("original_filename", Path(facts.audit.file_url).name))
    part = facts.one(("零件号", "Part number"), before=13)
    match = re.search(r"(?:零件号|PN)[_ -]?([A-Za-z0-9]+)", filename, re.I)
    if match and compact(match.group(1)) != compact(part):
        raise DefectFact("W04", "文件名明确零件号与原件内零件号不一致。")
    if not match:
        raise UnknownFact("文件名没有可可靠解析的零件号/版本/日期标签，未自行猜测。")


def _mcr_internal_metadata(facts):
    found = False
    for aliases in [("零件号", "Part number"), ("零件名称", "Part name"), ("项目", "项目名称"), ("项目阶段", "阶段")]:
        try:
            values = facts.values(aliases, required=False, before=13)
        except MissingStructure:
            continue
        found = True
        if len({compact(value) for value in values if not empty(value)}) > 1:
            raise DefectFact("B06", "封面与明细页的同名身份/项目/阶段字段冲突。")
    if not found:
        raise UnknownFact("封面与明细身份字段未定位，不能完成一致性核对。")


def _mcr_model_phase(facts):
    value = str(facts.one(("车型与阶段", "车型/阶段", "model_phase")))
    parts = [part.strip() for part in re.split(r"[/|]", value)]
    if len(parts) != 2 or not all(parts) or compact(parts[1]) not in {"pp", "p", "sop", "试制", "量产", "样件", "ots", "pv"}:
        raise DefectFact("W03", "车型与阶段复合值不能可靠拆分。")


def _mcr_zero_empty(facts):
    rows = _mcr_records(facts)
    for side in ("uaes", "supplier"):
        width = max(len(row[side]) for _, _, _, row in rows)
        for index in range(width):
            values = [row[side][index] for _, _, _, row in rows if index < len(row[side])]
            if any(empty(value) for value in values) and any(not empty(value) and numeric(value) == 0 for value in values):
                raise UnknownFact("同列同时存在0与空白；0按真实测量计入，尚无证据证明二者表示同义空值，未判混用缺陷。")
    return "未发现可确证的同义空白/0混用；真实数值0没有被当作空样本。"


def _inspection_quartet(facts):
    reports = {
        "尺寸报告": ("尺寸报告", "Dimension report", "Dimensional report"),
        "材料报告": ("材料报告", "Material report"),
        "功能报告": ("功能报告", "Function report", "Functional report"),
        "可靠性报告": ("可靠性报告", "Reliability report"),
    }
    for title, aliases in reports.items():
        found = False
        for table in facts.tables():
            if any(key(alias) in key(table["id"]) for alias in aliases):
                facts.columns(table, {"standard": _IR["standard"]})
                found = True
            for address, item in table["cells"].items():
                if item.get("state") == "known" and header(item.get("value"), aliases):
                    row, column = coordinate_to_tuple(address)
                    facts.read(table, row, column)
                    found = True
        if not found:
            if any(table.get("visual_evidence") or any(item.get("state") != "known" for item in table["cells"].values()) for table in facts.tables()):
                raise UnknownFact("未确认" + title + "；未知图像/公式区域仍需核对，未推定缺报告。")
            raise DefectFact("B05", "UAES四报告合一结构未定位到" + title + "；受控F-PU-207模板承载此区块，需确认文件完整性。")


def _material_filelist(facts):
    rows = facts.records({"file": ("文件名", "提交文件名")}, titles=("文件清单", "提交清单"))
    context = facts.context
    if context is None:
        raise UnknownFact("文件清单已读取，但没有完整冻结文件集合，不能双向对账。")
    if context.unavailable_files:
        raise OSError("已提交文件无法访问，当前属于技术读取失败。")
    names = {str(row["file"]).strip() for _, _, _, row in rows}
    actual = {str(item.file_metadata.get("original_filename", Path(item.file_url).name)) for item in context.by_audit_id.values()}
    if names - actual:
        raise DefectFact("B02", "材料清单明确列示但本批未提交：" + ",".join(sorted(names - actual)))
    if actual - names:
        raise DefectFact("W01", "实际提交文件未列入材料文件清单：" + ",".join(sorted(actual - names)))


def _material_split(facts):
    found = False
    for table, start, columns in facts.regions({**_MATERIAL, **_UAES_MATERIAL_COLUMNS}):
        parent = False
        bottom = max([start] + [span[2] for span in table.get("merges", []) if span[0] <= start <= span[2] and any(span[1] <= column <= span[3] for column in columns.values())])
        for row in range(bottom + 1, table["rows"] + 1):
            values = {name: facts.read(table, row, column, vertical_only=True) for name, column in columns.items()}
            if all(empty(value) for value in values.values()):
                continue
            if any(str(value or "").startswith(_FOOTERS) for value in values.values()):
                break
            if not empty(values["serial"]):
                parent = True
                continue
            if any(not empty(values[name]) for name in _UAES_MATERIAL_COLUMNS):
                found = True
                if not parent or not empty(values["part"]) or not empty(values["name"]):
                    raise DefectFact("B04", "材料拆分行没有可归属父节点，或前导标识留空方式不一致。")
    return "已核对一物多材拆分行的前导字段与父节点。" if found else "已读取适用数据区，没有发现需归属的材料拆分行。"


def _msa_metadata(facts):
    groups: dict = {}
    found = False
    for table in facts.tables():
        if not any(term in key(table["id"]) for term in ("grr", "kappa", "数据表", "分析表", "图表页", "重复性", "再现性")):
            continue
        child = _table_facts(facts, table)
        identity = compact(child.one(_MSA_GAUGES["id"]))
        values = tuple(compact(child.one(aliases)) for aliases in [_MSA_GAUGES["name"], ("零件号", "零件编号"), ("规格公差", "规格"), ("评价人姓名", "评价人", "操作者")])
        found = True
        if identity in groups and groups[identity] != values:
            raise DefectFact("W04", "同量具数据/分析/图表页的名称、零件、规格或评价人元数据冲突。")
        groups[identity] = values
    if not found:
        raise UnknownFact("未定位到同量具多联表单的完整元数据。")
    # Plan/report agreement additionally requires the declared scope and all
    # actual frozen report files; retain unknown if that context is unavailable.
    _msa_sets(facts, attributes=True)


def _msa_declared_types(facts):
    scope = compact(facts.one(("提交范围", "本次范围")))
    context = facts.context
    if context is None:
        raise UnknownFact("已读取范围声明，未提供冻结文件集合，不能把独立报告判缺失。")
    if context.unavailable_files:
        raise OSError("本批已提供资料无法读取，类型齐套尚未核实。")
    available = []
    for item in context.by_audit_id.values():
        available.extend(Facts(item).tables())
    if "计划" in scope and not any(any(term in key(table["id"]) for term in ("msa计划", "测量系统分析计划")) for table in available):
        raise DefectFact("B02", "声明范围包含MSA计划，但本批冻结资料中未定位到计划表。")
    if any(term in scope for term in ("报告", "grr", "kappa")) and not any(any(term in key(table["id"]) for term in ("grr", "kappa", "重复性", "再现性")) for table in available):
        raise DefectFact("B02", "声明范围包含MSA报告，但本批冻结资料中未定位到任何适用报告。")


def _msa_versions(facts):
    context = facts.context
    if context is None:
        raise UnknownFact("未提供本批冻结文件集合，不能核对多版本并存。")
    if context.unavailable_files:
        raise OSError("本批有已提供文件不可读取，多版本检查尚未完成。")
    seen: dict = {}
    for item in context.by_audit_id.values():
        name = str(item.file_metadata.get("original_filename", Path(item.file_url).name))
        if not any(term in key(name) for term in ("msa", "grr", "kappa", "测量系统", "重复性", "再现性")):
            continue
        other = Facts(item)
        identity = other.one(_MSA_GAUGES["id"])
        part = other.one(("零件号", "零件编号"))
        form = "plan" if "计划" in name else "kappa" if "kappa" in name.casefold() else "grr"
        gauge = (compact(identity), compact(part), form)
        signature = other.data["source"]["sha256"]
        # Different contents for the same gauge require a formal-version
        # designation, never upload-order selection.
        if gauge in seen and seen[gauge] != signature:
            raise DefectFact("W04", "同量具存在内容不同的多份MSA表单，需确认正式版本，未自行择一。")
        seen[gauge] = signature
        facts.sources.extend(other.sources)
    if not seen:
        raise UnknownFact("冻结文件未能可靠匹配MSA量具身份，未由相似文件名推定同版本。")


def _mcr_preservation(facts):
    part = facts.one(("零件号", "Part number"), before=13)
    if not isinstance(part, str):
        raise UnknownFact("零件号以数值存储；未有同源原文证明前导零，须人工确认标识保真。")
    dates = facts.values(("测量日期", "date of measuring"), before=13)
    for value in dates:
        if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            raise DefectFact("W03", "测量日期没有以YYYY-MM-DD格式保存；单号缺失未记问题。")


def _mcr_examples(facts):
    records = _mcr_records(facts)
    # Explicit examples are never passed to numeric predicates. A whole
    # coloured/locked row needs native style evidence as well as its text.
    if Path(facts.audit.file_url).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise UnknownFact("显式示例文本已排除；当前载体的锁定底色行尚需核对。")
    from openpyxl import load_workbook

    book = load_workbook(facts.audit.file_url, read_only=False, data_only=False)
    try:
        for table, row_number, columns, row in records:
            cells = [book[table["id"]].cell(row_number, column) for column in [*columns.values(), *row["uaes_columns"], *row["supplier_columns"]]]
            if all(cell.protection.locked for cell in cells) and all(cell.fill.patternType == "solid" for cell in cells):
                raise UnknownFact("实际候选行全部锁定且有底色，须确认是否模板说明/示例，未当真实测量通过。")
            if any(re.search(r"\bi\.e\.|\bExample\b|示例", str(cell.value or ""), re.I) for cell in cells):
                raise DefectFact("B04", "显式示例行仍混入实际候选数据区。")
    finally:
        book.close()


def _mcr_state_carrier(facts):
    found = False
    for table, start, columns in facts.regions({"id": _MCR["id"], "state": ("状态", "单点状态", "判定状态")}):
        found = True
        for row in range(start + 1, table["rows"] + 1):
            identity = effective(table, row, columns["id"])
            if empty(identity.get("value")):
                continue
            value = facts.read(table, row, columns["state"])
            if empty(value):
                raise UnknownFact("状态文本为空，底色是否为唯一承载尚需原件样式确认。")
    if not found:
        raise MissingStructure("UAES_MCR无强制单点状态列；实际接受方向交正式审核。")


def _capacity_constants(facts):
    for table in facts.tables():
        if not any(term in key(table["id"]) for term in ("控制图", "imr", "xbarr", "xbars")):
            continue
        child = _table_facts(facts, table)
        size = numeric(child.one(_CAP_REPORT_FIELDS["size"]))
        expected = {1: {"E2": "2.66", "d2": "1.128", "D4": "3.267", "D3": "0"}, 5: {"A2": ".577", "D3": "0", "D4": "2.114", "d2": "2.326"}}
        if size not in expected:
            raise UnknownFact("当前加载源只给出n=1与n=5的常数明值，尚无该实际子组容量的受控常数行，未生成或借用相邻行。")
        for name, value in expected[int(size)].items():
            if numeric(child.one((name,))) != Decimal(value):
                raise DefectFact("B06", "卷面控制图常数" + name + "与当前子组容量的源规定值不一致。")


def _mcr_references(facts):
    for table, start, _columns in facts.regions(_MCR):
        for address, item in table["cells"].items():
            row, column = coordinate_to_tuple(address)
            if not item.get("formula") or row >= start:
                continue
            facts.reference_value(table, row, column)
    return "表头引用已按实际源值回溯；缓存0或常数没有替代源空值，未新增单独业务方向。"


def _mcr_element_facts(agent):
    facts = Facts(agent.audit_file)
    operations = {
        "E01": _mcr_family,
        "E02": _mcr_meta,
        "E03": lambda f: f.one(("零件号", "Part number"), before=13),
        "E04": lambda f: f.one(("零件名称", "Part name"), before=13),
        "E05": lambda f: f.one(("项目", "项目名称"), before=13),
        "E06": lambda f: f.one(("项目阶段", "阶段"), before=13),
        "E07": _mcr_dates,
        "E08": lambda f: f.one(("供应商名称", "Supplier name"), before=13),
        "E09": lambda f: f.values(("测量员", "Measured by"), before=13),
        "E10": _mcr_records,
        "E13": lambda f: [numeric(row["nominal"]) for _, _, _, row in _mcr_records(f)],
        "E14": lambda f: [numeric(row["upper"]) for _, _, _, row in _mcr_records(f)],
        "E15": lambda f: [numeric(row["lower"]) for _, _, _, row in _mcr_records(f)],
        "E17": _mcr_samples,
        "E21": _mcr_result_region,
        "E22": _mcr_signatures,
        "E23": _mcr_version,
    }
    no_object = {"E11", "E12", "E16", "E18", "E19", "E20"}
    result = []
    for index in range(1, 24):
        element = f"E{index:02d}"
        identifier = "U02:M-02:" + element
        if element in no_object:
            observed = state_fact(identifier, "S01", "UAES_MCR本族无该要素要求；不是缺项，也未套用NIO。", status="not_checked")
        else:
            observed = facts.observe(identifier, lambda operation=operations[element]: operation(facts))
            if observed["status"] == "unknown" and "S05" in observed["message"]:
                observed["code"] = "S05"
        observed["source_id"] = "U02:M-02"
        result.append(observed)
    return result


def _inspection_gauges(facts):
    try:
        rows = facts.records({"id": _IR["id"], "method": ("检查方法", "供方检查方法", "检查工具", "测量方法"), "modality": ("量具模态", "测量模态")}, driver="id")
    except MissingStructure:
        pool = facts.one(("全局设备池", "全局测量设备", "测量设备池"))
        modality = compact(facts.one(("全局量具模态", "全局测量模态")))
        if empty(pool):
            raise DefectFact("W02", "行级量具未定位且全局设备池为空。")
        if modality not in {"计量型", "计数型", "变量型", "属性型"}:
            raise UnknownFact("全局量具模态未能可靠归一化；未使用量具名白名单猜测。")
        return "全局设备池和明确量具模态可读取，未按量具名称推定模态。"
    for _, _, _, row in rows:
        if empty(row["method"]):
            raise DefectFact("W02", "行级量具信息为空，需确认是否有全局设备池承载。")
        if compact(row["modality"]) not in {"计量型", "计数型", "变量型", "属性型"}:
            raise UnknownFact("行级量具模态未能可靠归一化；未使用量具名白名单。")


def _inspection_composite(facts):
    candidates = []
    for _, _, _, row in _inspection_records(facts):
        value = str(row["standard"] or "")
        if re.search(r"\d+(?:\.\d+)?\s*[xX×]\s*\d+(?:\.\d+)?\s*[°度]", value):
            candidates.append(compact(row["id"]))
        elif "锥度" in value:
            raise UnknownFact("锥度涉及的多个物理量尚未可靠拆分，需人工核对完整子维度。")
    if not candidates:
        return "已读取全部要求，未发现可确定的多物理量复合特征；未把单格样本斜杠当子要求。"
    rows = facts.records({"id": _IR["id"], "sub": ("子要求", "子维度", "复合特征子要求"), "unit": ("单位", "子要求单位")}, driver="id")
    for identity in candidates:
        units = {compact(row["unit"]) for _, _, _, row in rows if compact(row["id"]) == identity and not empty(row["sub"])}
        if not {"mm", "°"} <= units and not {"mm", "度"} <= units and not {"mm", "deg"} <= units:
            raise DefectFact("W02", "倒角复合特征没有同时拆出带独立单位的线性与角度子要求。")


def _mcr_context_states(agent, result):
    context = agent.group_context
    if context is None:
        return result
    if context.unavailable_files:
        return [state_fact(item["id"], None, "本批已提供资料无法读取，跨文件/跨形态核对未执行。", status="error") if item["id"] in {"U02:S-08", "U02:S-14", "U02:S-15"} else item for item in result]
    other = [item for item in context.by_audit_id.values() if item.audit_id != agent.audit_file.audit_id]
    if not other:
        return [
            state_fact(item["id"], "S01", "本批冻结集合只有本原件，没有双形态、同零件多报告或同时在批的两批对象；未追索外部历史。", status="not_checked")
            if item["id"] in {"U02:S-08", "U02:S-14", "U02:S-15"}
            else item
            for item in result
        ]
    return result


def _material_current_scope(facts, *, part=False):
    expected = getattr(facts.audit, "part" if part else "model", None)
    if not expected:
        raise UnknownFact("当前冻结上下文未提供可比较的" + ("零件范围" if part else "车型/项目名称") + "，不能用内部项目ID推定业务一致。")
    observed = facts.one(("零件号", "零件编号", "Part number", "Part No") if part else ("车型", "项目名称", "UAES项目编号"), before=5)
    if compact(observed) != compact(expected):
        raise DefectFact("B03", "材料清单的" + ("零件号" if part else "车型/项目") + "与当前明确审核范围不一致。")


def _material_effective_version(facts):
    context = facts.context
    current = facts.one(("零件号", "零件编号", "Part number", "Part No"), before=5)
    if context is None:
        raise UnknownFact("已读取当前零件号，但无完整冻结文件集合，不能确认同件有效版本唯一。")
    if context.unavailable_files:
        raise OSError("本批已提供文件不可读，有效版本核对未完成。")
    same = []
    for item in context.by_audit_id.values():
        if item.audit_id == facts.audit.audit_id:
            continue
        filename = str(item.file_metadata.get("original_filename", Path(item.file_url).name))
        if not any(term in filename for term in ("材料", "分供")):
            raise UnknownFact("其他冻结原件的类别未全部确定，未只按文件名推定不存在同件其他版本。")
        other = Facts(item)
        part = other.one(("零件号", "零件编号", "Part number", "Part No"), before=5)
        facts.sources.extend(other.sources)
        if compact(part) == compact(current):
            same.append(other)
    if same:
        raise UnknownFact("本批有同零件多份材料清单，须明确唯一有效提交件，未按上传顺序或版本大小自行择一。")
    return "当前完整冻结集合未见同零件其他材料清单；没有自行选择有效版本。"


def _mcr_index(facts):
    rows = facts.records({"sheet": ("页签名", "工作表名", "分页名称"), "rate": ("合格率", "合格率%")}, driver="sheet", titles=("汇总", "索引", "summary", "index"))
    actual = {table["id"]: table for table in facts.tables()}
    for _, _, _, row in rows:
        name = str(row["sheet"]).strip()
        if name not in actual:
            raise DefectFact("W06", "页签索引列出了本文件不存在的分页：" + name)
        child = _table_facts(facts, actual[name])
        detail = child.one(("合格率", "合格率%"))
        if numeric(row["rate"]) != numeric(detail):
            raise DefectFact("W06", "索引页与明细页已经印出的合格率不一致；未从超差方向重算合格率。")


def _material_declared_dependencies(facts):
    declared = str(facts.one(("随包文件清单", "关联文件清单", "随包文件")))
    context = facts.context
    if context is None:
        raise UnknownFact("随包声明已读取，但没有完整冻结文件集合，未推定对端缺失。")
    if context.unavailable_files:
        raise OSError("已提供随包资料无法读取，关联到位检查未完成。")
    wanted = [item.strip() for item in re.split(r"[,，;；\n]", declared) if item.strip()]
    actual = {str(item.file_metadata.get("original_filename", Path(item.file_url).name)) for item in context.by_audit_id.values()}
    if not wanted:
        raise UnknownFact("随包声明没有可可靠解析的文件名。")
    for filename in wanted:
        if filename not in actual:
            if any(term in filename.casefold() for term in ("图纸", "drawing")):
                raise UnknownFact("声明图纸尚未定位，须向甲方确认受控提供源（S04-C），不记供应商W01。")
            if Path(filename).suffix.lower() not in {".pdf", ".xlsx", ".xls", ".docx", ".xlsm"}:
                raise UnknownFact("随包声明是类别而非可对账的具体文件名，未凭文件名关键词推定缺件。")
            raise DefectFact("W01", "本批未识别到声明随包文件：" + filename + "，请确认并补充。")


def _paired_original_fact(agent, source_id, *, measurement):
    context = agent.group_context
    if context is None:
        return state_fact(source_id, None, "未提供完整冻结文件集合，不能核对同源双形态。", status="unknown")
    if context.unavailable_files:
        return state_fact(source_id, None, "本批已有原件无法读取，双形态比对尚未执行。", status="error")
    original = str(agent.audit_file.file_metadata.get("original_filename", Path(agent.audit_file.file_url).name))
    others = [item for item in context.by_audit_id.values() if item.audit_id != agent.audit_file.audit_id]
    if not others:
        return state_fact(source_id, "S01", "本批只有此原件，没有同时在批的同源另一形态；未追索外部文件。", status="not_checked")
    candidates = [
        item
        for item in others
        if Path(str(item.file_metadata.get("original_filename", Path(item.file_url).name))).stem == Path(original).stem
        and Path(item.file_url).suffix.lower() != Path(agent.audit_file.file_url).suffix.lower()
    ]
    if len(candidates) != 1:
        return state_fact(source_id, None, "本批同源双形态候选不唯一或未明确配对，文件名相似不能替代同源身份。", status="unknown")
    facts = Facts(agent.audit_file)
    try:
        other = Facts(candidates[0])
    except Exception:
        return state_fact(source_id, None, "已提供的配对原件无法读取，尚无业务结论。", status="error")

    def compare():
        for aliases in [
            ("零件号", "零件编号", "Part number", "Part No"),
            ("零件名称", "Part name"),
            ("版本号", "版本", "Revision"),
            ("项目号", "项目名称", "UAES项目编号"),
            ("客户名称", "客户", "Customer"),
        ]:
            if compact(facts.one(aliases, before=13)) != compact(other.one(aliases, before=13)):
                facts.sources.extend(other.sources)
                raise UnknownFact("双形态零件身份或版本不一致，尚未成立同源配对。")
        if measurement:

            def records(reader):
                result = {}
                for _, _, _, row in _mcr_records(reader):
                    identity = compact(row["id"])
                    if identity in result:
                        raise UnknownFact("双形态存在重复测点号，须确认分页/唯一映射，未覆盖前一条数据。")
                    result[identity] = (
                        compact(row["name"]),
                        numeric(row["nominal"]),
                        numeric(row["upper"]),
                        numeric(row["lower"]),
                        tuple(numeric(value) for value in row["uaes"]),
                        tuple(numeric(value) for value in row["supplier"]),
                    )
                return result
        else:

            def records(reader):
                result = {}
                for _, _, _, row in _material_rows(reader, {"weight": _WEIGHT}):
                    identity = compact(row["serial"])
                    if identity in result:
                        raise UnknownFact("双形态存在多个相同序号的切片，须确认切片映射，未任取末条数据。")
                    result[identity] = (compact(row["part"]), compact(row["name"]), numeric(row["weight"]))
                return result

        left, right = records(facts), records(other)
        facts.sources.extend(other.sources)
        if left != right:
            raise DefectFact("B06" if measurement else "W04", "同源原件两种形态的标识、条目数或逐行主要数值不一致。")
        return "同源配对成立，已对齐两种原件形态的身份、条目和逐行数值。"

    return facts.observe(source_id, compare)


def _msa_relation_scope(agent):
    identifier = "U05:MSA-PRE-15"
    facts = Facts(agent.audit_file)
    try:
        scope = compact(facts.one(("提交范围", "本次范围")))
    except (UnknownFact, DefectFact):
        return state_fact(identifier, None, "关联项需先取得可辨识范围声明；未把未交等同范围外。", status="unknown")
    if all(term in scope for term in ("不含控制计划", "不含pgqp1")):
        result = state_fact(identifier, "S01", "原件明确声明控制计划和PGQP1本就不在本次范围，未把未到冒充范围外。", status="not_checked")
        result["evidence"] = facts.evidence()
        return result
    if agent.group_context is None:
        return state_fact(identifier, None, "已读取范围声明，但冻结文件集合未提供，关联状态尚不能核实。", status="unknown")
    return state_fact(identifier, None, "范围声明已读取；需按具体量具与零件核对控制计划/PGQP1对端，未由名称推定齐套或缺件。", status="unknown")


def _mcr_batch(facts, agent, *, history=False):
    context = agent.group_context
    if context is None:
        raise UnknownFact("未提供完整冻结集合，不能核对本批多报告/同时在批的两批。")
    if context.unavailable_files:
        raise OSError("本批已提供资料无法读取，跨报告核对未完成。")
    typed = {item.audit_id for values in context.by_doc_type.values() for item in values}
    if any(item.audit_id not in typed for item in context.by_audit_id.values()):
        raise UnknownFact("冻结集合有原件未确定类型，未按文件名推定不存在另一份测量报告。")
    candidates = {item.audit_id: item for kind, values in context.by_doc_type.items() if kind in {agent.rule.type, "E02", "测量比较报告"} for item in values}
    others = [item for identity, item in candidates.items() if identity != facts.audit.audit_id]
    current_part = facts.one(("零件号", "Part number"), before=13)
    if not others:
        return "冻结类型集合没有同类另一份测量报告，未追索批次外历史。"
    project = facts.one(("项目", "项目名称", "项目号"), before=13)
    version = facts.one(("版本号", "版本", "Revision"), before=13)
    supplier = facts.one(("供应商名称", "Supplier name"), before=13)
    stage = facts.one(("项目阶段", "阶段"), before=13)
    for item in others:
        other = Facts(item)
        part = other.one(("零件号", "Part number"), before=13)
        other_project = other.one(("项目", "项目名称", "项目号"), before=13)
        if compact(part) != compact(current_part) or compact(project) != compact(other_project):
            continue
        other_version = other.one(("版本号", "版本", "Revision"), before=13)
        other_supplier = other.one(("供应商名称", "Supplier name"), before=13)
        other_stage = other.one(("项目阶段", "阶段"), before=13)
        facts.sources.extend(other.sources)
        if compact(supplier) != compact(other_supplier) or compact(stage) != compact(other_stage):
            raise DefectFact("B02", "同项目同零件多报告的供应商或阶段信息互斥。")
        if not history:
            if compact(version) == compact(other_version):
                raise DefectFact("W04", "本批存在同零件同版本多份测量报告，需确认重复原因，未择一覆盖。")
            continue
        current_batch = facts.one(("批次号", "提交批次"), before=13)
        other_batch = other.one(("批次号", "提交批次"), before=13)
        facts.sources.extend(other.sources)
        if compact(current_batch) == compact(other_batch):
            continue
        current_date = normalize_excel_date(facts.one(("报告日期", "测量日期"), before=13))
        other_date = normalize_excel_date(other.one(("报告日期", "测量日期"), before=13))
        facts.sources.extend(other.sources)
        if current_date is None or other_date is None:
            raise UnknownFact("两批日期未能可靠解析，不能判版本倒挂。")

        def revision(value):
            text = str(value).strip()
            match = re.fullmatch(r"[vV]?(\d+)", text)
            if match:
                return int(match.group(1))
            if re.fullmatch(r"[A-Z]", text):
                return ord(text)
            raise UnknownFact("两批版本号缺少可验证排序体系，未按普通字符串猜测倒挂。")

        left, right = revision(version), revision(other_version)
        if current_date > other_date and left < right or other_date > current_date and right < left:
            raise DefectFact("B02", "同时在本批冻结集合的两批报告存在新日期配旧版本的倒挂。")
        if left == right:
            raise DefectFact("W04", "同时在批的两批报告为同版本，需说明新旧与重复原因。")
    return "已核对当前冻结集合的同件报告，没有调用外部上一批结果。"
