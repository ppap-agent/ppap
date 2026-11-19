"""UAES control plan clauses (U08) on the shared rule engine, and its same-round counterparts."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, original_name, unable
from .resources import combine, flowchart_counterpart, pfmea_counterpart, pgqp1, pgqp1_counterpart
from .resources import control_plan as resources

_PFD, _PFMEA, _PGQP1 = "过程流程图", "潜在过程失效模式及后果分析", "E12"
_PLACEHOLDERS = {"", "/", "-", "--", "—", "n/a", "na", "无", "none", "tbd", "待定"}
# A UAES part (material) number as written, e.g. F01RD0Y776; neighbours must not continue it.
_PART = re.compile(r"(?<![0-9A-Za-z])F[0-9A-Z]{9}(?![0-9A-Za-z])")
_MARKS = "■☑✓√✔□☐"
_UNCHECKED = "□☐"
_COLUMNS = ("CP工序号", "CP生产资源", "CP特性编号", "CP产品特性", "CP过程特性", "CP特殊特性", "CP规格公差", "评价测量技术", "CP频率", "CP控制方法")
_CHARACTERISTIC = ("CP产品特性", "CP过程特性", "CP规格公差")
# Words that make a header cell a label rather than a written value.
_LABEL_WORDS = ("更改等级", "联系人", "电话", "小组", "批准", "日期", "编号", "approval", "核心", "代码", "代号", "名称", "零件号", "版本", "编制", "date")

# U08:2 (customer ruling 2026-11-13): traceability and FIFO are read from the specification/tolerance column only,
# and the traceability chain covers incoming, warehousing and shipping. Operation names place a row in a stage;
# the stage words and identifier words are our reading of the originals, the customer did not list them.
# An identifier names its carrier (batch, heat, serial number, code, card); "可追溯" alone does not say how.
_STAGES = (
    ("进料", ("进料", "来料", "收货", "原材料", "原料", "物料检验", "入库检验", "iqc", "incoming")),
    ("出入库", ("入库", "出库", "仓", "库存", "储存", "存储", "存放", "领用", "发料", "storage", "stock", "warehouse")),
    ("出货", ("出货", "发货", "发运", "出荷", "交付", "delivery", "shipping", "outgoing")),
)
_TRACE = ("批次", "批号", "炉号", "序列号", "流水号", "条码", "条形码", "二维码", "流转卡", "随工单", "lot", "batch", "serial")
_FIFO = ("先进先出", "先入先出", "先到期先出", "fifo", "按批次先后", "批次先后")
# "库龄不超过N天" limits how long a part is stored; it does not say which batch leaves first.
_STORAGE_LIMIT = ("库龄", "库存天数", "存放期", "最长存放")

# U08:6 (customer ruling 2026-11-13): a race-track dimension is a dimension registered in the plan, not the word 跑道.
# Its row fills the characteristic, special characteristic and specification columns, names a dimension-like
# characteristic, and belongs to a manufacturing operation; capability control is SPC, a control chart, Cpk or Ppk
# in the evaluation, frequency or control method column. The word lists are our reading, not the customer's.
# A dimension characteristic (U08:6 race tracks, U08:10 PGQP1 dimensions) names a size, a geometric tolerance or a
# radius ("4XR38±0.5"); roughness, leakage, hardness, cleanliness and material are measured but are not dimensions.
_DIMENSION: tuple[str, ...] = ("尺寸", "直径", "孔径", "外径", "内径", "半径", "φ", "ø", "长度", "宽度", "厚度", "高度", "深度", "距离", "间距", "角度", "倒角")
_DIMENSION += ("位置度", "同轴度", "圆度", "圆柱度", "平面度", "平行度", "垂直度", "跳动", "轮廓度", "⊥", "⊕", "◎", "○", "//")
_RADIUS = re.compile(r"(?<![a-wyz])r(?:角|\d)")
_NOT_DIMENSION = ("粗糙度", "泄漏", "测漏", "硬度", "清洁度", "材料", "成分")
# Operations that re-check a dimension rather than make it (inspections, tests, audits) or only hold and move parts.
# An in-process inspection (过程检验, 巡检) belongs to the manufacturing it watches.
_RECHECK = ("检验", "检查", "检测", "测试", "试验", "审核", "认可", "gp12", "iqc", "oqc", "inspection", "audit")
_IN_PROCESS = ("过程检验", "过程检查", "巡检", "inprocess")
_NOT_MAKING = ("入库", "出库", "仓", "储存", "存储", "包装", "出货", "发货", "出荷", "交付", "storage", "stock", "packag", "delivery", "shipping")
_CAPABILITY = ("spc", "控制图", "cpk", "ppk", "x-r", "x̄-r", "xbar", "均值极差", "单值移动极差", "i-mr")


def _manufacturing(operation: str) -> bool:
    """Whether an operation makes or changes the part; an empty name says nothing."""
    text = compact(operation)
    if not text or any(word in text for word in _NOT_MAKING):
        return False
    return any(word in text for word in _IN_PROCESS) or not any(word in text for word in _RECHECK)


def _dimension(text: str) -> bool:
    """Whether a characteristic (its name and specification) is a dimension."""
    folded = compact(text)
    if any(word in folded for word in _NOT_DIMENSION):
        return False
    return any(word in folded for word in _DIMENSION) or _RADIUS.search(folded) is not None


def _trace_stage(operation: str) -> str | None:
    """The traceability stage an operation name stands for, if any."""
    text = compact(operation)
    return next((stage for stage, words in _STAGES if any(word in text for word in words)), None)


def _stage(value: str) -> str | None:
    """Prototype and pre-launch first: 试生产 also contains 生产."""
    text = compact(value)
    if any(term in text for term in ("样件", "试生产", "prototype", "protype", "prelaunch", "pre-launch", "pre-production")):
        return "pre-production"
    if any(term in text for term in ("量产", "生产", "sop", "production")):
        return "production"
    return None


def _has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def _key(text: Any) -> str:
    """Comparable form of a step or characteristic: its Chinese words when it has any, else all of it."""
    folded = compact(text)
    chinese = "".join(char for char in folded if _has_cjk(char))
    return chinese or folded


def _parts(values: list[str]) -> set[str]:
    """Part numbers written in field values; a value without one stands for itself."""
    found = set()
    for value in values:
        tokens = _PART.findall(unicodedata.normalize("NFKC", value).upper())
        found |= set(tokens) if tokens else ({compact(value).upper()} if compact(value) not in _PLACEHOLDERS else set())
    return found


def _written(value: Any, text: str) -> bool:
    """Whether `value` is written in comparable `text`, not as the tail of a longer number (2# in 102#)."""
    wanted = compact(value)
    return bool(wanted) and re.search(r"(?<![0-9.])" + re.escape(wanted), text) is not None


@dataclass
class PlanRow:
    row: int
    where: str
    operation: str
    cells: dict[str, str] = field(default_factory=dict)
    sheet: Any = None
    columns: dict[str, int] = field(default_factory=dict)
    # The two operations a row lies between when the original draws no line separating them.
    candidates: tuple[str, ...] = ()
    # The operation number cell that owns the row ("10", or "1.物料检验" when it also names the operation).
    step: str = ""

    def text(self, *names: str) -> str:
        return " ".join(self.cells.get(name, "") for name in names)

    def owners(self) -> tuple[str, ...]:
        """The operation(s) the row belongs to, each read with its operation number."""
        return tuple(f"{self.step} {name}" for name in ((self.operation,) if self.operation else self.candidates))


def operation_owners(sheet: Sheet, column: int, rows: range, skipped: set[int]) -> dict[int, tuple[str, ...]]:
    """The operation(s) each row belongs to, read from how the operation name column is drawn.

    Horizontal lines in the name column split it into blocks; no line is drawn inside a merged cell. A name owns
    its own cell (all rows of a merged one, wherever its text is aligned). A row of a block outside every name
    belongs to the one name above or below it, and to both when it lies between two: the drawing leaves it open.
    """
    ws, ops = sheet.ws, sheet.ctx.ops

    def span(row: int) -> tuple[int, int]:
        merged = ops.get_detailed_merge_info(ws, row, column)
        return (merged.min_row, merged.max_row) if merged else (row, row)

    def line_above(row: int) -> bool:
        if span(row)[0] != row:
            return False
        return bool(getattr(ws.cell(row, column).border.top, "style", None) or getattr(ws.cell(row - 1, column).border.bottom, "style", None))

    blocks: list[list[int]] = []
    for row in rows:
        if not blocks or line_above(row):
            blocks.append([])
        blocks[-1].append(row)
    owners: dict[int, tuple[str, ...]] = {}
    for block in blocks:
        names = []
        for row in block:
            top, bottom = span(row)
            if top == row and row not in skipped and (name := str(sheet.cell(row, column).value() or "").strip()):
                names.append((name, top, bottom))
        for row in block:
            inside = [name for name, top, bottom in names if top <= row <= bottom]
            above = [name for name, _top, bottom in names if bottom < row][-1:]
            below = [name for name, top, _bottom in names if top > row][:1]
            owners[row] = tuple(inside) or tuple(dict.fromkeys((*above, *below)))
    return owners


class UaesControlPlanAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT, pgqp1.DOCUMENT, pgqp1_counterpart.DOCUMENT, flowchart_counterpart.DOCUMENT, pfmea_counterpart.DOCUMENT)

    # ---- the control plan itself -------------------------------------------------------

    def plan_sheets(self, ctx: RuleContext) -> list[Sheet]:
        sheets = Table(ctx).find_sheets("控制计划", on_missing=Decision.IGNORE)
        if not sheets:
            if unread := self.unread_pages():
                raise unable(f"原件第{'、'.join(map(str, unread))}页没有文字层，检查程序未能读取控制计划表格，需人工复核。")
            if self.untabled():
                raise unable("PDF原件未能转换出控制计划表格，检查程序未能读取，需人工复核。")
            raise unable("未能在原件中找到控制计划工作表（工序名称/操作描述与评价/测量技术表头），检查程序未能读取，需人工复核。")
        return sheets

    def plan(self, ctx: RuleContext) -> list[PlanRow]:
        """Every characteristic row of the control plan; repeated page headers of a PDF are not rows."""
        rows = []
        for sheet in self.plan_sheets(ctx):
            section = self.section(sheet, "控制计划区")
            columns = section.columns("工序名称", optional=_COLUMNS)
            start = max(column.data_start_row for column in columns.values())
            _rows, width = self.extent(sheet)
            header = [self._cells(sheet, row, width) for row in range(1, start)]
            kept, repeated = [], set()
            for row in section.rows_from(columns):
                cells = self._cells(sheet, row.row_index, width)
                # A PDF page repeats the title block and column headings: each such row is one of the first page's.
                if cells and any(cells <= block for block in header if block):
                    repeated.add(row.row_index)
                else:
                    kept.append(row)
            if not kept:
                continue
            span = range(kept[0].row_index, kept[-1].row_index + 1)
            owners = operation_owners(sheet, columns["工序名称"].header_col, span, repeated)
            steps = operation_owners(sheet, columns["CP工序号"].header_col, span, repeated) if "CP工序号" in columns else {}
            for row in kept:
                names = owners.get(row.row_index, ())
                values = {name: str(row.value(name) or "").strip() for name in columns if name != "工序名称"}
                where = f"“{sheet.title}”第{row.row_index}行"
                operation, between = (names[0], ()) if len(names) == 1 else ("", names)
                numbers = steps.get(row.row_index, ())
                step = numbers[0] if len(numbers) == 1 else ""
                rows.append(PlanRow(row.row_index, where, operation, values, sheet, {name: column.header_col for name, column in columns.items()}, between, step))
        return rows

    @staticmethod
    def _cells(sheet: Sheet, row: int, width: int) -> frozenset[tuple[int, str]]:
        return frozenset((cell.col_index, compact(cell.value())) for cell in sheet.scan_non_empty(row_min=row, row_max=row, col_min=1, col_max=width))

    @staticmethod
    def spanned(row: PlanRow, *names: str) -> str:
        """The row's text in `names`, a blank cell inside a merged one reading the merged cell's value."""
        texts = []
        for name in names:
            value = row.cells.get(name, "")
            if not value and name in row.columns and row.sheet is not None:
                merged = row.sheet.ctx.ops.get_detailed_merge_info(row.sheet.ws, row.row, row.columns[name])
                if merged:
                    value = str(row.sheet.cell(merged.min_row, merged.min_col).value() or "").strip()
            texts.append(value)
        return " ".join(texts)

    def graphic_in_blank(self, rows: list[PlanRow], name: str) -> str | None:
        """Where a blank `name` cell of a PDF original is filled by a picture or strokes (or cannot be placed on its page)."""
        for row in rows:
            if compact(row.cells.get(name)) in _PLACEHOLDERS and name in row.columns and self.drawn(row.sheet, row.row, row.columns[name]) is not False:
                return row.where
        return None

    def plan_column(self, ctx: RuleContext, name: str) -> bool:
        """Whether every control plan sheet heads the column `name`."""
        return all(ctx.optional_column(self.section(sheet, "控制计划区").resolved, name) is not None for sheet in self.plan_sheets(ctx))

    def header_texts(self, ctx: RuleContext) -> list[str]:
        """Heading cells around each control plan header row."""
        texts = []
        for sheet in self.plan_sheets(ctx):
            header = self.section(sheet, "控制计划区").resolved.header_row
            _rows, width = self.extent(sheet)
            texts += [compact(cell.value()) for cell in sheet.scan_non_empty(row_min=header - 3, row_max=header + 2, col_min=1, col_max=width)]
        return texts

    # ---- header fields -------------------------------------------------------------------

    def plan_fields(self, ctx: RuleContext, aliases: tuple[str, ...]) -> list[tuple[str, str]]:
        """Header fields of the control plan: the title block above its column headings."""
        return [item for sheet in self.plan_sheets(ctx) for item in self.fields(sheet, aliases, last_row=self.section(sheet, "控制计划区").resolved.header_row - 1)]

    def fields(self, sheet: Sheet, aliases: tuple[str, ...], *, last_row: int | None = None) -> list[tuple[str, str]]:
        """(value, place) of each label of `aliases`: after its colon, on its last line, right of it, or below it."""
        wanted = sorted((compact(alias) for alias in aliases), key=len, reverse=True)
        rows, width = self.extent(sheet)
        found = []
        for cell in sheet.scan_non_empty(row_min=1, row_max=rows if last_row is None else min(rows, last_row), col_min=1, col_max=width):
            raw = unicodedata.normalize("NFKC", str(cell.value())).strip()
            match = re.match(r"(.*?)([:：])(.*)", raw, re.S)
            left, colon, right = (match.group(1), match.group(2), match.group(3)) if match else (raw, "", "")
            label = compact(left)
            alias = next((alias for alias in wanted if alias in label and not _has_cjk(label.replace(alias, "", 1))), None)
            if alias is None:
                continue
            place = f"“{sheet.title}”{cell.ws.cell(cell.row_index, cell.col_index).coordinate}"
            if colon and right.strip():
                found.append((right.strip(), place))
                continue
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if not colon and len(lines) > 1 and alias not in compact(lines[-1]) and not lines[-1].endswith((":", "：")) and "(" not in lines[-1]:
                found.append((lines[-1], place))
                continue
            value = ""
            neighbour = cell.find_right(max_offset=8)
            if neighbour is not None and not self._labelish(neighbour.value()):
                value = str(neighbour.value()).strip()
            else:
                below = sheet.cell(cell.row_index + 1, cell.col_index)
                if below.has_data() and not self._labelish(below.value()):
                    value = str(below.value()).strip()
            found.append((value, place))
        return found

    @staticmethod
    def _labelish(value: Any) -> bool:
        raw = str(value or "").strip()
        return raw.endswith((":", "：")) or any(word in compact(raw) for word in _LABEL_WORDS)

    def part_number(self, ctx: RuleContext) -> str:
        """The one part number this control plan states: its 零件号, else the number written in its part name."""
        values = [value for value, _place in self.plan_fields(ctx, resources.PART_NUMBER)]
        parts = _parts(values)
        if not parts:
            names = [value for value, _place in self.plan_fields(ctx, resources.PART_NAME)]
            parts = set(_PART.findall(unicodedata.normalize("NFKC", " ".join(names)).upper()))
        if len(parts) != 1:
            written = "、".join(sorted(parts)) or "无"
            raise unable(f"控制计划表头未能读出唯一的零件号（读到：{written}），尚不能按同零件号配对同批次文件，需人工复核。")
        return next(iter(parts))

    # ---- same-round counterparts ---------------------------------------------------------

    def counterpart(self, ctx: RuleContext, kind: str, label: str, document: Any, mine: str) -> tuple[Any | None, str]:
        """The same-part original of `kind`: (item, "") or (None, why it is missing); unknown identity is UNABLE."""
        files = self.batch_files(kind)
        if not files:
            return None, f"同批次没有{label}"
        same, other, unknown = [], [], []
        for item in files:
            parts = self.parts_of(ctx, item, document)
            name = original_name(item)
            if mine in parts:
                same.append(item)
            elif parts:
                other.append(f"{name}（零件号{'、'.join(sorted(parts))}）")
            else:
                unknown.append(name)
        if len(same) > 1:
            raise unable(f"同批次有多份零件号为{mine}的{label}，尚不能确定按哪一份核对，需人工复核。")
        if same:
            return same[0], ""
        if unknown:
            raise unable(f"同批次{label}{'、'.join(unknown)}未能读出零件号，尚不能确认是否与本控制计划（{mine}）同零件号，需人工复核。")
        return None, f"源错配：同批次{label}{'、'.join(other)}与本控制计划零件号{mine}不同，没有同零件号的{label}"

    def parts_of(self, ctx: RuleContext, item: Any, document: Any) -> set[str]:
        """Part numbers an original states: its 零件号 fields, else every part number in its text."""
        with self.reading(ctx, item, document):
            sheets = [Sheet(ctx, ws) for ws in ctx.workbook.worksheets]
            parts = _parts([value for sheet in sheets for value, _place in self.fields(sheet, resources.PART_NUMBER)])
            if parts:
                return parts
            if not str(item.file_url).lower().endswith(".pdf"):
                texts = []
                for sheet in sheets:
                    rows, width = self.extent(sheet)
                    texts += [str(cell.value()) for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=width)]
                return set(_PART.findall(unicodedata.normalize("NFKC", " ".join(texts)).upper()))
        return set(_PART.findall(unicodedata.normalize("NFKC", self.pdf_text(item)).upper()))

    @staticmethod
    def pdf_text(item: Any) -> str:
        import fitz

        source = Path(str(item.file_url))
        metadata = getattr(item, "file_metadata", None) or {}
        expected = metadata.get("source_sha256") or metadata.get("sha256")
        if expected and hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise OSError("本轮冻结关联资料已变化，不能完成本项核对。")
        with fitz.open(str(source)) as document:
            return "".join(page.get_text() for page in document)

    def drawing(self, ctx: RuleContext) -> tuple[Any | None, str]:
        """The same-part drawing among the untyped originals of this round (no document type names drawings)."""
        context = self.group_context
        if context is None:
            return None, "同批次没有其他文件"
        if any(not kind or compact(kind) in {"unknown", "未知"} for kind in context.unavailable_files.values()):
            raise unable("同批次有无法读取且类别未知的原件，尚不能排除其中有同物料编号的图纸，需人工复核。")
        typed = {item.audit_id for kind, items in context.by_doc_type.items() if kind and compact(kind) not in {"unknown", "未知"} for item in items}
        untyped = [item for item in context.by_audit_id.values() if item.audit_id != self.audit_file.audit_id and item.audit_id not in typed]
        if not untyped:
            return None, "同批次没有图纸独立文件（其余文件均为已识别的其他类型），请提交图纸一起审核"
        mine = self.part_number(ctx)
        same, other, unknown = [], [], []
        for item in untyped:
            name = original_name(item)
            try:
                parts = self.parts_of(ctx, item, resources) if not name.lower().endswith(".pdf") else set(_PART.findall(unicodedata.normalize("NFKC", self.pdf_text(item)).upper()))
            except OSError:
                raise
            except Exception:
                parts = set()
            if not parts and name.lower().endswith(".pdf"):
                # A title block lettered with strokes is outside the text layer: read the drawing number from its image.
                number = self.drawing_title(item).get("drawing_number") or ""
                parts = set(_PART.findall("".join(unicodedata.normalize("NFKC", number).split()).upper()))
            if mine in parts:
                same.append(name)
            elif parts:
                other.append(f"{name}（写有{'、'.join(sorted(parts))}）")
            else:
                unknown.append(name)
        if same:
            raise unable(f"同批次文件{'、'.join(same)}写有本零件号{mine}，其图纸标注（特殊特性、CPK要求、日期）为图形，检查程序尚不能读取，需人工复核。")
        if unknown:
            raise unable(f"同批次文件{'、'.join(unknown)}的文字层中没有可读的零件号/图号，尚不能确认是否为同物料编号{mine}的图纸，需人工复核。")
        others = f"（同批次{'、'.join(other)}，不是本零件）" if other else ""
        return None, f"同批次没有同项目、同物料编号（{mine}）的图纸独立文件{others}，请提交图纸一起审核"

    # ---- clauses -------------------------------------------------------------------------

    @rule("全部工序生产资源")
    def rule_1(self, ctx: RuleContext):
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP生产资源"):
            raise unable("控制计划表头未能定位“机器、装置、夹具、工装”或其他生产资源列，检查程序不能确认各工序的生产资源，需人工复核。")
        operations: dict[tuple[str, int], list[PlanRow]] = {}
        start = 0
        for index, row in enumerate(rows):
            if index == 0 or row.operation != rows[index - 1].operation:
                start = row.row
            if row.operation:
                operations.setdefault((row.operation, start), []).append(row)
        if not operations:
            raise unable("控制计划没有读出写有工序名称的业务行，不能按空范围通过。")

        def written(item: PlanRow) -> bool:
            return compact(item.cells.get("CP生产资源")) not in _PLACEHOLDERS

        # A resource written between two operations with no line drawn between them may be either's.
        between = [item for item in rows if item.candidates and written(item)]
        lacking: list[str] = []
        open_: list[str] = []
        for (name, _start), items in operations.items():
            if not any(written(item) for item in items):
                label = f"“{' '.join(name.split())}”（{items[0].where}起）"
                (open_ if any(name in item.candidates for item in between) else lacking).append(label)
        if lacking:
            # A PDF converts text only: a resource drawn into the cell is not an empty cell.
            if where := self.graphic_in_blank([item for (name, start), items in operations.items() for item in items], "CP生产资源"):
                raise unable(f"控制计划{where}生产资源格内有图形或无法对应原页位置，检查程序未能读取其内容，需人工复核。")
            note = f"；另{'、'.join(open_)}本身各行为空，{between[0].where}的生产资源夹在两个工序之间、原件未画分隔线，归属待人工确认" if open_ else ""
            return "NG", f"控制计划共{len(operations)}个工序，其中{len(lacking)}个工序的生产资源列为空：{'、'.join(lacking[:12])}{'等' if len(lacking) > 12 else ''}{note}。"
        if open_:
            raise unable(f"控制计划{'、'.join(open_)}本身各行的生产资源为空；{between[0].where}写有生产资源，但位于两个工序名称之间且原件没有分隔线，检查程序不能确定它属于哪一工序，需人工复核。")
        return "PASS", f"控制计划全部{len(operations)}个工序都写明了生产资源。"

    @rule("生产追溯及先进先出实际措施")
    def rule_2(self, ctx: RuleContext):
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP规格公差"):
            if any("规格" in text or "规范" in text or "公差" in text for text in self.header_texts(ctx)):
                raise unable("控制计划表头有规格/公差字样，但检查程序未能定位“产品/过程规格/公差”列，需人工复核。")
            return "NG", "控制计划没有“产品/过程规格/公差”列，追溯标识与先进先出要求无处写明。"

        def spec(row: PlanRow) -> str:
            return compact(self.spanned(row, "CP规格公差"))

        problems, open_, unread = [], [], []
        for stage, _words in _STAGES:
            mine = [row for row in rows if row.operation and _trace_stage(row.owners()[0]) == stage]
            # A row drawn between two operations counts only when neither reading could change the outcome.
            shared = [row for row in rows if not row.operation and any(_trace_stage(name) == stage for name in row.owners())]
            if any(word in spec(row) for row in mine for word in _TRACE):
                continue
            if any(word in spec(row) for row in shared for word in _TRACE):
                open_.append(f"{stage}环节的追溯标识只写在{shared[0].where}等行，这些行位于两个工序之间且原件没有分隔线")
            elif not mine and not shared:
                problems.append(f"控制计划中没有识别出{stage}环节的工序")
            else:
                where = f"（{mine[0].where}起）" if mine else ""
                problems.append(f"{stage}环节{where}的“产品/过程规格/公差”列没有写明追溯标识方式")
                unread += mine
        fifo = [row for row in rows if any(word in spec(row) for word in _FIFO)]
        if not fifo:
            limits = [row for row in rows if any(word in spec(row) for word in _STORAGE_LIMIT)]
            if limits:
                problems.append(f"“产品/过程规格/公差”列只写了存放时限（{limits[0].where}“{self.spanned(limits[0], 'CP规格公差')}”），不算先进先出要求")
            else:
                problems.append("“产品/过程规格/公差”列没有写明先进先出要求")
        if problems:
            # A stage whose blank specification cell holds a picture may have written its identifier there.
            if drawn := self.graphic_in_blank(unread, "CP规格公差"):
                raise unable(f"控制计划{drawn}规格/公差格内有图形或无法对应原页位置，检查程序未能读取，需人工复核。")
            return "NG", "；".join(problems) + "。"
        if open_:
            raise unable("；".join(open_) + "，检查程序不能确定其归属，需人工复核。")
        return "PASS", f"控制计划进料、出入库、出货环节的“产品/过程规格/公差”列均写明追溯标识方式，{fifo[0].where}写明先进先出要求。"

    def plan_type(self, ctx: RuleContext) -> tuple[list[str], list[str], str]:
        """(checked options, options whose mark is unreadable, where): a form checkbox or marks written in a cell."""
        path = str(self.audit_file.file_url)
        sheets = self.plan_sheets(ctx)
        if path.lower().endswith((".xlsx", ".xlsm")):
            labels: list[tuple[str, bool]] = []
            for sheet in sheets:
                labels += ctx.ops.get_checkbox_labels(sheet.ws, path) or []
            staged = [(caption, state) for caption, state in labels if _stage(caption)]
            if staged:
                return [caption for caption, state in staged if state], [], f"“{sheets[0].title}”类型复选框"
        for sheet in sheets:
            rows, width = self.extent(sheet)
            for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=width):
                raw = unicodedata.normalize("NFKC", str(cell.value()))
                options = re.findall(rf"([{_MARKS}])\s*([^{_MARKS}]+)", raw)
                if len(options) < 2 or not any(_stage(text) for _mark, text in options):
                    continue
                # Text before the first mark that names a stage is an option whose mark the original does not show.
                lead = raw[: raw.find(options[0][0])]
                unseen = [lead.strip()] if _stage(lead) else []
                checked = [text.strip() for mark, text in options if mark not in _UNCHECKED]
                return checked, unseen, f"“{sheet.title}”{cell.ws.cell(cell.row_index, cell.col_index).coordinate}"
        return [], [], ""

    @rule("量产SOP类型及真实受控章")
    def rule_3(self, ctx: RuleContext):
        checked, unseen, where = self.plan_type(ctx)
        # A PDF is read through converted tables, whose sheet and cell names are not the user's.
        at = "" if str(self.audit_file.file_url).lower().endswith(".pdf") else f"（{where}）"
        if not where:
            raise unable("控制计划未能读出类型选项（样件/试生产/量产）的勾选，尚不能确认是否为量产/SOP控制计划，需人工复核。")
        if len(checked) != 1:
            written = "、".join(" ".join(text.split()) for text in checked) or "无"
            raise unable(f"控制计划类型选项{at}没有唯一的勾选（勾选：{written}），尚不能确认类型，需人工复核。")
        if _stage(checked[0]) != "production":
            return "NG", f"控制计划类型勾选为“{' '.join(checked[0].split())}”{at}，不是量产/SOP。"
        absent = self.pdf_without_stamp()
        status, reason = ("NG", absent) if absent else self.stamp()
        if status == "PASS" and unseen:
            raise unable(f"控制计划类型勾选“{checked[0]}”，但选项“{unseen[0]}”的勾选框在原件中不可读{at}，尚不能确认唯一为量产；{reason}")
        return status, f"控制计划类型勾选“{' '.join(checked[0].split()).split('(')[0]}”{at}；{reason}"

    def pdf_without_stamp(self) -> str | None:
        """For a PDF original, why no page carries a stamp; None when some page could.

        A page could carry one where its text layer says 受控, where it draws or writes in any colour
        other than black and grey, or where one of its pictures is not confirmed to hold no seal.
        Each distinct picture is cut out and described alone: a whole page leaves a small seal unclear.
        """
        path = str(self.audit_file.file_url)
        # A scanned page is not read (scanned PDFs are left to a person).
        if not path.lower().endswith(".pdf") or self.unread_pages():
            return None
        import fitz

        def grey(color: Any) -> bool:
            return color is None or len(set(round(part, 2) for part in color)) <= 1

        seen: dict[Any, Any] = {}
        with fitz.open(path) as document:
            for page in document:
                if "受控" in compact(page.get_text()):
                    return None
                if not all(grey(drawing.get(key)) for drawing in page.get_drawings() for key in ("color", "fill")):
                    return None
                spans = (span for block in page.get_text("dict")["blocks"] for line in block.get("lines", []) for span in line["spans"])
                if not all(grey((span["color"] >> 16 & 255, span["color"] >> 8 & 255, span["color"] & 255)) for span in spans):
                    return None
                for image in page.get_image_info(hashes=True):
                    digest = image.get("digest")
                    if digest not in seen:
                        area = fitz.Rect(image["bbox"]) + (-2, -2, 2, 2)
                        picture = page.get_pixmap(dpi=min(600, max(144, int(600 * 72 / max(area.width, 1)))), clip=area).tobytes("png")
                        seen[digest] = self.transcribe(picture, resources.PICTURE_PROMPT, resources.PICTURE_SCHEMA, f"第{page.number + 1}页上的图片").get("seal")
                        locator = {"kind": "pdf_region", "page": page.number + 1, "bbox": [area.x0, area.y0, area.x1, area.y1], "engine": "ocr_llm", "fact": "controlled_stamp"}
                        self.legacy().witness(locator, "有印章" if seen[digest] else "没有印章" if seen[digest] is False else "看不清", visual=True)
                    if seen[digest] is not False:
                        return None
            count = len(document)
        pictures = f"{len(seen)}种嵌入图片经逐一核对均没有印章" if seen else "没有图片"
        return f"原件共{count}页，各页均没有受控章：页面只有黑白文字和表格线，{pictures}。"

    @rule("特性类型列实际特性标记")
    def rule_4(self, ctx: RuleContext):
        rows = self.plan(ctx)
        if not self.plan_column(ctx, "CP特殊特性"):
            heads = self.header_texts(ctx)
            if any(word in text.replace("/", "") for text in heads for word in ("特殊特性", "特性类型", "特性分类", "特性类别")):
                raise unable("控制计划表头有特殊特性/特性类型字样，但检查程序未能定位该列，需人工复核。")
            return "NG", "控制计划表头没有“特性类型”或“特殊特性”列。"
        marked = [row for row in rows if compact(row.cells.get("CP特殊特性")) not in _PLACEHOLDERS]
        if not marked:
            # A mark drawn as a symbol picture or strokes converts to an empty cell.
            if where := self.graphic_in_blank(rows, "CP特殊特性"):
                raise unable(f"控制计划{where}特性类型格内有图形或无法对应原页位置，检查程序未能读取标记，需人工复核。")
            return "NG", f"控制计划“特性类型/特殊特性”列在全部{len(rows)}个特性行中均没有特性标记。"
        examples = "、".join(f"{row.where}“{row.cells['CP特殊特性']}”" for row in marked[:3])
        return "PASS", f"控制计划“特性类型/特殊特性”列有{len(marked)}行写有特性标记，如{examples}。"

    @rule("跑道尺寸过程能力控制")
    def rule_5(self, ctx: RuleContext):
        rows = self.plan(ctx)

        def filled(row: PlanRow, *names: str) -> bool:
            return any(compact(self.spanned(row, name)) not in _PLACEHOLDERS for name in names)

        def dimension(row: PlanRow) -> bool:
            return _dimension(self.spanned(row, *_CHARACTERISTIC))

        def racetrack(row: PlanRow) -> bool:
            return filled(row, "CP产品特性", "CP过程特性") and filled(row, "CP特殊特性") and filled(row, "CP规格公差") and dimension(row) and any(_manufacturing(name) for name in row.owners())

        racetracks = [row for row in rows if racetrack(row)]
        if not racetracks:
            # The customer's ruling: no race-track dimension found is left to a person, neither PASS nor NG.
            # A special characteristic written only as its specification leaves the characteristic columns blank.
            unnamed = [
                row for row in rows if not filled(row, "CP产品特性", "CP过程特性") and filled(row, "CP特殊特性") and filled(row, "CP规格公差") and any(_manufacturing(name) for name in row.owners())
            ]
            note = f"另有{len(unnamed)}行标有特殊特性、只写了规格/公差而特性列为空（如{unnamed[0].where}），" if unnamed else ""
            return "UNABLE", f"未发现跑道尺寸，请核实：控制计划加工/制造工序中没有特性、特殊特性、规格/公差三列均有内容的尺寸类特性行，{note}需人工确认该零件的跑道尺寸。"
        lacking = [row for row in racetracks if not any(term in compact(self.spanned(row, "评价测量技术", "CP频率", "CP控制方法")) for term in _CAPABILITY)]
        if not lacking:
            return "PASS", f"控制计划加工/制造工序的{len(racetracks)}个跑道尺寸行，评价测量技术/频率/控制方法均写明SPC、控制图或Cpk/Ppk管控。"
        # A row drawn between a manufacturing operation and an inspection may belong to the inspection.
        certain = [row for row in lacking if all(_manufacturing(name) for name in row.owners())]
        if not certain:
            raise unable(f"缺少过程能力管控的尺寸行（{lacking[0].where}等）位于加工/制造工序与检验等工序之间且原件没有分隔线，检查程序不能确定其归属，需人工复核。")
        lacking = certain
        if where := self.graphic_in_blank(lacking, "CP控制方法"):
            raise unable(f"控制计划{where}控制方法格内有图形或无法对应原页位置，检查程序未能读取，需人工复核。")
        listed = "、".join(f"{row.where}“{self.spanned(row, 'CP产品特性', 'CP过程特性').strip()}”" for row in lacking[:8])
        return "NG", f"控制计划加工/制造工序共{len(racetracks)}个跑道尺寸行，其中{len(lacking)}行的评价测量技术/频率/控制方法没有SPC、控制图或Cpk/Ppk管控：{listed}{'等' if len(lacking) > 8 else ''}。"

    @rule("CP身份字段图纸身份及最后更改日期")
    def rule_6(self, ctx: RuleContext):
        self.plan_sheets(ctx)
        failures: list[str] = []
        unknown: list[str] = []
        absent: list[str] = []
        # A spreadsheet's title block is read cell by cell: a label in none of its cells is absent from it.
        # A PDF converts only what its tables hold, so a label it does not show stays unread.
        pdf = str(self.audit_file.file_url).lower().endswith(".pdf")
        for label, aliases in (
            ("供应商名称", resources.SUPPLIER_NAME),
            ("供应商代码", resources.SUPPLIER_CODE),
            ("编制人", resources.PREPARER),
            ("版本号", resources.VERSION),
            ("受控日期/创建日期/修订日期", resources.DATES),
            ("零件名称", resources.PART_NAME),
            ("零件号", resources.PART_NUMBER),
        ):
            found = self.plan_fields(ctx, aliases)
            if not found:
                (unknown if pdf else absent).append(label)
            elif "日期" not in label and all(compact(value) in _PLACEHOLDERS for value, _place in found):
                failures.append(f"{label}（{found[0][1]}）为“{found[0][0] or '空'}”")
        if absent:
            failures.insert(0, f"表头缺少{'、'.join(absent)}字段")
        if unknown:
            unknown = [f"表头未能定位{'、'.join(unknown)}字段"]
        try:
            _drawing, missing = self.drawing(ctx)
            failures.append(missing)
        except GuardStop as stop:
            if stop.decision != Decision.UNABLE:
                raise
            unknown.append(stop.message.removesuffix("，需人工复核。"))
        if failures:
            note = f"；另{'；'.join(unknown)}，需人工复核" if unknown else ""
            return "NG", "控制计划" + "；".join(failures) + note + "。"
        # The drawing date is compared only once a same-part drawing is read; until then the clause is open.
        raise unable("；".join(unknown) + "，需人工复核。")

    def operations(self, ctx: RuleContext) -> list[str]:
        return [_key(name) for name in dict.fromkeys(row.operation for row in self.plan(ctx)) if name]

    def missing_sources(self, ctx: RuleContext) -> tuple[dict[str, Any], list[str]]:
        """The same-part flow chart, PFMEA and PGQP1 plan; a missing one decides before unknowns about the others."""
        wanted = [(_PFD, "过程流程图", flowchart_counterpart), (_PFMEA, "PFMEA", pfmea_counterpart), (_PGQP1, "采购件质量计划（PGQP1）", pgqp1)]
        absent = [label for kind, label, _document in wanted if not self.batch_files(kind)]
        if absent:
            return {}, [f"同批次没有{'、'.join(absent)}独立文件"]
        mine = self.part_number(ctx)
        found, missing, unknown = {}, [], []
        for kind, label, document in wanted:
            try:
                item, why = self.counterpart(ctx, kind, label, document, mine)
            except GuardStop as stop:
                if stop.decision != Decision.UNABLE:
                    raise
                unknown.append(stop)
                continue
            if item is None:
                missing.append(why)
            else:
                found[kind] = item
        if _PGQP1 in found:
            with self.reading(ctx, found[_PGQP1], pgqp1):
                if not Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                    missing.append("同批次采购件质量计划中没有PGQP1表")
        if unknown and not missing:
            raise unknown[0]
        return found, missing

    @rule("PFD PFMEA PGQP1全部工序覆盖")
    def rule_7(self, ctx: RuleContext):
        found, missing = self.missing_sources(ctx)
        if missing:
            return "NG", "；".join(missing) + "，请补充缺项文件后复审。"
        operations = self.operations(ctx)
        gaps = []
        with self.reading(ctx, found[_PFD], flowchart_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08流程图", "过程流程图"):
                for row in self.section(sheet, "U08流程区").rows("U08过程描述"):
                    step = str(row.value("U08过程描述") or "").strip()
                    if step and not any(_key(step) in name for name in operations):
                        gaps.append(f"过程流程图“{' '.join(step.split())}”")
        with self.reading(ctx, found[_PFMEA], pfmea_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08PFMEA", "PFMEA"):
                for row in self.section(sheet, "U08PFMEA区").rows("U08PFMEA工序"):
                    step = str(row.value("U08PFMEA工序") or "").strip()
                    if step and not any(_key(step) in name for name in operations):
                        gaps.append(f"PFMEA“{' '.join(step.split())}”")
        with self.reading(ctx, found[_PGQP1], pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                for row in self.section(sheet, "U08关键工序区").rows("U08PGQP1工艺"):
                    step = str(row.value("U08PGQP1工艺") or "").strip()
                    if step and not any(_key(step) in name for name in operations):
                        gaps.append(f"PGQP1工艺“{step}”")
        if gaps:
            return "NG", f"以下工序未在控制计划工序名称/操作描述列出现：{'、'.join(dict.fromkeys(gaps))}。"
        return "PASS", "过程流程图、PFMEA、PGQP1的全部工序都在控制计划工序名称/操作描述列出现。"

    def counterpart_sheets(self, ctx: RuleContext, name: str, label: str) -> list[Sheet]:
        sheets = Table(ctx).find_sheets(name, on_missing=Decision.IGNORE)
        if not sheets:
            raise unable(f"同批次{label}未能读出表格（未定位到其表头），检查程序未能比对，需人工复核。")
        return sheets

    def characteristics(self, ctx: RuleContext, rows: list[PlanRow] | None = None) -> str:
        """The product, process and specification cells of control plan rows, each comparable, one per line.

        Cells stay apart: a number ending one cell ("平面度 0.05") must not read as the head of the next ("102#").
        """
        rows = self.plan(ctx) if rows is None else rows
        return "\n".join(compact(row.cells.get(name, "")) for row in rows for name in _CHARACTERISTIC)

    @rule("PFD PFMEA PGQP1全部特殊特性覆盖")
    def rule_8(self, ctx: RuleContext):
        found, missing = self.missing_sources(ctx)
        if missing:
            return "NG", "；".join(missing) + "，请补充缺项文件后复审。"
        text = self.characteristics(ctx)
        gaps = []
        with self.reading(ctx, found[_PFD], flowchart_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08流程图", "过程流程图"):
                section = self.section(sheet, "U08流程区")
                if ctx.optional_column(section.resolved, "U08流程特殊特性") is None:
                    raise unable(f"过程流程图“{sheet.title}”没有可读的特性符号列（特性标识可能是图形），检查程序未能确认被标识的特性，需人工复核。")
                for row in section.rows("U08过程描述", "U08流程特殊特性", optional=("U08流程产品特性", "U08流程过程特性")):
                    if compact(row.value("U08流程特殊特性")) in _PLACEHOLDERS:
                        continue
                    for name in ("U08流程产品特性", "U08流程过程特性"):
                        value = str(row.value(name) or "").strip() if name in row.columns else ""
                        if value and not _written(value, text):
                            gaps.append(f"过程流程图第{row.row_index}行“{' '.join(value.split())}”")
        with self.reading(ctx, found[_PFMEA], pfmea_counterpart):
            for sheet in self.counterpart_sheets(ctx, "U08PFMEA", "PFMEA"):
                section = self.section(sheet, "U08PFMEA区")
                column = section.column("U08PFMEA工序")
                for row in section.rows("U08PFMEA分类"):
                    if compact(row.value("U08PFMEA分类")) in _PLACEHOLDERS:
                        continue
                    value = str(sheet.cell(row.row_index, column.header_col).value() or "").strip()
                    if value and not _written(value, text):
                        gaps.append(f"PFMEA第{row.row_index}行“{' '.join(value.split())}”")
        for name, spec, where in self.important(ctx, found[_PGQP1]):
            if not _written(spec, text) and not _written(name, text):
                gaps.append(f"PGQP1{where}“{name} {spec}”")
        if gaps:
            return "NG", f"以下特殊/重要特性未在控制计划产品、过程、产品/过程规格/公差列出现：{'、'.join(gaps[:15])}{'等' if len(gaps) > 15 else ''}。"
        return "PASS", "过程流程图、PFMEA、PGQP1的被标识特性都在控制计划产品、过程、规格/公差列出现。"

    def important(self, ctx: RuleContext, plan: Any) -> list[tuple[str, str, str]]:
        """(item, specification, place) of each PGQP1 important characteristic."""
        items = []
        with self.reading(ctx, plan, pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                for row in self.section(sheet, "重要特性区").rows("特性项目", "特殊特性"):
                    name, spec = str(row.value("特性项目") or "").strip(), str(row.value("特殊特性") or "").strip()
                    # The heading repeated after a page break reads as a row without a specification.
                    if name and spec and any(char.isdigit() for char in spec):
                        items.append((name, spec, f"第{row.row_index}行"))
        return items

    def plan_only(self, ctx: RuleContext) -> tuple[Any | None, str]:
        files = self.batch_files(_PGQP1)
        if not files:
            return None, "同批次没有采购件质量计划（PGQP1）独立文件"
        item, why = self.counterpart(ctx, _PGQP1, "采购件质量计划", pgqp1, self.part_number(ctx))
        if item is None:
            return None, why
        with self.reading(ctx, item, pgqp1):
            if not Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                return None, "同批次采购件质量计划中没有PGQP1表"
        return item, ""

    @rule("PGQP1尺寸类特性出货检验覆盖")
    def rule_9(self, ctx: RuleContext):
        plan, why = self.plan_only(ctx)
        if plan is None:
            return "NG", why + "，请补充后复审。"
        dimensions = [(name, spec, where) for name, spec, where in self.important(ctx, plan) if _dimension(f"{name} {spec}")]
        if not dimensions:
            raise unable("PGQP1重要特性清单中没有读出尺寸类特性，不能按空范围通过，需人工复核。")
        plan_rows = self.plan(ctx)

        def outgoing_(name: str) -> bool:
            return any(word in compact(name) for word in ("出货检验", "outgoinginspection"))

        outgoing = [row for row in plan_rows if outgoing_(row.operation)]
        if not outgoing:
            raise unable("控制计划中未找到出货检验工序，尚不能核对PGQP1尺寸是否在出货检验中，需人工复核。")
        text = self.characteristics(ctx, outgoing)
        # Rows between the outgoing inspection and a neighbour with no line drawn between them may be either's.
        between = [row for row in plan_rows if any(outgoing_(name) for name in row.candidates)]
        maybe = self.characteristics(ctx, between) if between else ""
        missing = [(name, spec, where) for name, spec, where in dimensions if not _written(spec, text) and not _written(name, text)]
        if missing and all(_written(spec, maybe) or _written(name, maybe) for name, spec, _where in missing):
            listed = "、".join(f"“{name} {spec}”" for name, spec, _where in missing[:8])
            raise unable(f"PGQP1尺寸类特性{listed}只出现在{between[0].where}等行，这些行位于出货检验与相邻工序之间且原件没有分隔线，检查程序不能确定其归属，需人工复核。")
        missing = [item for item in missing if not (_written(item[1], maybe) or _written(item[0], maybe))]
        absent = [f"{where}“{name} {spec}”" for name, spec, where in missing]
        differ = [f"{where}“{name} {spec}”" for name, spec, where in dimensions if not _written(spec, text) and _written(name, text)]
        if absent:
            extra = f"；另{len(differ)}项名称出现但规格写法与PGQP1不同：{'、'.join(differ[:5])}" if differ else ""
            listed = "、".join(absent[:15]) + ("等" if len(absent) > 15 else "")
            return "NG", f"PGQP1共{len(dimensions)}项尺寸类特性，其中{len(absent)}项未在控制计划出货检验工序（{outgoing[0].where}起）出现：{listed}{extra}。"
        if differ:
            raise unable(f"PGQP1尺寸类特性名称均在控制计划出货检验工序出现，但{len(differ)}项规格写法与PGQP1不同：{'、'.join(differ[:8])}，需人工复核是否一致。")
        return "PASS", f"PGQP1全部{len(dimensions)}项尺寸类特性都在控制计划出货检验工序出现。"

    def product_audit(self, ctx: RuleContext, plan: Any) -> list[tuple[str, str, str]]:
        """(item, content, place) of each row of the PGQP1 product audit list, found by its title."""
        rows = []
        with self.reading(ctx, plan, pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                height, width = self.extent(sheet)
                cells = sheet.scan_non_empty(row_min=1, row_max=height, col_min=1, col_max=width)
                titles = [cell for cell in cells if any(compact(word) in compact(cell.value()) for word in pgqp1_counterpart.PRODUCT_AUDIT_TITLE)]
                if not titles:
                    continue
                start = titles[0].row_index
                heads = [cell for cell in cells if cell.row_index > start and compact(cell.value()) in {compact(word) for word in pgqp1_counterpart.PRODUCT_AUDIT_ITEM}]
                if not heads:
                    raise unable(f"PGQP1“{sheet.title}”产品审核要求下未能定位“Items/项目”表头，检查程序未能读取，需人工复核。")
                head = heads[0]
                content = next((cell for cell in cells if cell.row_index == head.row_index and compact(cell.value()) in {compact(word) for word in pgqp1_counterpart.PRODUCT_AUDIT_CONTENT}), None)
                for row in range(head.row_index + 1, height + 1):
                    item = str(sheet.cell(row, head.col_index).value() or "").strip()
                    text = str(sheet.cell(row, content.col_index).value() or "").strip() if content is not None else ""
                    if item or text:
                        rows.append((item, text, f"“{sheet.title}”第{row}行"))
        return rows

    @rule("PGQP1产品审核型式试验覆盖")
    def rule_10(self, ctx: RuleContext):
        plan, why = self.plan_only(ctx)
        if plan is None:
            return "NG", why + "，请补充后复审。"
        audit = self.product_audit(ctx, plan)
        if not audit:
            raise unable("PGQP1中未能定位产品审核要求（Product Audit Requirements）表，检查程序未能读取，需人工复核。")
        tests = [(item, text, where) for item, text, where in audit if any(word in compact(item + text) for word in map(compact, pgqp1_counterpart.TYPE_TEST))]
        if not tests:
            listed = "、".join(item or text for item, text, _where in audit)
            return "PASS", f"PGQP1产品审核要求（{listed}）中没有型式试验内容，无需在控制计划中定义。"
        text = self.characteristics(ctx)
        absent = [f"{where}“{item or content}”" for item, content, where in tests if not _written(item or content, text)]
        if absent:
            return "NG", f"PGQP1产品审核要求的型式试验未在控制计划产品、过程、规格/公差列出现：{'、'.join(absent)}。"
        return "PASS", f"PGQP1产品审核要求的{len(tests)}项型式试验都在控制计划中有定义。"

    def drawing_clause(self, ctx: RuleContext) -> tuple[str, str]:
        drawing, missing = self.drawing(ctx)
        if drawing is None:
            return "NG", missing + "。"
        raise unable("同批次图纸的特殊特性标注为图形，检查程序尚不能读取，需人工复核。")

    @rule("图纸CPK特殊尺寸对应SPC控制")
    def rule_11(self, ctx: RuleContext):
        return self.drawing_clause(ctx)

    @rule("图纸全部特殊尺寸和要求覆盖")
    def rule_12(self, ctx: RuleContext):
        return self.drawing_clause(ctx)
