"""U07 PFMEA: lessons-learned input, S/O/D scoring facts, special characteristic marks and PGQP1 coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .identity import pair_identity
from .resources import combine, pgqp1, special_list
from .resources import pfmea as resources

# A special characteristic list is frozen under its document type or its UAES preset code.
_LISTS, _PGQP1 = ("过程特殊特性清单", "T12"), "E12"
_PART_LABELS = ("零件号", "零件编号", "零件号码", "物料编号", "客户零件号", "Part Number", "Part No", "产品件号 Part No.")
_PROJECT_LABELS = ("项目", "项目名称", "项目号", "UAES项目编号", "车型项目")
_LESSONS = ("历史问题", "客户抱怨", "过往失效案例", "类似产品问题")
_TEMPLATE_WORDS = ("例如", "示例", "请填写", "填写指南")
_SCORES = {"严重度": "S", "频度": "O", "探测度": "D"}
_REJECTED = (
    "S/O/D范围、安全/法规后果S≥9、仅轻微外观S为2~4、无探测措施D=10、相同条件评分一致均已核对无误；"
    "但条款中“O与失效起因和预防控制匹配”“D与探测方法和时机匹配”“后果与S明显不符”没有给出可程序化的判定标准"
    "（何为有效预防、各类探测方法对应的D值、何为明显不符均未定义），本部分已拒绝自动判定，请人工复核。"
)


def meaningful(value: Any) -> str:
    """Comparable text; a cell holding only punctuation (", ," on a repeated page header) reads blank."""
    text = compact(value)
    return text if re.search(r"[0-9a-z一-鿿]", text) else ""


def marks(value: Any) -> frozenset[str]:
    """The symbols of a characteristic mark: "3，5", "3 5" and "3,5" are the same mark; SC/2 is SC and 2."""
    return frozenset(re.findall(r"[a-z]+|\d+|[^\w\s,，、/;；.]", compact(value)))


@dataclass
class Item:
    """One failure item row of a PFMEA table; blank cells continue the item above as the form writes them."""

    sheet: Sheet
    row: int
    values: dict[str, str] = field(default_factory=dict)
    columns: dict[str, int] = field(default_factory=dict)

    def where(self) -> str:
        return f"PFMEA“{self.sheet.title}”第{self.row}行"

    def scored(self) -> bool:
        return any(self.values.get(name) for name in _SCORES)


class UaesPfmeaAgent(UaesExcelAgent):
    # Tables of spreadsheets and PDFs; a Word original has no table this reader can load.
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})
    # Leaves bind each clause to its method by the clause's own ID; every cell read is evidence.
    SOURCE_CLAUSE_IDS = True
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT, special_list.DOCUMENT, pgqp1.DOCUMENT)

    def unreadable(self) -> str | None:
        if self.untabled():
            return "PDF原件未能读出表格"
        if pages := self.unread_pages():
            return f"原件第{'、'.join(map(str, pages))}页没有文字层"
        return None

    def sheets(self, ctx: RuleContext) -> list[Sheet]:
        sheets = Table(ctx).find_sheets("PFMEA", on_missing=Decision.IGNORE)
        if not sheets:
            reason = self.unreadable()
            raise unable(f"{reason + '，' if reason else ''}未能定位PFMEA失效分析表（失效模式、失效起因、严重度、频度、探测度表头），检查程序未能读取，需人工复核。")
        return sheets

    def items(self, ctx: RuleContext) -> list[Item]:
        """Every failure item of every PFMEA table, the trailing legend/revision rows removed."""
        found: list[Item] = []
        for sheet in self.sheets(ctx):
            section = self.section(sheet, "PFMEA失效区")
            columns = section.columns(optional=tuple(spec.canonical_name for spec in resources.DOCUMENT[2]))
            if missing := [name for name in ("失效模式", *_SCORES) if name not in columns]:
                raise unable(f"PFMEA“{sheet.title}”未能定位{'、'.join(missing)}列，检查程序未能读取，需人工复核。")
            items: list[Item] = []
            previous: dict[str, str] = {}
            for row in section.rows_from(columns):
                cells = {name: row.cell(name) for name in columns}
                own = {name for name, cell in cells.items() if cell.row == row.row_index}
                # A score cell merged in from another column (a page banner, a revision-history line
                # under the table) means the row does not follow the table's columns: not a failure item.
                if any(cells[name].column != columns[name].header_col for name in _SCORES):
                    continue
                values = {name: meaningful(cell.value) and str(cell.value).strip() for name, cell in cells.items()}
                # A wrapped line of a merged item adds no mode, cause or score of its own.
                if not any(values[name] for name in ("失效模式", "失效起因", *_SCORES) if name in own):
                    continue
                if not values["失效模式"]:
                    # The scores of a mode belong to its effect: a new effect brings its own severity.
                    carried = ("功能", "要求", "失效模式", "失效后果") + (() if values.get("失效后果") else ("严重度",))
                    for name in carried:
                        if name in values and not values[name]:
                            values[name] = previous.get(name, "")
                if "失效起因" in values and not values["失效起因"]:
                    values["失效起因"] = previous.get("失效起因", "")
                    if not values["频度"]:
                        values["频度"] = previous.get("频度", "")
                for name in ("功能", "要求"):
                    if name in values and not values[name]:
                        values[name] = previous.get(name, "")
                previous = values
                items.append(Item(sheet, row.row_index, values, {name: column.header_col for name, column in columns.items()}))
            found.extend(self.without_footer(sheet, items))
        if not found:
            raise unable("PFMEA没有可读取的失效分析行，不能按空范围判定，需人工复核。")
        return found

    def without_footer(self, sheet: Sheet, items: list[Item]) -> list[Item]:
        """Rows after the last scored item are a legend, revision history or sign-off when the original says so."""
        last = max((index for index, item in enumerate(items) if item.scored()), default=-1)
        trailing = items[last + 1 :]
        if not trailing:
            return items
        rows, columns = self.extent(sheet)
        words = [word.casefold() for word in resources.FOOTER_MARKERS]
        for cell in sheet.scan_non_empty(row_min=trailing[0].row, row_max=rows, col_min=1, col_max=columns):
            if any(word in " ".join(str(cell.value()).split()).casefold() for word in words):
                return items[: last + 1]
        if self.footer_seen(sheet, trailing[0].row) is True:
            return items[: last + 1]
        raise unable(f"PFMEA“{sheet.title}”第{trailing[0].row}行起有未填S/O/D的行，原件单元格不能说明它们是表尾图例/履历还是缺少评分的失效项，需人工复核。")

    def footer_seen(self, sheet: Sheet, row: int) -> bool | None:
        """Only where the cells cannot tell: whether the rendered worksheet shows these rows as a legend or footer."""
        path = str(self.audit_file.file_url).lower()
        if path.endswith(".pdf") or sheet.ws.parent is not self._workbook_cache:
            return None
        legacy = self.legacy()
        table = next((table for table in legacy.native().tables() if table["id"] == sheet.title), None)
        return None if table is None else legacy.table_footer(table, row)

    def part_number(self, ctx: RuleContext) -> str:
        """The PFMEA part number, read beside its label; an unlabelled value is not identified."""
        # A part number has no spaces; V040 writes several "label：value" fields in one header cell.
        found = {compact(value.split()[0]).upper() for sheet in self.sheets(ctx) for value in self.labelled(sheet, _PART_LABELS) if compact(value)}
        if len(found) != 1 or not pair_identity(next(iter(found))):
            described = "零件号有多个取值：" + "、".join(sorted(found)) if found else "未标注零件号"
            raise unable(f"PFMEA{described}，尚不能确认本文件零件号，需人工复核。")
        return next(iter(found))

    def project(self, sheets: list[Sheet]) -> str | None:
        """The one project the sheets state; unstated, placeholder or several projects leave it unknown."""
        found = {compact(value).upper() for sheet in sheets for value in self.labelled(sheet, _PROJECT_LABELS) if compact(value)}
        return next(iter(found)) if len(found) == 1 and pair_identity(next(iter(found))) else None

    def require_readable(self, kinds: tuple[str, ...]) -> None:
        """A provided but unreadable original of the wanted type, or of an unknown type, may be the counterpart."""
        from audit.kinds import PpapDocumentType

        for kind in self.group_context.unavailable_files.values() if self.group_context else ():
            if (
                not kind
                or compact(kind) in {"unknown", "未知", "未知类型"}
                or kind in kinds
                or any(PpapDocumentType.from_string(kind) == PpapDocumentType.from_string(want) != PpapDocumentType.UNKNOWN for want in kinds)
            ):
                raise OSError("当前批次有已提供但无法读取的关联资料，尚不能排除所需对端。")

    def untyped(self) -> list[Any]:
        """Same-round originals without a frozen type: they can be neither counted nor excluded as a counterpart."""
        if self.group_context is None:
            return []
        typed = {item.audit_id for items in self.group_context.by_doc_type.values() for item in items}
        return [item for item in self.group_context.by_audit_id.values() if item.audit_id != self.audit_file.audit_id and item.audit_id not in typed]

    @rule("经验教训的实际输入引用")
    def rule_1(self, ctx: RuleContext):
        for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
            rows, columns = self.extent(sheet)
            for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns):
                text = str(cell.value())
                if cited := self.lesson(text):
                    return "PASS", f"PFMEA“{sheet.title}”第{cell.row_index}行引用经验教训：{cited}"
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            import fitz

            with fitz.open(str(self.audit_file.file_url)) as document:
                for page in document:
                    for block in page.get_text("blocks"):
                        if cited := self.lesson(block[4]):
                            return "PASS", f"PFMEA第{page.number + 1}页引用经验教训：{cited}"
        if reason := self.unreadable():
            raise unable(f"{reason}，可读文字中没有经验教训引用，其余内容尚未读取，需人工复核。")
        if pictured := self.header_pictures(ctx):
            raise unable(f"可读文字中没有经验教训引用，但表头区域{pictured}有图片，图片内容尚未核对，需人工复核。")
        return "NG", "PFMEA表头、修订记录及全部内容均未引用历史问题、客户抱怨、过往失效案例或类似产品问题等经验教训输入，不通过，请人工复核。"

    @staticmethod
    def lesson(text: str) -> str | None:
        for term in _LESSONS:
            if term not in text or any(word in text for word in _TEMPLATE_WORDS):
                continue
            if len(re.sub(r"[\W_]", "", text.split(term, 1)[1])) >= 4:
                return " ".join(text.split())[:200]
        return None

    def header_pictures(self, ctx: RuleContext) -> str:
        """Pictures placed above a failure table header: the header band could carry text only as an image."""
        if str(self.audit_file.file_url).lower().endswith(".pdf"):
            return ""
        places = []
        for sheet in Table(ctx).find_sheets("PFMEA", on_missing=Decision.IGNORE):
            header = self.section(sheet, "PFMEA失效区").resolved.header_row
            for image in getattr(sheet.ws, "_images", []):
                anchor = getattr(getattr(image, "anchor", None), "_from", None)
                if anchor is None or anchor.row + 1 < header:
                    places.append(f"“{sheet.title}”第{anchor.row + 1 if anchor else '?'}行")
        return "、".join(places)

    @staticmethod
    def score(item: Item, name: str) -> Decimal | None:
        try:
            value = Decimal(item.values[name])
        except (InvalidOperation, ValueError):
            return None
        return value if value.is_finite() else None

    @rule("逐行SOD整数及后果控制一致性")
    def rule_2(self, ctx: RuleContext):
        items = self.items(ctx)
        by_condition: dict[tuple[str, ...], tuple[Item, tuple]] = {}
        by_effect: dict[tuple[str, str], tuple[Item, Decimal]] = {}
        converted = str(self.audit_file.file_url).lower().endswith(".pdf")
        gaps: list[str] = []
        for item in items:
            scores = []
            for name, letter in _SCORES.items():
                raw = item.values[name]
                if not raw:
                    if self.uncomputed(item.sheet, item.row, item.columns[name]):
                        gaps.append(f"{item.where()}{letter}是没有计算结果的公式")
                        break
                    return "NG", f"{item.where()}{letter}（{name}）为空。"
                value = self.score(item, name)
                if value is None and converted and re.fullmatch(r"\d+(\s+\d+)+", raw):
                    # The PDF conversion merged the scores of neighbouring rows into one cell.
                    gaps.append(f"{item.where()}{letter}读出“{' '.join(raw.split())}”（PDF转换把相邻行的评分合在一格）")
                    break
                if value is None or value != value.to_integral_value() or not 1 <= value <= 10:
                    return "NG", f"{item.where()}{letter}（{name}）为“{raw}”，不是1至10的整数。"
                scores.append(value)
            if len(scores) < len(_SCORES):
                continue
            s, _o, d = scores
            effect = item.values.get("失效后果", "")
            if any(term in effect for term in ("安全", "法规", "整车召回", "人员伤亡")) and s < 9:
                return "NG", f"{item.where()}失效后果“{effect}”涉及安全/法规，严重度S={s}低于9。"
            if "轻微" in effect and "外观" in effect and not 2 <= s <= 4:
                return "NG", f"{item.where()}失效后果“{effect}”仅为轻微外观，严重度S={s}不在2至4。"
            detection = item.values.get("探测控制", "")
            if compact(detection) in {"无", "无法探测", "无探测措施", "none"} and d < 10:
                return "NG", f"{item.where()}探测控制为“{detection}”，探测度D={d}小于10。"
            condition = tuple(compact(item.values.get(name, "")) for name in ("失效模式", "失效起因", "预防控制", "探测控制"))
            if condition in by_condition and by_condition[condition][1] != tuple(scores):
                first, earlier = by_condition[condition]
                return "NG", (f"{first.where()}与{item.where()}失效模式、失效起因、预防和探测控制均相同，S/O/D却分别为{'/'.join(map(str, earlier))}与{'/'.join(map(str, scores))}。")
            by_condition.setdefault(condition, (item, tuple(scores)))
            consequence = (compact(item.values["失效模式"]), compact(effect))
            if all(consequence) and consequence in by_effect and by_effect[consequence][1] != s:
                first, severity = by_effect[consequence]
                return "NG", f"{first.where()}与{item.where()}失效模式“{item.values['失效模式']}”及失效后果“{effect}”相同（空白格沿用上一行），严重度S却分别为{severity}与{s}。"
            by_effect.setdefault(consequence, (item, s))
        if gaps:
            raise unable("、".join(gaps[:3]) + "，这些行的评分尚未读取，需人工复核；其余行" + _REJECTED)
        # Rejected with the user: the clause gives no checkable standard for these parts.
        raise unable(_REJECTED)

    def batch_lists(self, ctx: RuleContext, part: str, project: str | None) -> tuple[list[tuple[str, int, str, str, str]], list[str]]:
        """Marked characteristics of the same-round lists for this part and project, and the lists whose project is unknown."""
        found, undetermined = [], []
        lists = {item.audit_id: item for kind in _LISTS for item in self.batch_files(kind)}
        for item in lists.values():
            with self.reading(ctx, item, special_list):
                label = f"特殊特性清单“{Path(str(item.file_url)).name}”"
                sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
                rows = []
                for sheet in sheets:
                    section = self.section(sheet, "特殊特性区")
                    columns = section.columns(optional=("零件", "特性名称", "过程特性名称", "特性值", "特性类别"))
                    if "特性类别" not in columns:
                        continue
                    stated = {compact(value).upper() for value in self.labelled(sheet, special_list.PART_NUMBER)}
                    for row in section.rows_from(columns):
                        owner = compact(row.value("零件")).upper() if "零件" in columns else ""
                        # A list names the part on each row, or once in its header.
                        if not (part in owner if owner else part in stated):
                            continue
                        mark = str(row.value("特性类别") or "").strip()
                        if not marks(mark):
                            continue
                        # The mark column belongs to the (product) characteristic name; V040 writes its
                        # process column separately with control documents, not under this mark.
                        column = "特性名称" if "特性名称" in columns else "过程特性名称"
                        name = str(row.value(column)).strip() if column in columns and meaningful(row.value(column)) else ""
                        value = str(row.value("特性值")).strip() if "特性值" in columns and meaningful(row.value("特性值")) else ""
                        rows.append((label, row.row_index, name, value, mark))
                if not rows:
                    continue
                # U07:3 pairs 同项目、同物料编号: the project must be stated alike on both originals.
                theirs = self.project(sheets)
                if project is None or theirs is None:
                    undetermined.append(label)
                elif theirs == project:
                    found.extend(rows)
        return found, undetermined

    @rule("特殊特性清单全部产品过程特性和一致标识")
    def rule_3(self, ctx: RuleContext):
        items = self.items(ctx)
        if not any("类别" in item.values for item in items):
            return "NG", "PFMEA没有“特殊特性”或“分类”列，无法标注关键/重要/特殊特性。"
        marked = [item for item in items if marks(item.values.get("类别", ""))]
        if not marked:
            if reason := self.unreadable():
                raise unable(f"{reason}，可读部分的PFMEA特殊特性/分类列为空，需人工复核。")
            return "NG", "PFMEA“特殊特性/分类”列全部为空，没有任何行标注关键/重要/特殊特性符号。"
        vocabulary = frozenset().union(*(marks(item.values["类别"]) for item in marked))
        for item in items:
            # The 776/A40 forms append a characteristic's own class to its name: "71#平行度5", "72#位置度3,5".
            match = re.search(r"^\s*\d+#.*[一-鿿]\s*(\d+(?:\s*[,，]\s*\d+)*)\s*$", item.values.get("功能", ""))
            if match and marks(match.group(1)) <= vocabulary and not marks(item.values.get("类别", "")):
                return "NG", f"{item.where()}“{item.values['功能']}”自标为{match.group(1)}类特性，但特殊特性/分类列为空，已知关键特性未标识。"
        self.require_readable(_LISTS)
        unknown = self.untyped()
        if not any(self.batch_files(kind) for kind in _LISTS):
            if unknown:
                raise unable(f"同批次没有已识别的特殊特性清单，另有{len(unknown)}份原件类型未识别，尚不能确认是否缺少特殊特性清单，需人工复核。")
            return "NG", "同批次没有特殊特性清单，请提交同项目、同物料编号的特殊特性清单后一起复审。"
        part = self.part_number(ctx)
        project = self.project(self.sheets(ctx))
        characteristics, undetermined = self.batch_lists(ctx, part, project)
        if not characteristics:
            if undetermined:
                side = "PFMEA与" if project is None else ""
                raise unable(f"{'、'.join(undetermined)}有零件号{part}的特性，但{side}清单未写明项目（或为占位值），条款要求同项目、同物料编号，尚不能确认是同一份对端，需人工复核。")
            if unknown:
                raise unable(f"同批次特殊特性清单中没有零件号{part}的特性，另有{len(unknown)}份原件类型未识别，需人工复核。")
            return "NG", f"同批次特殊特性清单中没有同项目、零件号{part}的特殊特性，不存在同物料编号的特殊特性清单，请提交后复审。"
        unnamed = None
        for label, row, name, value, mark in characteristics:
            where = f"{label}第{row}行"
            symbols = marks(mark)
            if not any(marks(item.values.get("类别", "")) == symbols for item in marked):
                return "NG", f"{where}特性“{name or value or '（名称未能从原件文字读取）'}”标识为“{mark}”，PFMEA特殊特性/分类列中没有该标识，缺项或标识不一致。"
            if not name and not value:
                unnamed = unnamed or f"{where}特性名称与特性值未能从原件文字读取（标识“{mark}”）"
                continue
            found = [item for item in items if name and compact(name) in {compact(item.values.get("功能", "")), compact(item.values.get("要求", ""))}]
            found = found or [item for item in items if value and compact(value) in {compact(item.values.get(key, "")) for key in ("失效模式", "要求", "功能")}]
            if not found:
                return "NG", f"{where}特性“{name} {value}”（{mark}）在PFMEA功能/要求及失效模式中均未出现，缺项。"
            if not any(marks(item.values.get("类别", "")) == symbols for item in found):
                shown = "、".join(f"第{item.row}行“{item.values.get('类别') or '空'}”" for item in found[:3])
                return "NG", f"{where}特性“{name} {value}”标识为“{mark}”，PFMEA对应{shown}，标识不一致。"
        if unnamed:
            raise unable(unnamed + "，尚不能核对其在PFMEA中的分析，需人工复核。")
        if undetermined or unknown:
            pending = "、".join(undetermined) + "未写明项目" if undetermined else f"另有{len(unknown)}份原件类型未识别"
            raise unable(f"已配对清单的特性均一致，但{pending}，尚不能排除其中有不一致的同零件特性，需人工复核。")
        return "PASS", f"同批次特殊特性清单中零件号{part}的{len(characteristics)}项特性均在PFMEA中出现，且标识一致。"

    @rule("PGQP1重要特性覆盖")
    def rule_4(self, ctx: RuleContext):
        self.require_readable((_PGQP1,))
        plans = self.batch_files(_PGQP1)
        if not plans:
            if unknown := self.untyped():
                raise unable(f"同批次没有已识别的采购件质量计划，另有{len(unknown)}份原件类型未识别，尚不能确认是否缺少PGQP1，需人工复核。")
            return "NG", "同批次没有采购件质量计划（含PGQP1表），存在缺项文件，请补充后复审。"
        part = self.part_number(ctx)
        others, characteristics = [], []
        for plan in plans:
            with self.reading(ctx, plan, pgqp1):
                for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                    numbers = {compact(value).upper() for value in self.labelled(sheet, ("Part No/零件号",)) if compact(value)}
                    if part not in numbers:
                        others.extend(sorted(numbers) or ["未写零件号"])
                        continue
                    for row in self.section(sheet, "重要特性区").rows("特性项目"):
                        if meaningful(row.value("特性项目")):
                            characteristics.append((sheet.title, row.row_index, str(row.value("特性项目")).strip()))
        if not characteristics:
            if others:
                return "NG", f"同批次采购件质量计划PGQP1的零件号为{'、'.join(others)}，与PFMEA零件号{part}不同，没有同零件号的PGQP1，请补充后复审。"
            return "NG", f"同批次采购件质量计划中没有零件号{part}且含重要特性的PGQP1表，请补充后复审。"
        items = self.items(ctx)
        texts = [compact(item.values.get(name, "")) for item in items for name in ("功能", "要求", "失效模式")]
        for title, row, characteristic in characteristics:
            if not any(compact(characteristic) in text for text in texts if text):
                return "NG", f"PGQP1“{title}”第{row}行重要特性“{characteristic}”在PFMEA功能/要求及失效模式中均未出现，缺项。"
        return "PASS", f"PGQP1全部{len(characteristics)}项重要特性均在PFMEA中出现。"
