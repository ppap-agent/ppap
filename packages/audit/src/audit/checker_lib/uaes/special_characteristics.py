"""U12 special characteristic list: part identity and the four required columns of every characteristic row."""

from __future__ import annotations

from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .native import MissingFact, UaesSourceAgent, UnknownFact
from .resources import combine
from .resources import special_list as resources

_REQUIRED = {"特性编号": "编号/序号", "特性名称": "产品特性/过程特性/特殊特性名称", "特性值": "特性及公差/特性值", "特性类别": "特性类型/特性类别"}
_NAMES = ("特性名称", "过程特性名称")


def characteristic_rows(agent: UaesExcelAgent, ctx: RuleContext) -> list[tuple[str, Any, dict]]:
    """(sheet title, row, resolved columns) of every row that numbers, names or values a characteristic."""
    found = []
    for sheet in Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE):
        section = agent.section(sheet, "特殊特性区")
        columns = section.columns(optional=("特性编号", "零件", *_NAMES, "特性值", "特性类别"))
        for row in section.rows_from(columns):
            found.append((sheet.title, row, columns))
    return found


def filled(row, name: str, columns: dict) -> bool:
    return name in columns and bool(compact(row.value(name)))


class UaesSpecialCharacteristicsAgent(UaesExcelAgent):
    # Tables of spreadsheets and PDFs; a Word original has no table this reader can load.
    SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".pdf"})
    # Leaves bind each clause to its method by the clause's own ID; every cell read is evidence.
    SOURCE_CLAUSE_IDS = True
    PRECHECK_METHODS = {"U12:1": "rule_1", "U12:2": "rule_2"}
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT)

    def precheck(self):
        """Preparation facts still come from the snapshot reader; its U12 identity and row facts run these clauses."""
        from .precheck import structural_facts

        return structural_facts(_Preparation(self))

    def unreadable(self) -> str | None:
        if self.untabled():
            return "PDF原件未能读出表格"
        if pages := self.unread_pages():
            return f"原件第{'、'.join(map(str, pages))}页没有文字层"
        return None

    @rule("物料名称与编号必填")
    def rule_1(self, ctx: RuleContext):
        rows = characteristic_rows(self, ctx)
        per_row = [(title, row) for title, row, columns in rows if "零件" in columns]
        if per_row:
            # "产品名称/零件号" names each characteristic's part: both fields, once per row.
            for title, row in per_row:
                if not compact(row.value("零件")):
                    if self.uncomputed(Sheet(ctx, row.ws), row.row_index, row.columns["零件"].header_col):
                        raise unable(f"“{title}”第{row.row_index}行产品名称/零件号是没有计算结果的公式，需人工复核。")
                    return "NG", f"特殊特性清单“{title}”第{row.row_index}行产品名称/零件号为空。"
            return "PASS", f"特殊特性清单“产品名称/零件号”列在全部{len(per_row)}个特性行均已填写（物料名称与编号）。"
        names, numbers = [], []
        # A list without the per-row column states its part once, as labelled header fields of any sheet.
        for sheet in (Sheet(ctx, ws) for ws in ctx.workbook.worksheets):
            names.extend(self.labelled(sheet, resources.PART_NAME))
            numbers.extend(self.labelled(sheet, resources.PART_NUMBER))
        names = [value for value in names if compact(value)]
        numbers = [value for value in numbers if compact(value)]
        if names and numbers:
            return "PASS", f"特殊特性清单物料名称“{names[0]}”、物料编号“{numbers[0]}”均已填写。"
        if reason := self.unreadable():
            raise unable(f"{reason}，尚不能确认物料名称与物料编号，需人工复核。")
        missing = "、".join(label for label, values in (("物料名称", names), ("物料编号", numbers)) if not values)
        return "NG", f"特殊特性清单没有填写{missing}（未见物料名称/产品名称、物料编号/零件号字段或其内容为空）。"

    @rule("特殊特性列与全部物理行必填")
    def rule_2(self, ctx: RuleContext):
        sheets = Table(ctx).find_sheets("特殊特性清单", on_missing=Decision.IGNORE)
        if not sheets:
            if reason := self.unreadable():
                raise unable(f"{reason}，尚不能确认特殊特性清单的四列，需人工复核。")
            raise unable("未能定位特殊特性清单的表头（编号/序号、特性名称、特性值、特性类别），检查程序未能读取，需人工复核。")
        rows = characteristic_rows(self, ctx)
        for sheet in sheets:
            section = self.section(sheet, "特殊特性区")
            columns = section.columns(optional=("特性编号", *_NAMES, "特性值", "特性类别"))
            missing = [label for name, label in _REQUIRED.items() if name not in columns and not (name == "特性名称" and "过程特性名称" in columns)]
            if missing:
                return "NG", f"特殊特性清单“{sheet.title}”缺少{'、'.join(missing)}列。"
        if not rows:
            raise unable("特殊特性清单没有可读取的特性行，不能按空范围通过。")
        for title, row, columns in rows:
            name = next((str(row.value(column)).strip() for column in _NAMES if filled(row, column, columns)), "")
            value = str(row.value("特性值")).strip() if filled(row, "特性值", columns) else ""
            where = f"特殊特性清单“{title}”第{row.row_index}行"
            # Reading 1 (product owner): every row that writes a characteristic needs its serial number.
            for column, label in (("特性编号", "编号/序号"), (None, "特性名称"), ("特性值", "特性值"), ("特性类别", "特性类别")):
                present = bool(name) if column is None else filled(row, column, columns)
                if present:
                    continue
                if column is not None and self.uncomputed(Sheet(ctx, row.ws), row.row_index, columns[column].header_col):
                    raise unable(f"{where}{label}是没有计算结果的公式，需人工复核。")
                described = f"（特性“{name or value}”）" if name or value else ""
                return "NG", f"{where}{described}{label}为空。"
        return "PASS", f"特殊特性清单全部{len(rows)}个特性行的序号、特性名称、特性值、特性类别均已填写。"


class _Preparation(UaesSourceAgent):
    """The snapshot reader's U12 structure facts, with the identity and row facts taken from the clauses."""

    def __init__(self, carrier: UaesSpecialCharacteristicsAgent):
        super().__init__(carrier.audit_file, carrier.rule, group_context=carrier.group_context)
        self.carrier = carrier

    def _clause(self, method: str) -> tuple[str, str]:
        from audit.engine.checkers.rule_declarations import discover_declared_rules

        names = [name for name, _description in discover_declared_rules(type(self.carrier))]
        result = self.carrier.check(names.index(method))
        if result is None:
            raise unable(f"条款{method}没有执行结果，需人工复核。")
        if result.evidence is not None:
            self._evidence_sources.extend(source for source in result.evidence.sources if source not in self._evidence_sources)
        return result.status, result.suggestions

    def identity(self, name):
        status, message = self._clause("rule_1")
        if status == "NG":
            raise MissingFact(message)
        if status != "PASS":
            raise UnknownFact(message)
        return message

    def rule_2(self):
        return self._clause("rule_2")
