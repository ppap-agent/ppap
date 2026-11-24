"""UAES U11: sample stage history, UAES v1.0 revision 3 (SSH:1, SSH:2).

The original is read as tables (a spreadsheet's cells, a PDF's converted tables); every verdict is decided in code
over the header and row cells it reads. A scanned PDF is not read.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.reader import HOLLOW, PLACEHOLDERS, Place, Reader, compact, resource, unable
from audit.engine.rule_engine import RuleContext, Sheet

# ---- SSH ------------------------------------------------------------------------------------------

# SSH:1: the five fields of the 工程更改一览表 and the spellings taken as the same field ("含义相近的，按满足要求处理";
# the clause names "变更说明""更改日期""修订记录" as such spellings, and 日期/变更时间/变更日期 for 时间).
FIELDS = {
    "项目": ("项目",),
    "零件号": ("零件号", "件号", "零件编号", "物料号", "物料编号", "partno", "partnumber"),
    "变更内容简述": ("变更内容", "更改内容", "变更说明", "更改说明", "变更描述", "更改描述", "修订记录", "修订内容"),
    "变更原因": ("变更原因", "更改原因", "修改原因", "修订原因"),
    "时间": ("时间", "日期"),
}
# Column names the clause hands to a person when a field is not found ("变更履历""其他""备注" and the like).
UNSURE = ("履历", "其他", "其它", "备注")

# SSH:2: the five dimensions and the column names the clause maps to each.
DIMENSIONS = {
    "批次": ("批次", "样件编号", "试制批号"),
    "数量": ("数量", "送样件数"),
    "送样日期": ("日期", "时间", "送样日"),
    "交付目的": ("变更内容", "设变说明", "目的"),
    "工程设变状态": ("设变状态", "更改状态"),
}
SAMPLE_TABLE = ("样品提交履历", "送样履历")
HISTORY_TITLE = "工程更改一览表"
# Sign-off lines close a table.
SIGN_OFF = ("编制", "校对", "审核", "批准", "prepared", "approved")


def _hits(text: str, words: tuple[str, ...]) -> bool:
    return any(compact(word) in text for word in words)


def _day(value: Any) -> date | None:
    from audit.engine.checkers.date_utils import normalize_excel_date

    if hasattr(value, "date") and callable(value.date):
        return value.date()
    if isinstance(value, date):
        return value
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.fullmatch(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", text)
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None
    found = normalize_excel_date(value)
    return found if isinstance(found, date) else None


class SampleHistoryR3(Reader):
    DOCUMENT = ((), (), ())
    EXTRA_RESOURCE = resource(DOCUMENT)

    @staticmethod
    def sheets(ctx: RuleContext) -> list[Sheet]:
        return [Sheet(ctx, ws) for ws in ctx.workbook.worksheets]

    def readable(self) -> None:
        if unread := self.unread_pages():
            raise unable(f"原件第{'、'.join(map(str, unread))}页为扫描页（没有文字层），检查程序不读取扫描件，需人工复核。")

    def titled(self, ctx: RuleContext, words: tuple[str, ...]) -> bool:
        return any(_hits(compact(sheet.title), words) or any(_hits(compact(self.raw(cell)), words) for cell in self.cells(sheet)) for sheet in self.sheets(ctx))

    def header(self, ctx: RuleContext, names: dict[str, tuple[str, ...]], titled: tuple[str, ...]):
        """The header row, under a title of `titled` (in the sheet name or a cell above), naming most of `names`:
        (sheet, row, name -> [(column, written header)])."""
        best = None
        for sheet in self.sheets(ctx):
            rows: dict[int, dict[str, list[tuple[int, str]]]] = {}
            for cell in self.cells(sheet):
                text = compact(self.raw(cell))
                if not text or len(text) > 30:
                    continue
                for name, words in names.items():
                    if _hits(text, words):
                        rows.setdefault(cell.row_index, {}).setdefault(name, []).append((cell.col_index, self.raw(cell)))
            for row, mapped in rows.items():
                above = [compact(self.raw(cell)) for cell in self.cells(sheet, row_max=row)]
                if not (_hits(compact(sheet.title), titled) or any(_hits(text, titled) for text in above)):
                    continue
                if best is None or len(mapped) > len(best[2]):
                    best = (sheet, row, mapped)
        return best

    def written(self, sheet: Sheet, row: int) -> list[tuple[int, str]]:
        found = [(cell.col_index, self.raw(cell)) for cell in self.cells(sheet, row_min=row, row_max=row)]
        for column, _text in found:
            self.cite(sheet, row, column)
        return found

    @rule("工程更改一览表及五个字段")
    def rule_1(self, ctx: RuleContext):
        self.readable()
        if not self.titled(ctx, (HISTORY_TITLE,)):
            if self.untabled():
                raise unable("PDF原件未能转换出表格，检查程序未能确认是否有“工程更改一览表”，需人工复核。")
            return "NG", "文件中没有“工程更改一览表”。"
        found = self.header(ctx, FIELDS, (HISTORY_TITLE,))
        if found is None:
            return "NG", "工程更改一览表没有可识别的表头，缺项目、零件号、变更内容简述、变更原因、时间五个字段。"
        sheet, row, mapped = found
        written = self.written(sheet, row)
        where = f"工程更改一览表“{sheet.title}”第{row}行表头（{'、'.join(text for _column, text in written)}）"
        missing = [name for name in FIELDS if name not in mapped]
        if missing:
            used = {column for columns in mapped.values() for column, _text in columns}
            unsure = [text for column, text in written if column not in used and _hits(compact(text), UNSURE)]
            if unsure:
                raise unable(f"{where}未能对应{'、'.join(missing)}；表头中的“{'”“'.join(unsure)}”能否算作该字段，需人工裁定。")
            return "NG", f"{where}缺少{'、'.join(missing)}字段。"
        pairs = "；".join(f"{name}：{'、'.join(text for _column, text in columns)}" for name, columns in mapped.items())
        return "PASS", f"{where}含五个字段（{pairs}）。"

    @rule("送样五维度齐全有效与时间轴")
    def rule_2(self, ctx: RuleContext):
        self.readable()
        # A separate 样品提交履历表 is judged when present; else the 工程更改一览表 carries the history (clause's last paragraph).
        found, carrier = self.header(ctx, DIMENSIONS, SAMPLE_TABLE), "样品提交履历表"
        if found is None:
            found, carrier = self.header(ctx, DIMENSIONS, (HISTORY_TITLE,)), "工程更改一览表"
        if found is None:
            if self.untabled():
                raise unable("PDF原件未能转换出表格，检查程序未能读取样品阶段履历的表头，需人工复核。")
            return "NG", "样品阶段履历中没有可识别的履历表头，缺批次、数量、送样日期、交付目的、工程设变状态五类维度列。"
        sheet, row, mapped = found
        written = self.written(sheet, row)
        where = f"{carrier}“{sheet.title}”第{row}行表头（{'、'.join(text for _column, text in written)}）"
        pairs = "；".join(f"{'、'.join(text for _column, text in columns)}↦{name}" for name, columns in mapped.items())
        missing = [name for name in DIMENSIONS if name not in mapped]
        if missing:
            return "NG", f"{where}缺{'、'.join(missing)}维度列（按条款映射到的：{pairs or '无'}）。"
        blank: list[Place] = []
        placeholder: list[Place] = []
        future: list[Place] = []
        rows: list[int] = []
        dated: list[tuple[date, int]] = []
        today = date.today()
        last, _width = self.extent(sheet)
        for index in range(row + 1, last + 1):
            line = [compact(self.raw(cell)) for cell in self.cells(sheet, row_min=index, row_max=index)]
            if any(_hits(text, SIGN_OFF) for text in line):
                break
            entries = [(name, column, sheet.cell(index, column)) for name, columns in mapped.items() for column, _text in columns]
            if not any(cell.has_data() for _name, _column, cell in entries):
                continue
            rows.append(index)
            for name, column, cell in entries:
                value = cell.value()
                text = unicodedata.normalize("NFKC", "" if value is None else str(value)).strip()
                if not text or text in HOLLOW:
                    blank.append(Place.of(f"第{index}行{name}", sheet, index, column))
                elif compact(text) in PLACEHOLDERS:
                    placeholder.append(Place.of(f"第{index}行{name}“{text}”", sheet, index, column))
                elif name == "送样日期":
                    day = _day(value)
                    if day is None:
                        self.cite(sheet, index, column)
                        raise unable(f"{carrier}第{index}行送样日期“{text}”未能读为日期，需人工复核。")
                    dated.append((day, index))
                    if day > today:
                        future.append(Place.of(f"第{index}行送样日期{day.isoformat()}晚于审核基准日{today.isoformat()}", sheet, index, column))
        if not rows:
            return "NG", f"{where}下没有有效行。"
        problems = []
        if blank:
            problems.append("单元格空白：" + "、".join(map(self.named, blank[:8])) + ("等" if len(blank) > 8 else ""))
        if placeholder:
            problems.append("填占位词：" + "、".join(map(self.named, placeholder[:8])) + ("等" if len(placeholder) > 8 else ""))
        if future:
            problems.append("未来日期：" + "、".join(map(self.named, future[:8])))
        # The clause sorts the rows by sample date before comparing neighbours.
        ordered = sorted(dated)
        if any(earlier[0] > later[0] for earlier, later in zip(ordered, ordered[1:], strict=False)):
            problems.append("履历时间轴异常")
        if problems:
            return "NG", f"{where}（{pairs}）：" + "；".join(problems) + "。"
        return "PASS", f"{where}五类维度列齐全（{pairs}），{len(rows)}个有效行均已填写，送样日期按升序重排后无倒退、无晚于审核基准日的日期。"
