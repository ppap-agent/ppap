"""Content-level data-standardization precheck: one file against the rule entry routed for its customer and type.

The entry comes from PPAP-00-A (L1 = a full per-type spec, L2 = the routing table's ``required_structure``).
Reading is deterministic text and tables only (no OCR, no models) and runs outside every checker flow.
Every criterion of the routed entry gets exactly one result; a criterion this module does not decide is
``未执行`` with the reason, never ``满足`` without the cell that proves it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from typing import Any, Callable

from product.precheck_reading import Doc, Grid, norm, read_document

# (customer, recognized document type) -> 00-A entry key. Types are the 00-A dictionary codes plus the names this
# codebase recognizes (rule body ``type`` and PpapDocumentType names).
ENTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "U01": ("UAES", ("E01", "初始样品检验报告")),
    "U02": ("UAES", ("E02", "测量比较报告")),
    "U03": ("UAES", ("E04", "实验室资质证书")),
    "U04": ("UAES", ("T04", "材料产品及分供清单")),
    "U05": ("UAES", ("T07", "测量系统分析评价")),
    "U06": ("UAES", ("T08", "过程能力研究")),
    "U07": ("UAES", ("T10", "潜在过程失效模式及后果分析")),
    "U08": ("UAES", ("T11", "控制计划")),
    "U09": ("UAES", ("E03", "包装方案")),
    "U10": ("UAES", ("E05", "材料规范")),
    "U11": ("UAES", ("E06", "样品阶段履历")),
    "U12": ("UAES", ("T12", "过程特殊特性清单", "特殊特性清单")),
    "U13": ("UAES", ("E07", "生产件样品照片")),
    "U14": ("UAES", ("E08", "订货规范")),
    "U15": ("UAES", ("E09", "试装单")),
    "U16": ("UAES", ("T09", "过程流程图")),
    "U17": ("UAES", ("T17", "零件提交保证书")),
    "U18": ("UAES", ("E10", "零件追溯说明")),
    "U19": ("UAES", ("E11", "顾客工程批准")),
    "L01": ("理想", ("T01", "全功能检验报告")),
    "L02": ("理想", ("T02", "零部件设计验证计划和报告")),
    "L03": ("理想", ("T03", "DV报告_PV报告_试验报告", "零部件设计验证报告")),
    "L04": ("理想", ("T04", "材料产品及分供清单")),
    "L05": ("理想", ("T07", "测量系统分析评价")),
    "L06": ("理想", ("T08", "过程能力研究")),
    "L07": ("理想", ("T10", "潜在过程失效模式及后果分析")),
    "L08": ("理想", ("T11", "控制计划")),
    "L09": ("理想", ("T05", "检具验收报告")),
    "L10": ("理想", ("T06", "模具验收报告")),
    "L11": ("理想", ("T09", "过程流程图")),
    "L12": ("理想", ("T12", "过程特殊特性清单", "特殊特性清单")),
    "L13": ("理想", ("T13", "设备清单")),
    "L14": ("理想", ("T14", "生产系统产能评估报告")),
    "L15": ("理想", ("T15", "项目问题清单", "APQP项目问题清单")),
    "L16": ("理想", ("T16", "零部件年度试验要求")),
    "L17": ("理想", ("T17", "零件提交保证书")),
    "N01": ("蔚来", ("T01", "全功能检验报告", "测量报告")),
    "N02": ("蔚来", ("T10", "潜在过程失效模式及后果分析")),
    "N03": ("蔚来", ("T11", "控制计划")),
    "N04": ("蔚来", ("T12", "过程特殊特性清单", "特殊特性清单")),
    "N05": ("蔚来", ("T09", "过程流程图")),
}
_ROUTES = {(customer, norm(name)): key for key, (customer, names) in ENTRIES.items() for name in names}

NOT_IMPLEMENTED = "本期未实现此判定项"
NEEDS_IMAGE = "需要图像识别，预检不调用模型"
NEEDS_PEER = "需与同批其他文件比对，内容级预检只读本文件"
NO_TEXT = "文件无可读文字（扫描件或图片），预检不做 OCR"
CHECKED, UNCHECKED = "☑☒■√✓✔▣●◉⊠", "☐□○◯"
PLACEHOLDERS = {"/", "\\", "-", "--", "—", "——", "n/a", "na", "tbd", "待填", "待定", "xx", "xxx", "?"}
ERROR_VALUES = ("#ref!", "#value!", "#div/0!", "#n/a", "#name?", "#num!", "#null!")
FOOTER_WORDS = ("编制", "审核", "批准", "备注", "注:", "说明", "签字", "日期:")

Outcome = tuple[str, str | None, str, str, str]  # result, code, location, evidence, reason


def entry_key(customer: str | None, document_type: str | None) -> str | None:
    """The PPAP-00-A entry for a recognized (customer, document type); None means no rule coverage (S02)."""
    if not customer or not document_type:
        return None
    key = _ROUTES.get((customer, norm(document_type)))
    if key is None:
        from audit.kinds import PpapDocumentType

        kind = PpapDocumentType.from_string(document_type)
        key = None if kind == PpapDocumentType.UNKNOWN else _ROUTES.get((customer, norm(kind.get_type_name())))
    return key


@lru_cache(maxsize=2)
def rule_source(name: str) -> dict[str, Any]:
    """Packaged machine-readable rules extracted from assets/uaes/prechecks (``l1.json`` / ``l2.json``)."""
    return json.loads(files("product").joinpath("precheck_rules", name).read_text(encoding="utf-8"))


def entry_criteria(key: str) -> tuple[str, list[dict[str, Any]]]:
    """(source document, criteria) of one entry: the L1 spec it routes to, else its L2 required_structure."""
    l1 = rule_source("l1.json")
    spec = l1["routing"].get(key)
    if spec is not None:
        return l1["specs"][spec]["source"], [{"criterion_id": item["criterion_id"], "criterion": item["criterion"], "outcome": ""} for item in l1["specs"][spec]["criteria"]]
    l2 = rule_source("l2.json")
    rows = l2["entries"].get(key, [])
    return l2["source"], [{"criterion_id": item["id"], "criterion": item["criterion"], "outcome": item["outcome"]} for item in rows]


# ---------------------------------------------------------------- reading primitives


def blank(value: str) -> bool:
    return norm(value) in PLACEHOLDERS or not norm(value)


def _num(value: str) -> float | None:
    text = norm(value).replace(",", "")
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)(%?)", text)
    if not match:
        return None
    return float(match.group(1)) / (100 if match.group(2) else 1)


def _date(value: str) -> dt.date | None:
    match = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", value)
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None
    except ValueError:
        return None


def _in(alt: str, n: str) -> bool:
    """``=X`` matches only a cell that is exactly X (single-letter headers such as S / O / D)."""
    return n == norm(alt[1:]) if alt.startswith("=") else norm(alt) in n


def _hit(text: str, alts: tuple[str, ...]) -> str | None:
    n = norm(text)
    return next((alt for alt in alts if _in(alt, n)), None)


@dataclass
class Ctx:
    doc: Doc
    filename: str
    customer: str
    labels: tuple[str, ...]  # every field / group / section name of this entry: a cell equal to one is a label, not a value
    groups: tuple[str, ...]
    tables: dict[tuple[tuple[str, ...], ...], Table | None] = field(default_factory=dict)
    cells: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)  # distinct short cell text -> (grid, row, col)

    def short_cells(self) -> dict[str, list[tuple[int, int, int]]]:
        """Index of label-sized cells (merged continuations skipped), so field lookups scan distinct texts, not every cell."""
        if not self.cells:
            for g, grid in enumerate(self.doc.grids):
                for r, row in enumerate(grid.rows):
                    for c, text in enumerate(row):
                        if text and len(norm(text)) <= 40 and (not c or row[c - 1] != text):
                            self.cells.setdefault(text, []).append((g, r, c))
        return self.cells

    def is_label(self, text: str) -> bool:
        n = norm(text)
        names = {norm(label).lstrip("=") for label in self.labels}
        return bool(n) and (n.endswith(":") or n in names or re.sub(r"\([^)]*\)|:", "", n) in names)

    def table(self, specs: list[tuple[str, ...]]) -> Table | None:
        key = tuple(specs)
        if key not in self.tables:
            self.tables[key] = find_table(self.doc, specs)
        return self.tables[key]


@dataclass
class Table:
    grid: Grid
    header: int  # last header row
    cols: dict[int, int]  # spec index -> column
    titles: dict[int, str]

    def rows(self, key: int | None = None) -> list[int]:
        """Data rows below the header; with ``key``, only rows whose key cell has a value."""
        out = []
        for r in range(self.header + 1, len(self.grid.rows)):
            cells = [self.grid.cell(r, c) for c in self.cols.values()]
            if not any(norm(cell) for cell in cells):
                continue
            if key is not None:
                value = self.grid.cell(r, self.cols[key])
                if blank(value) or norm(value) == norm(self.titles[key]) or norm(value).startswith(FOOTER_WORDS):
                    continue
            out.append(r)
        return out


def find_table(doc: Doc, specs: list[tuple[str, ...]]) -> Table | None:
    """The header row (1-4 stacked rows) that names the most of ``specs``; None when no row names any.

    Ties go to exact header names, then fewer stacked rows, then the later row: identity fields above a
    table (``零件号 | Q-1``) name the same words as its column header row below.
    """
    best: tuple[tuple[int, int, int], Table] | None = None
    for grid in doc.grids:
        for r in range(min(len(grid.rows), 300)):
            if not any(norm(cell) for cell in grid.rows[r]):
                continue
            for span in (1, 2, 3, 4):
                if r + span > len(grid.rows):
                    break
                heads = []
                for c in range(max(len(grid.rows[i]) for i in range(r, r + span))):
                    parts: list[str] = []
                    for i in range(r, r + span):
                        part = norm(grid.cell(i, c))
                        if part and part not in parts:
                            parts.append(part)
                    heads.append("".join(parts))
                cols, titles, exact = {}, {}, 0
                for index, alts in enumerate(specs):
                    found = [(head != norm(alt).lstrip("="), len(head), c) for alt in alts for c, head in enumerate(heads) if _in(alt, head)]
                    if found:
                        cols[index], exact = min(found)[2], exact + (not min(found)[0])
                        titles[index] = " ".join(grid.cell(i, cols[index]) for i in range(r, r + span) if grid.cell(i, cols[index])).strip()
                score = (len(cols), exact, -span)
                if cols and (best is None or score >= best[0]):
                    best = (score, Table(grid, r + span - 1, cols, titles))
    return best[1] if best else None


def _after(text: str, alt: str) -> str:
    """Inline value written in the label cell itself: ``零件号：ABC`` -> ``ABC``."""
    n, a = norm(text), norm(alt)
    rest = n[n.find(a) + len(a) :]
    rest = re.sub(r"^\([^)]{0,12}\)", "", rest)
    return rest.lstrip(":/").strip()


@dataclass
class Field:
    found: bool
    value: str = ""
    location: str = ""
    label: str = ""


def find_field(ctx: Ctx, alts: tuple[str, ...]) -> Field:
    """A labelled header/footer field: inline after the label, else the next non-empty cell to the right, else below."""
    hits: list[Field] = []
    positions = sorted((g, r, c, text) for text, places in ctx.short_cells().items() if (alt := _hit(text, alts)) and len(norm(text)) <= len(norm(alt)) + 16 for g, r, c in places)
    for g, r, c, text in positions:
        grid, row, alt = ctx.doc.grids[g], ctx.doc.grids[g].rows[r], _hit(text, alts) or ""
        value, where = _after(text, alt), grid.ref(r, c)
        if value and ctx.is_label(value):
            value = ""
        if not value:
            right = [(cc, row[cc]) for cc in range(c + 1, min(len(row), c + 7)) if row[cc] != text and norm(row[cc])]
            if right and not ctx.is_label(right[0][1]):
                value, where = right[0][1], grid.ref(r, right[0][0])
            elif not right and grid.cell(r + 1, c) and not ctx.is_label(grid.cell(r + 1, c)) and grid.cell(r + 1, c) != text:
                value, where = grid.cell(r + 1, c), grid.ref(r + 1, c)
        hits.append(Field(True, value, where, text))
    for index, (where, text) in enumerate(ctx.doc.lines):
        alt = _hit(text, alts)
        if not alt:
            continue
        value = _after(text, alt)
        if not value and index + 1 < len(ctx.doc.lines) and not ctx.is_label(ctx.doc.lines[index + 1][1]):
            value = ctx.doc.lines[index + 1][1].strip()
        hits.append(Field(True, "" if ctx.is_label(value) else value, where, text))
    return next((hit for hit in hits if not blank(hit.value)), hits[0] if hits else Field(False))


def find_text(doc: Doc, alts: tuple[str, ...], *, top: int | None = None) -> tuple[str, str] | None:
    """(location, text) of the first cell or line containing any alternative; ``top`` limits to the title area."""
    for grid in doc.grids:
        rows = [r for r, row in enumerate(grid.rows) if any(norm(cell) for cell in row)][:top] if top else range(len(grid.rows))
        for r in rows:
            for c, cell in enumerate(grid.rows[r]):
                if _hit(cell, alts):
                    return grid.ref(r, c), cell
    lines = doc.lines[:top] if top else doc.lines
    return next(((where, text) for where, text in lines if _hit(text, alts)), None)


def find_marks(ctx: Ctx, group: tuple[str, ...]) -> tuple[list[tuple[bool, str]], str]:
    """Check boxes written as characters after a group label (☑/√ checked, □ unchecked), up to the next group label."""
    others = tuple(label for label in ctx.groups if label not in group)
    regions: list[tuple[str, str]] = []
    for grid in ctx.doc.grids:
        for r, row in enumerate(grid.rows):
            for c, text in enumerate(row):
                alt = _hit(text, group)
                if not alt or (c and row[c - 1] == text):
                    continue
                parts = [text[text.find(alt) + len(alt) :] if alt in text else text]
                for rr in range(r, min(r + 4, len(grid.rows))):
                    for cc in range(c + 1 if rr == r else c, len(grid.rows[rr])):
                        cell = grid.rows[rr][cc]
                        if not cell or cell == text or (cc and grid.rows[rr][cc - 1] == cell):
                            continue
                        if others and _hit(cell, others):
                            break
                        parts.append(cell)
                    else:
                        continue
                    break
                regions.append((grid.ref(r, c), " ".join(parts)))
    for index, (where, text) in enumerate(ctx.doc.lines):
        alt = _hit(text, group)
        if alt:
            following = []
            for _, line in ctx.doc.lines[index + 1 : index + 4]:
                if others and _hit(line, others):
                    break
                following.append(line)
            regions.append((where, " ".join([text, *following])))
    for where, text in regions:
        marks = [(mark in CHECKED, label.strip()) for mark, label in re.findall(rf"([{CHECKED}{UNCHECKED}])\s*([^{CHECKED}{UNCHECKED}]*)", text)]
        if marks:
            return marks, where
    return [], regions[0][0] if regions else ""


def all_cells(doc: Doc):
    for grid in doc.grids:
        for r, row in enumerate(grid.rows):
            for c, cell in enumerate(row):
                if cell and (not c or row[c - 1] != cell):
                    yield grid, r, c, cell


def _texts(doc: Doc) -> list[tuple[str, str]]:
    return [(grid.ref(r, c), cell) for grid, r, c, cell in all_cells(doc)] + list(doc.lines)


def column_title(grid: Grid, r: int, c: int) -> str:
    """Nearest text above a cell in its column that is not a number or an error value."""
    for rr in range(r - 1, -1, -1):
        text = grid.cell(rr, c)
        if norm(text) and _num(text) is None and not norm(text).startswith("#"):
            return text
    return ""


# ---------------------------------------------------------------- outcomes


def ok(location: str, evidence: str) -> Outcome:
    return "满足", None, location, evidence or "(空)", ""


def bad(code: str, location: str, evidence: str, reason: str = "") -> Outcome:
    return "不满足", code, location, evidence or "(空)", reason


def skip(reason: str, location: str = "", evidence: str = "") -> Outcome:
    return "未执行", None, location, evidence, reason


def _codes(outcome: str) -> tuple[str, str]:
    found = re.findall(r"[BW]0\d", outcome)
    return (found[0], found[-1]) if found else ("", "")


@dataclass
class Check:
    run: Callable[[Ctx, str, str], Outcome]
    labels: tuple[str, ...] = ()
    group: bool = False
    needs_text: bool = True
    needs_tables: bool = False


def _list(items: list[str], limit: int = 5) -> str:
    return "；".join(items[:limit]) + (f" 等 {len(items)} 处" if len(items) > limit else "")


def title(*alts: str, missing: str = "", wrong: str = "") -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        found = find_text(ctx.doc, alts, top=6)
        if found:
            return ok(*found)
        head = find_text(ctx.doc, ("报告", "清单", "表", "单", "书"), top=3)
        return bad((wrong if head else missing) or miss, head[0] if head else "文件首部", head[1] if head else "", f"标题区未见「{'／'.join(alts)}」")

    return Check(run)


def present(*alts: str, code: str = "") -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        found = find_text(ctx.doc, alts)
        return ok(*found) if found else bad(code or miss, "全文", "", f"未找到「{'／'.join(alts)}」")

    return Check(run, labels=alts)


def columns(*specs: tuple[str, ...], code: str = "") -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        table = ctx.table(list(specs))
        if table is None:
            return bad(code or miss, "全文", "", "未找到含这些列的表头：" + "、".join(spec[0] for spec in specs))
        missing = [spec[0] for index, spec in enumerate(specs) if index not in table.cols]
        where = table.grid.ref(table.header)
        if missing:
            return bad(code or miss, where, "已有列：" + "、".join(table.titles.values()), "缺列：" + "、".join(missing))
        return ok(where, "、".join(table.titles[index] for index in range(len(specs))))

    return Check(run, needs_tables=True)


def fields(*specs: tuple[str, ...], missing: str = "", empty: str = "", skip_missing: str = "") -> Check:
    """Each labelled field exists (else ``missing`` / the rule's first code) and has a value (else ``empty`` / its last code)."""

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        found = [(spec[0], find_field(ctx, spec)) for spec in specs]
        absent = [name for name, item in found if not item.found]
        if absent:
            if skip_missing:
                return skip(skip_missing + "：未找到 " + "、".join(absent))
            return bad(missing or miss, "全文", "", "缺字段：" + "、".join(absent))
        empties = [(name, item) for name, item in found if blank(item.value)]
        if empties:
            return bad(empty or last, empties[0][1].location, _list([f"{name}={item.value or '(空)'}" for name, item in empties]), "字段为空或占位：" + "、".join(name for name, _ in empties))
        return ok(found[0][1].location, _list([f"{name}={item.value}" for name, item in found], 8))

    return Check(run, labels=tuple(alt for spec in specs for alt in spec))


def rows_filled(key: tuple[str, ...], *others: tuple[str, ...], code: str = "", any_of: bool = False, structural: bool = False) -> Check:
    """Rows whose ``key`` cell has a value have every (``any_of``: at least one) of ``others`` filled.

    ``structural``: the rule itself codes a missing column (its first code); otherwise a sibling structure item does.
    """

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        specs = [key, *others]
        table = ctx.table(specs)
        if table is None or len(table.cols) < len(specs):
            missing = [spec[0] for index, spec in enumerate(specs) if not table or index not in table.cols]
            if structural:
                return bad(miss, table.grid.ref(table.header) if table else "全文", "", "缺列：" + "、".join(missing))
            return skip("缺少判定所需的列（由结构项判定）：" + "、".join(missing))
        rows = table.rows(0)
        if not rows:
            return skip("「" + key[0] + "」列下没有数据行", table.grid.ref(table.header))
        gaps = []
        for r in rows:
            empty = [table.titles[i] for i in range(1, len(specs)) if blank(table.grid.cell(r, table.cols[i]))]
            if empty and (not any_of or len(empty) == len(others)):
                gaps.append(f"{table.grid.ref(r)}：{'、'.join(empty)}")
        if gaps:
            return bad(code or last, gaps[0].split("：")[0], _list(gaps), f"{len(gaps)} 行有空值")
        return ok(table.grid.ref(rows[0]), f"{len(rows)} 行均已填写")

    return Check(run, needs_tables=True)


def column_values(spec: tuple[str, ...], test: Callable[[str], bool], *, missing: str = "", wrong: str = "", what: str = "", context: tuple[str, ...] = ()) -> Check:
    """Every non-blank value of a column passes ``test`` (else ``wrong``); a missing column is ``missing``."""

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table([spec, *([context] if context else [])])
        if table is None or 0 not in table.cols:
            return bad(missing or miss, "全文", "", f"缺列：{spec[0]}") if (missing or miss) else skip(f"未找到「{spec[0]}」列")
        values = [(r, table.grid.cell(r, table.cols[0])) for r in table.rows() if not blank(table.grid.cell(r, table.cols[0]))]
        if not values:
            return skip(f"「{spec[0]}」列没有可判定的值", table.grid.ref(table.header))
        wrongs = [f"{table.grid.ref(r, table.cols[0])}={value}" for r, value in values if not test(value)]
        if wrongs:
            return bad(wrong or last, wrongs[0].split("=")[0], _list(wrongs), what)
        return ok(table.grid.ref(values[0][0], table.cols[0]), f"{len(values)} 个取值均合规")

    return Check(run, needs_tables=True)


def when(spec: tuple[str, ...], test: Callable[[str], bool], *then: tuple[str, ...], code: str = "", structural: bool = False) -> Check:
    """Rows where ``spec`` passes ``test`` have every ``then`` column filled (``structural`` as in ``rows_filled``)."""

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        specs = [spec, *then]
        table = ctx.table(specs)
        if table is None or len(table.cols) < len(specs):
            missing = [s[0] for index, s in enumerate(specs) if not table or index not in table.cols]
            return bad(miss, "全文", "", "缺列：" + "、".join(missing)) if structural else skip("缺少判定所需的列（由结构项判定）：" + "、".join(missing))
        rows = [r for r in table.rows(0) if test(table.grid.cell(r, table.cols[0]))]
        if not rows:
            return ok(table.grid.ref(table.header), "无触发条件的行")
        gaps = [f"{table.grid.ref(r)}：{'、'.join(table.titles[i] for i in range(1, len(specs)) if blank(table.grid.cell(r, table.cols[i])))}" for r in rows]
        gaps = [gap for gap in gaps if not gap.endswith("：")]
        return bad(code or last, gaps[0].split("：")[0], _list(gaps), "条件行有空值") if gaps else ok(table.grid.ref(rows[0]), f"{len(rows)} 个条件行均已填写")

    return Check(run, needs_tables=True)


def choice(group: tuple[str, ...], *, mode: str = "one", want: str = "", code: str = "W02", missing: str = "") -> Check:
    """A check-box group: ``one`` = exactly one ticked; ``any`` = at least one; ``want`` = the ticked one must say this."""

    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        marks, where = find_marks(ctx, group)
        if not where:
            return bad(missing, "全文", "", f"未找到「{group[0]}」") if missing else skip(f"未找到「{group[0]}」选项组")
        if not marks:
            return skip("未读到勾选框文字（勾选可能为图形控件），本期未判", where)
        ticked = [label for checked, label in marks if checked]
        evidence = " ".join(("☑" if checked else "□") + label for checked, label in marks)
        if not ticked:
            return bad(code, where, evidence, "未勾选")
        if mode == "one" and len(ticked) > 1:
            return bad(code, where, evidence, "多选")
        if want and not any(want in label and "不" + want not in label and "未" + want not in label for label in ticked):
            return "S03", None, where, evidence, f"勾选结果不是「{want}」，结论方向移交正式审核"
        return ok(where, evidence)

    return Check(run, labels=group, group=True)


def controlled() -> Check:
    """Controlled mark: the word 受控 in text satisfies; 作废/无效/非受控 fail; otherwise the stamp needs image reading."""

    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        marks = [(where, text) for where, text in _texts(ctx.doc) if "受控" in norm(text) and len(norm(text)) <= 12]
        negative = [item for item in marks if _hit(item[1], ("非受控", "不受控"))] + [(w, t) for w, t in _texts(ctx.doc) if norm(t) in {"作废", "无效", "作废件"}]
        if negative:
            return bad(miss or "B05", negative[0][0], negative[0][1], "文件标注为非受控 / 作废")
        return ok(*marks[0]) if marks else skip("文字中未见「受控」标识；印章" + NEEDS_IMAGE)

    return Check(run)


def handoff(reason: str) -> Check:
    return Check(lambda ctx, miss, last: ("S03", None, "", "", reason), needs_text=False)


def closed(reason: str) -> Check:
    return Check(lambda ctx, miss, last: ("S02", None, "", "", reason), needs_text=False)


def todo(reason: str) -> Check:
    return Check(lambda ctx, miss, last: skip(reason), needs_text=False)


IMAGE, PEER = todo(NEEDS_IMAGE), todo(NEEDS_PEER)


def custom(run: Callable[[Ctx, str, str], Outcome], needs_text: bool = True) -> Check:
    return Check(run, needs_text=needs_text)


def formula(target: tuple[str, ...], inputs: list[tuple[str, ...]], fn: Callable[[list[float]], float]) -> Check:
    """Columns exist (else the rule's structure code) and ``target == fn(inputs)`` within 1% on every numeric row (else its last code)."""

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        specs = [target, *inputs]
        table = ctx.table(specs)
        if table is None or len(table.cols) < len(specs):
            return bad(miss, table.grid.ref(table.header) if table else "全文", "", "缺列：" + "、".join(spec[0] for i, spec in enumerate(specs) if not table or i not in table.cols))
        checked, wrong = 0, []
        for r in table.rows():
            values = [_num(table.grid.cell(r, table.cols[i])) for i in range(len(specs))]
            if any(value is None for value in values):
                continue
            numbers = [float(value or 0) for value in values]
            try:
                expected = fn(numbers[1:])
            except ZeroDivisionError:
                continue
            checked += 1
            if abs(numbers[0] - expected) > max(abs(expected) * 0.01, 1e-6):
                wrong.append(f"{table.grid.ref(r)}：{table.titles[0]}={table.grid.cell(r, table.cols[0])}，复算={expected:.4g}")
        if wrong:
            return bad(last, wrong[0].split("：")[0], _list(wrong), "复算不平")
        return ok(table.grid.ref(table.header), f"{checked} 行复算一致") if checked else skip("列齐备，但没有可复算的数值行", table.grid.ref(table.header))

    return Check(run, needs_tables=True)


# ---------------------------------------------------------------- L2 required_structure (00-A §2)

PART_NO, PART_NAME = ("零件号", "零件编号", "物料编号"), ("零件名称", "物料名称")
STEP = ("工序名称", "过程名称")
ACTIVITIES = [("制造",), ("运输",), ("存储", "储存"), ("检验",), ("返工",), ("报废",)]


def _vehicle_prefix(word: str) -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        found = find_text(ctx.doc, (word,))
        if not found:
            return skip(f"未找到「{word}」标题")
        prefix = norm(found[1]).split(norm(word))[0]
        return ok(*found) if re.search(r"[a-z0-9]", prefix) else bad(miss, found[0], found[1], "标题前无代号前缀")

    return Check(run)


def _section_content(label: str, *siblings: str) -> Check:
    """A titled region has at least one non-label cell before the next sibling region."""

    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        sequences = [[(grid.ref(r, c), cell) for r, row in enumerate(grid.rows) for c, cell in enumerate(row) if norm(cell) and (not c or row[c - 1] != cell)] for grid in ctx.doc.grids]
        for items in [*sequences, [item for item in ctx.doc.lines if norm(item[1])]]:
            start = next((i for i, (_, text) in enumerate(items) if norm(label) in norm(text)), None)
            if start is None:
                continue
            for where, text in items[start + 1 :]:
                if _hit(text, siblings):
                    break
                if norm(text) != norm(items[start][1]) and not ctx.is_label(text):
                    return ok(where, text)
            return bad(miss, items[start][0], items[start][1], "区域无内容")
        return bad(miss, "全文", "", f"未找到「{label}」区域")

    return Check(run, labels=(label,))


def _image_file() -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        suffix = ctx.filename.rsplit(".", 1)[-1].lower() if "." in ctx.filename else ""
        return ok("文件", ctx.filename) if ctx.doc.kind == "image" else bad("B03", "文件", ctx.filename, f"提交格式为 .{suffix}，不是图片")

    return Check(run, needs_text=False)


def _first_last(words: tuple[str, ...], *, last: bool) -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        table = ctx.table([STEP])
        if table is None:
            return skip("未找到「工序名称」列")
        rows = table.rows(0)
        if not rows:
            return skip("「工序名称」列没有数据")
        pick = rows[-3:] if last else rows[:3]
        values = [table.grid.cell(r, table.cols[0]) for r in pick]
        hit = next((r for r in pick if _hit(table.grid.cell(r, table.cols[0]), words)), None)
        return (
            ok(table.grid.ref(hit, table.cols[0]), table.grid.cell(hit, table.cols[0]))
            if hit is not None
            else bad(miss, table.grid.ref(pick[0], table.cols[0]), "、".join(values), "首末工序未见「" + "／".join(words) + "」")
        )

    return Check(run, needs_tables=True)


def _rework_then_inspect() -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        table = ctx.table([STEP])
        if table is None:
            return skip("未找到「工序名称」列")
        rows = table.rows(0)
        names = [table.grid.cell(r, table.cols[0]) for r in rows]
        wrong = [
            f"{table.grid.ref(rows[i], table.cols[0])}={names[i]} 后为「{names[i + 1] if i + 1 < len(names) else '(无)'}」"
            for i, name in enumerate(names)
            if _hit(name, ("返工", "返修")) and not (i + 1 < len(names) and _hit(names[i + 1], ("检验", "重检")))
        ]
        if wrong:
            return bad(miss, wrong[0].split("=")[0], _list(wrong), "返工 / 返修后未接检验")
        return ok(table.grid.ref(table.header), "返工 / 返修后均接检验") if any(_hit(n, ("返工", "返修")) for n in names) else ok(table.grid.ref(table.header), "无返工 / 返修工序")

    return Check(run, needs_tables=True)


def _two_columns(word: str) -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        best: list[str] = []
        where = "全文"
        for grid in ctx.doc.grids:
            for r in range(min(len(grid.rows), 300)):
                cols = [grid.ref(r, c) for c, cell in enumerate(grid.rows[r]) if norm(word) in norm(cell) and (not c or grid.rows[r][c - 1] != cell)]
                if len(cols) > len(best):
                    best, where = cols, grid.ref(r)
        return ok(where, "、".join(best)) if len(best) >= 2 else bad(miss, where, "、".join(best), f"「{word}」列不足两列")

    return Check(run, needs_tables=True)


def _due_dates() -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        table = ctx.table([("时间节点",), ("问题描述",)])
        if table is None or len(table.cols) < 2:
            return skip("未找到「时间节点」「问题描述」列")
        dates = [(r, d) for r in table.rows(1) if (d := _date(table.grid.cell(r, table.cols[0])))]
        if not dates:
            return skip("「时间节点」没有可解析的日期")
        late = [f"{table.grid.ref(r, table.cols[0])}={d.isoformat()}" for r, d in dates if d < dt.date.today()]
        if late:
            return "S03", None, late[0].split("=")[0], _list(late), "时间节点早于当前日期，逾期属整改有效性，移交正式审核"
        return ok(table.grid.ref(dates[0][0], table.cols[0]), f"{len(dates)} 个时间节点均晚于当前日期")

    return Check(run, needs_tables=True)


def _test_frequency(biennial: bool) -> Check:
    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        table = ctx.table([("试验项目",), ("测试频率", "试验频率")])
        if table is None or len(table.cols) < 2:
            return skip("未找到「试验项目」「测试频率」列")
        special = ("elv", "禁限用", "emc")
        rows = [r for r in table.rows(0) if bool(_hit(table.grid.cell(r, table.cols[0]), special)) == biennial]
        allowed = ("每两年",) if biennial else ("每年", "每半年")
        wrong = [f"{table.grid.ref(r, table.cols[1])}={table.grid.cell(r, table.cols[1]) or '(空)'}" for r in rows if norm(table.grid.cell(r, table.cols[1])) not in {norm(a) for a in allowed}]
        if wrong:
            return bad(miss, wrong[0].split("=")[0], _list(wrong), "测试频率应为" + "／".join(allowed))
        return ok(table.grid.ref(table.header), f"{len(rows)} 行测试频率合规") if rows else ok(table.grid.ref(table.header), "无适用的试验项目行")

    return Check(run, needs_tables=True)


def _risk_conclusion() -> Check:
    risks = [("SOP+1",), ("SOP+2",), ("SOP+3",)]

    def run(ctx: Ctx, miss: str, _: str) -> Outcome:
        specs = [*risks, ("产能评估总体结论", "总体结论"), ("后续措施",)]
        table = ctx.table(specs)
        if table is None or len(table.cols) < len(specs):
            return skip("缺少判定所需的列（由 S4 判定）")
        wrong = []
        for r in table.rows():
            if any(_hit(table.grid.cell(r, table.cols[i]), ("中风险", "高风险")) for i in range(3)):
                conclusion, action = table.grid.cell(r, table.cols[3]), table.grid.cell(r, table.cols[4])
                if not _hit(conclusion, ("条件通过", "未通过")) or blank(action):
                    wrong.append(f"{table.grid.ref(r)}：结论={conclusion or '(空)'}，后续措施={action or '(空)'}")
        return bad(miss, wrong[0].split("：")[0], _list(wrong), "存在中/高风险但结论或措施不符") if wrong else ok(table.grid.ref(table.header), "中/高风险行的结论与措施齐备")

    return Check(run, needs_tables=True)


def _photo_column() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table([("实物照片",)])
        if table is None:
            return bad(miss, "全文", "", "缺列：实物照片")
        if not ctx.doc.images_known:
            return skip("该格式不能枚举嵌入图片；" + NEEDS_IMAGE, table.grid.ref(table.header, table.cols[0]))
        column = table.cols[0]
        anchored = [cell for cell in table.grid.images if cell[1] == column and cell[0] > table.header]
        if anchored:
            return ok(table.grid.ref(anchored[0][0], column), f"该列有 {len(anchored)} 张嵌入图片")
        return bad(last, table.grid.ref(table.header, column), "(无嵌入图片)", "实物照片列无图片")

    return Check(run, needs_tables=True)


SIZE = re.compile(r"\d+(\.\d+)?\s*[×xX*]\s*\d+(\.\d+)?\s*[×xX*]\s*\d+(\.\d+)?(mm)?")
YES_NO_RISK = [("整车",), ("SOP+3",), ("安全",), ("法律法规",)]

L2: dict[str, Check] = {
    "U09:S1": title("包装方案"),
    "U09:S2": present("零件基础信息"),
    "U09:S3": present("供应商基础信息"),
    "U09:S4": present("包装设计的基本要求"),
    "U09:S5": present("针对该零件包装的特殊要求"),
    "U09:S6": present("包装方案信息"),
    "U09:S7": present("包装材料清单"),
    "U10:S1": controlled(),
    "U11:S1": present("工程更改一览表"),
    "U11:S2": columns(("项目",)),
    "U11:S3": columns(("零件号", "零件编号", "物料编号")),
    "U11:S4": columns(("变更内容简述", "变更内容", "更改内容")),
    "U11:S5": columns(("变更原因", "更改原因")),
    "U11:S6": columns(("时间", "日期")),
    "U12:S1": fields(("物料名称",)),
    "U12:S2": fields(("物料编号",)),
    "U12:S3": columns(("编号", "序号")),
    "U12:S4": columns(("产品特性", "过程特性", "特殊特性名称")),
    "U12:S5": columns(("特性及公差", "特性值")),
    "U12:S6": columns(("特性类型", "特性类别")),
    "U12:S7": rows_filled(("编号", "序号"), ("产品特性", "过程特性", "特殊特性名称"), ("特性及公差", "特性值"), ("特性类型", "特性类别")),
    "U13:S1": _image_file(),
    "U13:S2": handoff("照片是否符合要求需人工确认，移交正式审核"),
    "U14:S1": controlled(),
    "U15:S1": present("流转记录"),
    "U15:S2": present("试装结论"),
    "U15:S3": present("检验结论"),
    "U15:S4": custom(
        lambda ctx, miss, last: ok(*found) if (found := find_text(ctx.doc, ("流程结束",))) and find_text(ctx.doc, ("流程完成",)) else bad(miss, "全文", "", "流转记录未同时含「流程结束」「流程完成」")
    ),
    "U15:S5": choice(("试装结论",), want="通过"),
    "U15:S6": choice(("检验结论",), want="合格"),
    "U16:S1": custom(lambda ctx, miss, last: ok(*found) if (found := find_text(ctx.doc, ("★", "◆"))) else skip("文字中未见 ★ / ◆；K 标记、加粗、底色、虚框" + NEEDS_IMAGE)),
    "U16:S2": custom(lambda ctx, miss, last: ok(*found) if (found := _symbol(ctx.doc)) else skip("文字中未见 CC / SC / YC / YS / ★；图形标注" + NEEDS_IMAGE)),
    "U16:S3": custom(lambda ctx, miss, last: ok(*found) if (found := find_text(ctx.doc, ("图例", "注释", "表示"))) else skip("文字中未见图例；图形图例" + NEEDS_IMAGE)),
    "U16:S4": IMAGE,
    "U16:S5": IMAGE,
    "U16:S6": IMAGE,
    "U16:S7": IMAGE,
    "U16:S8": controlled(),
    "U17:S1": fields(("零件名称",), ("客户零件号",), ("图纸号", "图号"), ("供应商零件号", "供方零件号"), ("工程更改级别",), ("日期",), ("其他工程更改", "其它工程更改")),
    "U17:S2": choice(("安全及政府条例", "安全和政府法规", "安全及政府法规")),
    "U17:S3": custom(
        lambda ctx, miss, last: ok(*found)
        if (found := find_text(ctx.doc, ("供方制造厂资料",))) and find_text(ctx.doc, ("买方资料",))
        else bad(miss, "全文", "", "缺「供方制造厂资料」或「买方资料」区域")
    ),
    "U17:S4": fields(("公司名称",)),
    "U17:S5": fields(("采购方及部门",), ("采购方代码",), ("应用",)),
    "U17:S6": choice(("是否报告了受客户关注的材料信息", "受客户关注的材料"), code="W02"),
    "U17:S7": fields(("以IMDS或者其它客户格式递交", "IMDS")),
    "U17:S8": custom(lambda ctx, miss, last: skip("「供方代表签字」栏存在；手写签与印章" + NEEDS_IMAGE) if find_text(ctx.doc, ("供方代表签字",)) else bad(miss, "全文", "", "缺「供方代表签字」字段")),
    "U17:S9": choice(("提交原因",), mode="any"),
    "U17:S10": choice(("要求提交的等级", "提交等级")),
    "U18:S1": present("供应商零件追溯"),
    "U18:S2": _section_content("基本信息", "分供方追溯", "生产过程追溯", "交付追溯", "追溯状态总结"),
    "U18:S3": _section_content("分供方追溯", "基本信息", "生产过程追溯", "交付追溯", "追溯状态总结"),
    "U18:S4": _section_content("生产过程追溯", "基本信息", "分供方追溯", "交付追溯", "追溯状态总结"),
    "U18:S5": _section_content("交付追溯", "基本信息", "分供方追溯", "生产过程追溯", "追溯状态总结"),
    "U18:S6": _section_content("追溯状态总结"),
    "U19:S1": columns(("否决", "reject")),
    "U19:S2": fields(("批准的前提", "前提/备注")),
    "U19:S3": todo("签名" + NEEDS_IMAGE),
    "U19:S4": todo(NOT_IMPLEMENTED),
    "U19:S5": PEER,
    "U19:S6": PEER,
    "U19:S7": PEER,
    "U19:S8": todo(NOT_IMPLEMENTED),
    "L09:S1": present("检具清单"),
    "L09:S2": present("检具认可报告"),
    "L09:S3": todo(NOT_IMPLEMENTED),
    "L09:S4": todo(NOT_IMPLEMENTED),
    "L09:S5": _vehicle_prefix("检具清单"),
    "L09:S6": todo(NOT_IMPLEMENTED),
    "L10:S1": fields(("零件供应商名称",)),
    "L10:S2": fields(("提交日期",)),
    "L10:S3": columns(("工艺类型",)),
    "L10:S4": columns(("一级分总成零件名称",)),
    "L10:S5": columns(("分总成组件或分序模具名称",)),
    "L10:S6": columns(("模具制造供应商",)),
    "L10:S7": columns(("模具存放地点",)),
    "L10:S8": columns(("零件质量评估",)),
    "L10:S9": columns(("模具分供方及实物VS数据&标准验收", "数据&标准验收")),
    "L10:S10": columns(("模具量产性能评估",)),
    "L10:S11": columns(("模具维保评估",)),
    "L10:S12": column_values(("模具设计尺寸",), lambda v: bool(SIZE.fullmatch(norm(v))), what="不是 长×宽×高 格式"),
    "L10:S13": _photo_column(),
    "L10:S14": fields(("编制",), ("审核",)),
    "L10:S15": column_values(("实物确认",), lambda v: norm(v) in {"有", "无"}, what="取值不在 {有, 无}"),
    "L11:S1": fields(PART_NO, PART_NAME, ("项目车型", "车型"), ("供应商名称",), ("代码", "供应商代码"), ("编制日期",), ("版本",), ("团队",)),
    "L11:S2": _two_columns("特性类别"),
    "L11:S3": todo(NOT_IMPLEMENTED),
    "L11:S4": todo(NOT_IMPLEMENTED),
    "L11:S5": rows_filled(STEP, ("操作描述",), structural=True),
    "L11:S6": columns(STEP, *ACTIVITIES),
    "L11:S7": rows_filled(STEP, *ACTIVITIES, any_of=True),
    "L11:S8": IMAGE,
    "L12:S1": present("CCSC特性管理清单"),
    "L12:S2": fields(PART_NAME, PART_NO, ("供应商名称",), ("供应商编码", "供应商代码")),
    "L12:S3": columns(
        ("过程/流程步骤", "流程步骤"),
        ("部件",),
        ("客户要求/功能要求", "功能要求"),
        ("特性名称",),
        ("检验岗位",),
        ("产品/过程特性分类", "特性分类"),
        ("名义值", "规格中心值"),
        ("规格下限",),
        ("规格上限",),
        ("T1-特性监控方法", "特性监控方法"),
    ),
    "L12:S4": columns(("测量设备",), ("测量方式",), ("防错控制",), ("检查控制",), ("抽样频率",), ("SPC监控",), ("SPC线上管理计划时间",), ("说明",)),
    "L12:S5": rows_filled(
        ("过程/流程步骤", "流程步骤"),
        ("部件",),
        ("客户要求/功能要求", "功能要求"),
        ("特性名称",),
        ("检验岗位",),
        ("产品/过程特性分类", "特性分类"),
        ("名义值", "规格中心值"),
        ("规格下限",),
        ("规格上限",),
        ("测量设备",),
        ("测量方式",),
        ("防错控制",),
        ("检查控制",),
        ("抽样频率",),
        ("SPC监控",),
        ("SPC线上管理计划时间",),
        ("说明",),
    ),
    "L12:S6": columns(("现有管理方式风险评估",), ("高风险管理项预防措施",)),
    "L12:S7": when(("现有管理方式风险评估",), lambda v: "高风险" in norm(v), ("高风险管理项预防措施",)),
    "L13:S1": present("设备开发/验收清单", "设备开发验收清单"),
    "L13:S2": columns(("产线/工序", "产线"), ("设备开发",)),
    "L13:S3": columns(("设备开发",), ("设备",), ("供应商",), ("设备型号",), ("利旧设备数量",), ("新采购设备数量",), ("设备通用情况",), ("计划到位时间",)),
    "L13:S4": rows_filled(("产线/工序", "产线"), ("设备型号",), ("供应商",), ("利旧设备数量",), ("新采购设备数量",), ("设备通用情况",), ("计划到位时间",)),
    "L13:S5": columns(("设备验收",), ("验收结果",), ("整改措施",), ("整改计划",)),
    "L13:S6": rows_filled(("产线/工序", "产线"), ("验收结果",)),
    "L13:S7": when(("验收结果",), lambda v: norm(v) in {"不通过", "条件通过"}, ("整改措施",), ("整改计划",)),
    "L13:S8": fields(("车型",)),
    "L13:S9": fields(PART_NAME, PART_NO),
    "L13:S10": fields(("供应商",)),
    "L14:S1": present("零件产能评估表"),
    "L14:S2": fields(PART_NAME, PART_NO, ("供应商名称",), ("供应商编码", "供应商代码"), ("产能评估总体结论",)),
    "L14:S3": todo(NOT_IMPLEMENTED),
    "L14:S4": columns(("SOP+1",), ("SOP+2",), ("SOP+3",), ("产能评估总体结论", "总体结论"), ("后续措施",)),
    "L14:S5": _risk_conclusion(),
    "L14:S6": formula(("良率",), [("合格品数",), ("产出数量",)], lambda v: v[0] / v[1]),
    "L14:S7": formula(("JPH",), [("产出数量",), ("净利用时间",), ("参与测试线体数量",)], lambda v: v[0] / v[1] / v[2]),
    "L14:S8": formula(
        ("预估月度产能",), [("JPH",), ("现有线体数",), ("班次/天", "班次"), ("每班净利用时间",), ("每月标准工作天数",), ("累积良率",)], lambda v: v[0] * v[1] * v[2] * v[3] * v[4] * v[5]
    ),
    "L15:S1": fields(("车型",), ("供应商名称",), ("零件编号", "零件号"), ("零件名称",), missing="B05"),
    "L15:S2": todo(NOT_IMPLEMENTED),
    "L15:S3": rows_filled(("问题描述",), ("对应措施",), ("时间节点",), ("负责人",)),
    "L15:S4": todo(NOT_IMPLEMENTED),
    "L15:S5": _due_dates(),
    "L16:S1": custom(lambda ctx, miss, last: _field_in(ctx, ("质量等级",), {"关键件", "重要件", "一般件"}, miss, last)),
    "L16:S2": _test_frequency(True),
    "L16:S3": _test_frequency(False),
    "L16:S4": custom(lambda ctx, miss, last: _field_match(ctx, ("供应商名称",), r"[a-z0-9]+-.+", "W02", "W03", "应为「供应商代码-供应商全称」")),
    "L16:S5": column_values(("样本数量",), lambda v: bool(re.search(r"\d", v)), what="不含阿拉伯数字"),
    "L16:S6": rows_filled(("试验项目",), ("试验要求",), ("试验标准/方法", "试验标准", "试验方法"), structural=True),
    "L16:S7": fields(("车型",)),
    "L17:S1": todo(NOT_IMPLEMENTED),
    "L17:S2": custom(lambda ctx, miss, last: _field_match(ctx, ("重量",), r"\d+\.\d{4}(kg)?", miss, last, "应为保留小数点后四位的数值")),
    "L17:S3": IMAGE,
    "L17:S4": IMAGE,
    "L17:S5": custom(lambda ctx, miss, last: _field_match(ctx, ("供应商名称/供应商代码", "供应商名称/代码"), r".+/.+", "W03", "W03", "应为「总公司名称/代码」")),
    "L17:S6": IMAGE,
    "L17:S7": fields(
        ("零件名称",),
        ("理想汽车零件号", "零件号"),
        ("供方料号",),
        ("图纸号", "图号"),
        ("模具穴号",),
        ("工程更改版本",),
        ("版本日期",),
        ("重量",),
        ("供应商名称/供应商代码", "供应商名称"),
        ("街道地址",),
        ("城市",),
        ("省",),
        ("国家",),
        ("顾客名称",),
        ("设计开发",),
        ("采购工程",),
        ("PTC",),
    ),
    "L17:S8": choice(("提交原因",), mode="any"),
    "L17:S9": choice(("认可情况",), missing="B05"),
    "L17:S10": todo(NOT_IMPLEMENTED),
    "L17:S11": todo(NOT_IMPLEMENTED),
    "L17:S12": todo(NOT_IMPLEMENTED),
    "N04:S1": _vehicle_prefix("特殊特性清单"),
    "N04:S2": columns(
        ("零件编号", "零件号"), ("零件名称",), ("特性分类",), ("特性描述",), ("特性值",), ("特性类别",), ("特殊特性种类",), ("控制方法",), ("防错方案描述", "防错方案"), ("频次",), ("样本数",)
    ),
    "N04:S3": rows_filled(("特性描述",), ("零件编号", "零件号"), ("零件名称",)),
    "N04:S4": rows_filled(("零件编号", "零件号"), ("特性分类",), ("特性描述",), ("特性值",), ("特性类别",), ("特殊特性种类",), ("控制方法",), ("防错方案描述", "防错方案"), ("频次",), ("样本数",)),
    "N05:S1": present("过程流程图"),
    "N05:S2": fields(PART_NO, PART_NAME, ("项目车型", "车型"), ("供应商名称",), ("代码", "供应商代码"), ("编制日期",), ("修订日期",), ("版本",), ("团队",)),
    "N05:S3": when(("产品特性类别",), lambda v: not blank(v), ("产品特殊特性",), structural=True),
    "N05:S4": when(("过程特性类别",), lambda v: not blank(v), ("过程特殊特性",), structural=True),
    "N05:S5": rows_filled(STEP, ("操作描述",), structural=True),
    "N05:S6": columns(STEP, *ACTIVITIES),
    "N05:S7": rows_filled(STEP, *ACTIVITIES, any_of=True),
    "N05:S8": _first_last(("进料检验", "iqc"), last=False),
    "N05:S9": _first_last(("出货", "fqc", "oqc"), last=True),
    "N05:S10": _rework_then_inspect(),
    "N05:S11": PEER,
}


def _symbol(doc: Doc) -> tuple[str, str] | None:
    for grid, r, c, cell in all_cells(doc):
        if re.search(r"(?<![a-z])(cc|sc|yc|ys)(?![a-z])|★", norm(cell)):
            return grid.ref(r, c), cell
    return None


def _field_in(ctx: Ctx, alts: tuple[str, ...], allowed: set[str], missing: str, wrong: str) -> Outcome:
    item = find_field(ctx, alts)
    if not item.found:
        return bad(missing, "全文", "", f"缺字段：{alts[0]}")
    if norm(item.value) in {norm(a) for a in allowed}:
        return ok(item.location, item.value)
    marks = re.findall(rf"([{CHECKED}])\s*({'|'.join(allowed)})", item.label + " " + item.value)
    if len(marks) == 1:
        return ok(item.location, marks[0][1])
    return bad(wrong, item.location, item.value, "取值不在 {" + "、".join(sorted(allowed)) + "}")


def _field_match(ctx: Ctx, alts: tuple[str, ...], pattern: str, missing: str, wrong: str, what: str) -> Outcome:
    item = find_field(ctx, alts)
    if not item.found:
        return bad(missing, "全文", "", f"缺字段：{alts[0]}")
    if blank(item.value):
        return bad("W02", item.location, item.value, "字段为空或占位")
    return ok(item.location, item.value) if re.fullmatch(pattern, norm(item.value)) else bad(wrong, item.location, item.value, what)


# ---------------------------------------------------------------- L1 spec criteria (implemented subset)


def _scan(doc: Doc, test: Callable[[str], bool]) -> list[tuple[Grid, int, int, str]]:
    return [(grid, r, c, cell) for grid, r, c, cell in all_cells(doc) if test(cell)]


def _errors(doc: Doc) -> list[tuple[Grid, int, int, str]]:
    return _scan(doc, lambda cell: norm(cell).startswith(ERROR_VALUES) or bool(re.fullmatch(r"#{3,}", norm(cell))))


def _found(hits: list[tuple[Grid, int, int, str]]) -> tuple[str, str]:
    return hits[0][0].ref(hits[0][1], hits[0][2]), _list([f"{grid.ref(r, c)}={cell}" for grid, r, c, cell in hits])


def _formula_errors(derived: bool) -> Check:
    words = ("centering", "range", "差异率", "公差带", "均值", "极差")

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        hits = [hit for hit in _scan(ctx.doc, lambda cell: norm(cell).startswith(ERROR_VALUES)) if bool(_hit(column_title(hit[0], hit[1], hit[2]), words)) == derived]
        return bad("W05" if derived else "B05", *_found(hits), "派生列出现错误值" if derived else "被审字段出现公式错误值") if hits else ok("全文", "未发现公式错误值")

    return Check(run)


def _value_scan(code: str, values: set[str], what: str, *, pattern: str = "") -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        hits = _scan(ctx.doc, lambda cell: norm(cell) in values or bool(pattern and re.fullmatch(pattern, norm(cell))))
        return bad(code, *_found(hits), what) if hits else ok("全文", f"未发现{what}")

    return Check(run)


def _capability_residue() -> Check:
    """07 PRE-09: error values (a, B05), ##### overflow (b, W02), template placeholders (c, B05), non-standard NA (e, W03)."""

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        groups = [
            ("B05", "错误值", _scan(ctx.doc, lambda cell: norm(cell).startswith(ERROR_VALUES))),
            ("B05", "模板占位符", _scan(ctx.doc, lambda cell: norm(cell) in {"xx", "--", "待填", "tbd", "?", "*"})),
            ("W02", "显示溢出 #####", _scan(ctx.doc, lambda cell: bool(re.fullmatch(r"#{3,}", norm(cell))))),
            ("W03", "不规范的不适用写法（应写 NA）", _scan(ctx.doc, lambda cell: norm(cell) == "n/a")),
        ]
        for code, what, hits in groups:
            if hits:
                return bad(code, *_found(hits), what)
        return ok("全文", "未发现错误值、占位符或不规范的 NA")

    return Check(run)


def _pfmea_tables() -> Check:
    keys = [
        ("严重度", "severity"),
        ("发生度", "频度", "occurrence"),
        ("探测度", "detection"),
        ("失效模式", "failuremode"),
        ("预防控制", "preventivecontrol", "预防措施"),
        ("探测控制", "detectioncontrol", "探测措施"),
    ]

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table(keys)
        if table is None or len(table.cols) < 5:
            return bad("B05", "全文", "、".join(table.titles.values()) if table else "", "未找到 PFMEA 主表（表头含 ≥5 个关键语义列）")
        if ctx.customer == "UAES":
            return ok(table.grid.ref(table.header), "主表：" + "、".join(table.titles.values()) + "（UAES 变更履历可选）")
        for grid in ctx.doc.grids:
            if _hit(grid.name, ("履历", "变更", "修订", "历史", "版本")):
                filled = [r for r, row in enumerate(grid.rows) if sum(bool(norm(cell)) for cell in row) >= 2]
                if len(filled) >= 3:
                    return ok(grid.ref(filled[0]), f"主表 {table.grid.ref(table.header)}；履历表 {grid.name}")
        history = ctx.table([("版本",), ("日期",), ("变更内容", "更改内容", "修订内容")])
        if history and len(history.cols) == 3 and history.grid is not table.grid and len(history.rows()) >= 2:
            return ok(history.grid.ref(history.header), f"主表 {table.grid.ref(table.header)}；履历 {history.grid.ref(history.header)}")
        return bad("B05", table.grid.ref(table.header), "", "未找到变更履历表（≥2 行数据）")

    return Check(run, needs_tables=True)


def _pfmea_header() -> Check:
    specs = [("公司名称",), ("车型项目", "项目名称", "车型"), PART_NAME, PART_NO, ("制造地址", "工厂"), ("开始日期",), ("修订日期",), ("跨职能团队", "核心小组", "团队")]

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        found = [(spec[0], find_field(ctx, spec)) for spec in specs]
        absent = [name for name, item in found if not item.found]
        empty = [name for name, item in found if item.found and blank(item.value)]
        if len(absent) >= 3:
            return bad("B05", "表头区", "缺：" + "、".join(absent), "表头 8 要素缺 ≥3 项")
        if absent:
            return bad("W01", "表头区", "缺：" + "、".join(absent), "表头 8 要素缺 1~2 项")
        if empty:
            return bad("W02", next(item.location for name, item in found if name == empty[0]), "空：" + "、".join(empty), "表头要素为空")
        return ok(found[0][1].location, _list([f"{name}={item.value}" for name, item in found], 8))

    return Check(run, labels=tuple(alt for spec in specs for alt in spec))


SC_WHITELIST = {"g", "z", "na", "n/a", "cc", "sc", "yc", "ys", "kpc", "kcc", "★", "◆", "▼", "▽", "☆", "[g]", "[z]", "【g】", "【z】", "[cc]", "[sc]"}


def _sod() -> Check:
    names = [("严重度", "severity", "=s"), ("发生度", "频度", "occurrence", "=o"), ("探测度", "detection", "=d")]

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table(names)
        if table is None or len(table.cols) < 3:
            return bad("B05", "全文", "", "缺严重度 / 发生度 / 探测度列")
        # A sentence across the S/O/D columns is a merged note or sign-off row below the table, not a rating.
        note = lambda r: any(len(norm(table.grid.cell(r, table.cols[i]))) > 8 and _num(table.grid.cell(r, table.cols[i])) is None for i in range(3))  # noqa: E731
        rows = [r for r in table.rows() if any(norm(table.grid.cell(r, table.cols[i])) for i in range(3)) and not note(r)]
        # Sign-off rows (names, dates) follow the last rated row; the table ends there.
        rated = [r for r in rows if any(_num(table.grid.cell(r, table.cols[i])) is not None for i in range(3))]
        rows = [r for r in rows if rated and r <= rated[-1]]
        empties, wrong = [], []
        for r in rows:
            for i in range(3):
                cell = table.grid.cell(r, table.cols[i])
                value = _num(cell)
                if not norm(cell):
                    empties.append(f"{table.grid.ref(r, table.cols[i])}=(空)")
                elif value is None or value != int(value) or not 1 <= value <= 10:
                    wrong.append(f"{table.grid.ref(r, table.cols[i])}={cell}")
        if not rows:
            return skip("S/O/D 列下没有数据行", table.grid.ref(table.header))
        if len(wrong) > len(rows) * 3 * 0.15:
            return skip("S/O/D 列定位未通过数值分布自校验（<85% 为 1~10 整数）", table.grid.ref(table.header), _list(wrong))
        if wrong:
            return bad("B05", wrong[0].split("=")[0], _list(wrong), "S/O/D 非 1~10 整数")
        if empties:
            return bad("W02", empties[0].split("=")[0], _list(empties), "S/O/D 为空")
        return ok(table.grid.ref(table.header), f"{len(rows)} 行 S/O/D 均为 1~10 整数")

    return Check(run, needs_tables=True)


CP_GENERIC = [
    # Control plans are often bilingual with some columns titled in English only (AIAG layout).
    ("零件/工序号", "工序号", "零件号", "processnumber", "part/process"),
    ("工序名称", "操作描述", "processname", "operationdescription"),
    ("生产设备", "工装", "机器", "machine", "device", "jig"),
    ("特性分类", "特殊特性", "特性类型", "specialchar", "class"),
    ("产品特性", "产品", "product"),
    ("过程特性", "过程", "process"),
    ("规格/公差", "公差", "规格", "specification", "tolerance"),
    ("评价测量技术", "测量技术", "measurementtechnique", "evaluation/measurement"),
    ("样本容量", "容量", "samplesize", "样本"),
    ("频次", "频率", "freq"),
    ("控制方法", "controlmethod"),
    ("反应计划", "reactionplan"),
]
CP_UAES_CLOSED = {"产品特性", "过程特性", "频次", "控制方法"}
CP_HEADER = [
    ("控制计划类型", "CP类型"),
    ("控制计划编号", "CP编号"),
    ("联系人",),
    ("电话",),
    ("编制日期",),
    ("修订日期",),
    ("版本", "Rev"),
    ("页数",),
    ("页码", "第"),
    PART_NO,
    PART_NAME,
    ("核心小组", "多功能小组"),
    ("公司名称", "公司"),
    ("公司代码",),
    ("公司批准",),
    ("顾客工程批准",),
    ("顾客质量批准",),
    ("其它批准", "其他批准"),
]
CP_HEADER_UAES = [("供应商名称",), ("供应商代码",), ("编制人", "编制"), ("版本号", "版本"), ("受控日期", "创建日期", "修订日期"), PART_NAME, PART_NO]


def _cp_header(non_empty: bool) -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        specs = CP_HEADER_UAES if ctx.customer == "UAES" else CP_HEADER
        found = [(spec[0], find_field(ctx, spec)) for spec in specs]
        if non_empty:
            empty = [(name, item) for name, item in found if item.found and blank(item.value) and name != "控制计划类型"]
            return (
                bad("W02", empty[0][1].location, "空：" + "、".join(name for name, _ in empty), "表头字段为空") if empty else ok("表头区", f"{sum(item.found for _, item in found)} 个已存在字段均有值")
            )
        absent = [name for name, item in found if not item.found]
        note = "（UAES 按 §7.9 差异层判 7 项，其余 11 项关闭）" if ctx.customer == "UAES" else ""
        if absent:
            return bad("B05" if len(absent) >= 3 else "W01", "表头区", "缺：" + "、".join(absent) + note, f"表头字段缺 {len(absent)} 项")
        return ok(found[0][1].location, f"{len(specs)} 项表头字段齐备{note}")

    return Check(run, labels=tuple(alt for spec in CP_HEADER + CP_HEADER_UAES for alt in spec))


def _cp_columns() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        specs = [spec for spec in CP_GENERIC if not (ctx.customer == "UAES" and spec[0] in CP_UAES_CLOSED)]
        table = ctx.table(specs)
        missing = [spec[0] for i, spec in enumerate(specs) if not table or i not in table.cols]
        note = "（UAES 差异层不要求 产品/过程分列、频次、控制方法）" if ctx.customer == "UAES" else ""
        if missing:
            return bad("B05", table.grid.ref(table.header) if table else "全文", "已有列：" + "、".join(table.titles.values()) if table else "", "缺必列：" + "、".join(missing) + note)
        return ok(table.grid.ref(table.header) if table else "", "、".join(table.titles.values()) + note if table else "")

    return Check(run, needs_tables=True)


def _cp_class_column() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table([("特性分类", "特殊特性", "特性类型")])
        if table is None:
            return skip("未找到特性分类列（由 M-04 判定）")
        values = [r for r in range(table.header + 1, len(table.grid.rows)) if not blank(table.grid.cell(r, table.cols[0]))]
        return (
            ok(table.grid.ref(values[0], table.cols[0]), table.grid.cell(values[0], table.cols[0]))
            if values
            else bad("B05", table.grid.ref(table.header, table.cols[0]), table.titles[0], "特性分类列全列为空")
        )

    return Check(run, needs_tables=True)


def _cp_controlled() -> Check:
    stamp = controlled()

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        if ctx.customer != "UAES":
            return "S02", None, "", "", "受控章仅对 UAES × T11 生效（10 分则 §7.9），该客户不要求"
        return stamp.run(ctx, "B05", "B05")

    return Check(run)


def _requirement_column() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table([("要求", "判定标准", "质量标准", "技术要求"), ("序号", "no")])
        if table is None or 0 not in table.cols:
            return bad("B05", "全文", "", "缺「要求 / 判定标准」列")
        if 1 not in table.cols:
            return ok(table.grid.ref(table.header, table.cols[0]), table.titles[0])
        empty = [table.grid.ref(r, table.cols[0]) for r in table.rows(1) if blank(table.grid.cell(r, table.cols[0]))]
        return bad("W02", empty[0], _list(empty), "待检项无标准") if empty else ok(table.grid.ref(table.header, table.cols[0]), table.titles[0])

    return Check(run, needs_tables=True)


def _part_numbers() -> Check:
    fake = {"123456", "111111", "000000", "待定", "tbd", "xxx", "xx"}

    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        table = ctx.table([PART_NO, PART_NAME])
        if table is None or 0 not in table.cols:
            return skip("未找到零件号列")
        hits = [f"{table.grid.ref(r, table.cols[0])}={table.grid.cell(r, table.cols[0])}" for r in table.rows() if norm(table.grid.cell(r, table.cols[0])) in fake]
        return bad("B03", hits[0].split("=")[0], _list(hits), "零件号为占位 / 脱敏假号") if hits else ok(table.grid.ref(table.header, table.cols[0]), "零件号无占位假号")

    return Check(run, needs_tables=True)


def _supplier_header() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        part, name = find_field(ctx, PART_NO), find_field(ctx, PART_NAME)
        empty = [label for label, item in (("零件号", part), ("零件名称", name)) if blank(item.value)]
        if empty:
            return bad("B03", (part if "零件号" in empty else name).location or "表头区", "空：" + "、".join(empty), "表头零件号 / 零件名称为空")
        supplier = find_field(ctx, ("供应商名称", "供应商", "部件供应商"))
        if blank(supplier.value):
            table = ctx.table([("供应商", "部件供应商", "材料生产商")])
            if table is None or not any(not blank(table.grid.cell(r, table.cols[0])) for r in table.rows()):
                return bad("B03", supplier.location or "表头区", "", "表头与明细区均无供应商")
        return ok(part.location, f"零件号={part.value}；零件名称={name.value}")

    return Check(run, labels=(*PART_NO, *PART_NAME, "供应商名称", "供应商"))


def _msa_conclusion() -> Check:
    def run(ctx: Ctx, miss: str, last: str) -> Outcome:
        found = find_text(ctx.doc, ("可接受", "不可接受", "acceptable", "%grr", "%r&r", "ndc", "结论"))
        return ok(*found) if found else bad("B05", "全文", "", "未见判定结论勾选、%GRR / ndc 数值或结论文本")

    return Check(run)


L1: dict[str, dict[str, Check]] = {
    "01-1": {
        "IR-02": title("检验报告", missing="W01", wrong="W03"),
        "IR-04": _requirement_column(),
        "IR-05": fields(("车型",), PART_NAME, PART_NO, ("供应商",), ("责任人", "负责人"), ("日期",), empty="W02", skip_missing="缺栏定级取决于提交范围档（R1/R2/R3），本期未判"),
        "IR-07": column_values(("检查方法", "检验方法", "测量方法", "检测方法"), lambda v: "/" not in v and "或" not in v, wrong="W03", what="检查方法含「/」或「或」，不唯一"),
        "IR-09": handoff("实测值与公差的比较移交正式审核"),
        "IR-10": column_values(("判定", "结果"), lambda v: norm(v) not in {"nok", "不合格", "pass", "正确"}, wrong="W03", what="判定结果取值不合法（NOK / 不合格 / PASS / 正确）"),
        "IR-11": handoff("同行实测值重复移交正式审核"),
        "IR-13": IMAGE,
        "IR-14": IMAGE,
        "IR-15": PEER,
        "IR-16": handoff("贴近上限提示过程能力移交正式审核"),
    },
    "01-2": {"C-10": _formula_errors(False), "C-17": _formula_errors(True), "S-07": handoff("合格率与明细行互推移交正式审核"), "S-08": PEER, "S-14": PEER, "S-15": PEER},
    "02-2": {"COM-09": present("样品数量", "样本数量", "sample quantity", code="B05"), "COM-13": IMAGE, "COR-10": IMAGE},
    "02-3": {
        "P-07": custom(lambda ctx, miss, last: ok(*found) if (found := _regex(ctx.doc, r"cnas\s*l\s*\d{4}")) else skip("文字中未见 CNAS L#### 注册号；证书多为扫描件，" + NEEDS_IMAGE)),
        "P-11": custom(lambda ctx, miss, last: ok(*found) if (found := find_text(ctx.doc, ("中国合格评定国家认可委员会",))) else skip("文字中未见 13 字机构全称；" + NEEDS_IMAGE)),
        "P-14": custom(lambda ctx, miss, last: ok(*found) if (found := _regex(ctx.doc, r"cnas\s*l\s*\d{4}")) else skip("未读到注册号文字；" + NEEDS_IMAGE)),
        "P-10": IMAGE,
        "P-13": todo("分则已停用此项（内容由 A-04 承接）"),
        "P-22": todo("分则已整项退出预检（总纲 2.3）"),
        "A-02": handoff("报告出具机构一致性比对属正式审核"),
        "A-03": handoff("报告签发日与证书有效期时间窗属正式审核"),
    },
    "03": {
        "PC-B1": _supplier_header(),
        "PC-C2": _part_numbers(),
        "PC-C3": column_values(("单件重量", "单重"), lambda v: _num(v) is not None, missing="B05", wrong="B05", what="单件重量不是单一数值"),
        "PC-D7": PEER,
        "PC-A8": PEER,
    },
    "06": {
        "MSA-PRE-01": _value_scan("B05", set(), "公式错误值或 ####", pattern=r"#(ref!|value!|div/0!|n/a|name\?|num!|null!)|#{3,}"),
        "MSA-PRE-03": _value_scan("W02", {"待填", "待定", "tbd", "xxx", "示例", "草稿", "draft"}, "未清除的占位符或草稿字样"),
        "MSA-PRE-16": _msa_conclusion(),
        "MSA-PRE-05": PEER,
        "MSA-PRE-06": PEER,
    },
    "07": {"PRE-09": _capability_residue(), "PRE-16": PEER},
    "09": {
        "R01": _pfmea_tables(),
        "R02": custom(lambda ctx, miss, last: ("S02", None, "", "", "UAES × T10 差异层「表头 8 字段：无此要求」（09 分则 §7.6）") if ctx.customer == "UAES" else _pfmea_header().run(ctx, miss, last)),
        "R08": todo("分则已停用此项（v1.1 起由 R09 统一）"),
        "R11": column_values(("特殊特性", "特性分类", "分类", "specialcharacteristic", "class"), lambda v: norm(v) in SC_WHITELIST, missing="B05", wrong="W03", what="特殊特性取值不在白名单内"),
        "R12": _sod(),
        "R17": PEER,
        "R18": PEER,
        "R19": PEER,
        "R20": PEER,
        "R21": PEER,
        "R22": PEER,
    },
    "10": {
        "M-01": _cp_header(False),
        "M-02": _cp_header(True),
        "M-04": _cp_columns(),
        "M-05": _cp_class_column(),
        "M-14": _cp_controlled(),
        "C-07": _value_scan("W05", set(), "掩码或公式错误显示", pattern=r"#(ref!|value!|div/0!|n/a|name\?|num!|null!)|#{3,}"),
        "P-05": PEER,
        "X-05": PEER,
        "X-07": PEER,
        "X-08": PEER,
        "X-09": PEER,
    },
}


def _regex(doc: Doc, pattern: str) -> tuple[str, str] | None:
    for grid, r, c, cell in all_cells(doc):
        if re.search(pattern, norm(cell)):
            return grid.ref(r, c), cell
    return next(((where, text) for where, text in doc.lines if re.search(pattern, norm(text))), None)


def checks_for(key: str) -> dict[str, Check]:
    spec = rule_source("l1.json")["routing"].get(key)
    return L1.get(spec, {}) if spec else {cid: check for cid, check in L2.items() if cid.startswith(key + ":")}


# ---------------------------------------------------------------- engine


def precheck_content(file_bytes: bytes, filename: str, entry_key: str, *, customer: str | None = None) -> list[dict[str, Any]]:
    """One result per criterion of the routed entry: 满足 / 不满足 (with B/W code) / S01 / S02 / S03 / 未执行 (with reason)."""
    source, criteria = entry_criteria(entry_key)
    if not criteria:
        raise KeyError(entry_key)
    checks = checks_for(entry_key)
    doc = read_document(file_bytes, filename)
    labels = tuple(label for check in checks.values() for label in check.labels)
    ctx = Ctx(doc, filename, customer or ENTRIES.get(entry_key, ("",))[0], labels, tuple(label for check in checks.values() if check.group for label in check.labels))
    results = []
    for item in criteria:
        check = checks.get(item["criterion_id"])
        miss, last = _codes(item["outcome"])
        if check is None:
            outcome = skip(NOT_IMPLEMENTED)
        elif check.needs_text and doc.error:
            outcome = skip(doc.error)
        elif check.needs_text and not doc.has_text:
            outcome = skip(NO_TEXT)
        elif check.needs_tables and not doc.grids:
            outcome = skip("未识别出表格结构，表格类判定未执行")
        else:
            try:
                outcome = check.run(ctx, miss, last)
            except Exception as exc:  # a reading gap is never a verdict
                outcome = skip(f"判定执行失败（{type(exc).__name__}），本项未判")
        result, code, location, evidence, reason = outcome
        if result == "不满足" and code == "B05" and check is not None and check.needs_tables and doc.kind == "pdf":
            result, code, reason = "未执行", None, "PDF 表格抽取未见所需列，不能据此判缺列：" + reason
        if result == "不满足" and not code:
            result, reason = "未执行", "规则未给出该情形的原因码，本期未判：" + reason
        results.append(
            {"criterion_id": item["criterion_id"], "criterion": item["criterion"], "result": result, "code": code, "location": location, "evidence": evidence, "reason": reason, "source": source}
        )
    return results
