"""UAES v1.0 m4 candidate: signed measurement-system clarifications."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.contracts import EvidenceSource
from audit.engine.rule_engine import Decision, GuardStop, RuleContext

from .msa import (
    _ACCEPT,
    _CONCLUSION_LABEL,
    _DECLARED_NA,
    _LISTS,
    _MARGINAL,
    _PART,
    _PGQP1,
    _PLACEHOLDERS,
    _PLAN_END,
    MsaClauses,
    PlanRow,
    UaesMsaAgentR3,
    _c,
    _text,
    compact,
    gauge_family,
    gauge_kind,
    original_name,
    unable,
)
from .msa import (
    Report as MsaReport,
)
from .scoped_text_r4 import read_facts
from .special_characteristics_r4 import labelled_number, number_label, parts_in

_PARAMETER = ("测量参数", "Measure parameter", "Measuring parameter", "特性", "特 性", "Denote Requirement", "标注要求")
_LIMIT = ("参数规格", "Parameter specification")
_UNIT_LABEL = ("测量单位", "测量参数单位", "measurement unit", "unit of measurement")
_LINEAR_UNIT = r"(?:mm|μm|um|毫米|微米)"


def linear_unit(value: str | None) -> str | None:
    unit = _c(value or "")
    return {"mm": "mm", "毫米": "mm", "μm": "μm", "um": "μm", "微米": "μm"}.get(unit)


# A linear specification is evidence only when both endpoints and a unit can
# be read. Never borrow a unit from the other document or from a sibling value.
_MAG = r"(?:\d+(?:\.\d*)?|\.\d+)"
_NOMINAL = rf"([+-]?{_MAG})"
_TOL = rf"(?:±({_MAG})|\+({_MAG})/-({_MAG}))"
_RANGE = re.compile(rf"{_NOMINAL}[-–~至]{_NOMINAL}({_LINEAR_UNIT})?")
_COMBINED = re.compile(rf"{_NOMINAL}{_TOL}({_LINEAR_UNIT})?")
_SINGLE = re.compile(rf"{_NOMINAL}({_LINEAR_UNIT})?")
_SCALE = {"mm": Decimal(1000), "μm": Decimal(1)}


def length_bounds(raw: str, limits: tuple, declared: frozenset[str]):
    """Read one complete length specification in µm; an unbound or conflicting unit is not evidence."""

    def scale(suffix):
        sources = declared | ({linear_unit(suffix)} if suffix else set())
        return _SCALE.get(next(iter(sources))) if len(sources) == 1 else None

    value = _c(raw).replace("−", "-")
    if not limits and (interval := _RANGE.fullmatch(value)):
        factor = scale(interval[3])
        if factor and Decimal(interval[1]) <= Decimal(interval[2]):
            return Decimal(interval[1]) * factor, Decimal(interval[2]) * factor
    if not limits and (both := _COMBINED.fullmatch(value)):
        nominal, symmetric, upper, lower, suffix = both.groups()
        nominal_scale = tol_scale = scale(suffix)
    elif len(limits) == 1 and (single := _SINGLE.fullmatch(value)):
        deviation = _c(limits[0]).strip("()").replace("−", "-")
        # In a standalone field the upper deviation may be written unsigned.
        if "/" in deviation and not deviation.startswith("+"):
            deviation = "+" + deviation
        tol = re.fullmatch(rf"{_TOL}({_LINEAR_UNIT})?", deviation)
        if not tol:
            return None
        nominal, suffix = single.groups()
        symmetric, upper, lower, tol_suffix = tol.groups()
        nominal_scale, tol_scale = scale(suffix), scale(tol_suffix)
    else:
        return None
    if nominal_scale and tol_scale:
        n = Decimal(nominal) * nominal_scale
        return n - Decimal(symmetric or lower) * tol_scale, n + Decimal(symmetric or upper) * tol_scale
    return None


@dataclass
class Report(MsaReport):
    """Keep separately anchored conclusion and metric evidence for this revision."""

    result_cells: tuple = ()
    dimension_units: frozenset[str] = frozenset()
    supports: dict[str, list[str]] = field(default_factory=dict)
    pdf_evidence: list[EvidenceSource] = field(default_factory=list)


_SUPPORT = {
    "客户批准": "approval",
    "客户认可": "approval",
    "整改说明": "correction",
    "整改措施": "correction",
    "公差带豁免说明": "waiver",
    "customer approval": "approval",
    "corrective action": "correction",
    "tolerance waiver": "waiver",
}
_SUPPORT = {compact(k): v for k, v in _SUPPORT.items()}
_RESULT = {"结论", "conclusion", "conclude", "判定", "judgement", "judgment"}
_RATINGS = {"accept", "reject", "marginal"}


def literal_rating(text: str) -> str | None:
    """A whole standalone rating needs no model. Never match an embedded result word in prose.

    A result field may restate its acceptance threshold before the verdict ("%R&R<10%，测量系统可以接受！"); the
    verdict after that stated threshold is the rating."""
    words = compact(text).strip("。.!！")
    if stated := re.fullmatch(r"%?(?:grr|r&r|gr&r)[<＜≤]\d+(?:\.\d+)?%[,，;；]?(.+)", words):
        words = stated[1].strip("。.!！")
    if not words or words == "pending" or words in _PLACEHOLDERS:
        return "unknown"
    if words in _MARGINAL:
        return "marginal"
    if words in {"不合格", "不可接受", "不通过", "unacceptable", "fail", "failed", "reject", "rejected", "poor", "bad", "ng"}:
        return "reject"
    if (
        words in _ACCEPT
        or re.fullmatch(r"(?:该|本|此)?(?:测量系统|量具)(?:可接受|可以接受|能接受|合格|通过|满足要求|符合要求)", words)
        or re.fullmatch(r"(?:the)?(?:gage|gauge|measurementsystem)(?:is(?:acceptable|ok(?:touse)?)|canaccept(?:it)?|canbeaccepted)", words)
    ):
        return "accept"
    return None


def literal_support(role: str, value: str) -> str | None:
    word = compact(value)
    if not word or word in _DECLARED_NA or word in {"未批准", "未认可", "未豁免", "不批准", "不认可", "notapproved"}:
        return "absent"
    if word in _PLACEHOLDERS:
        return "unknown"
    if role == "approval" and re.fullmatch(r"(?:授权单|批准单)\s*[:：]?\s*[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)+", value):
        return "documented"
    return None


def read_statements(reports: list[MsaReport]) -> dict[tuple[str, str], str]:
    """One optional read per audited original, irrespective of its number of studies."""
    candidates: dict[tuple[str, str], None] = {}
    for report in reports:
        if not isinstance(report, Report):
            continue
        for value in report.conclusions:
            if literal_rating(value) is None:
                candidates["conclusion", value] = None
        roles = (["approval", "correction"] if report.grr is not None and 10 < report.grr <= 30 else []) + (["waiver"] if report.ndc is not None and report.ndc < 5 else [])
        for role in roles:
            for value in report.supports.get(role, []):
                if literal_support(role, value) is None:
                    candidates[role, value] = None
    # Never truncate, split into extra requests, or cite a partial quote.
    if not candidates or len(candidates) > 24:
        return {}
    source_ids = {f"{role}:{i}": (role, value) for i, (role, value) in enumerate(candidates)}
    sources = {key: value for key, (_role, value) in source_ids.items()}
    properties = {}
    for key, (role, _v) in source_ids.items():
        states = _RATINGS if role == "conclusion" else {"documented", "absent"}
        properties[key] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["state", "quote"],
            "properties": {"state": {"type": "string", "enum": sorted(states | {"unknown"})}, "quote": {"type": "string"}},
        }
    try:
        facts = read_facts(
            "uaes_r4_msa_statements",
            sources=sources,
            schema={"type": "object", "additionalProperties": False, "required": list(sources), "properties": properties},
            instructions="各字段独立，不串用。conclusion 只读本次研究结论栏的最终表态：accept 无条件可接受，reject 明确不接受，marginal 明确临界或有条件接受。"
            "前次结果、示例、假设或待批不代表当前表态。注意否定的对象，冲突或无法区分时 unknown。approval 指客户确已批准，correction 指有具体整改说明（不要求已完成），"
            "waiver 指明确写明的公差带豁免。documented 表示本次该材料明确载明；absent 表示明确没有；只有申请、条件、历史信息或不确定则 unknown。"
            "每个 quote 须原样返回该字段的完整值，包括条件和限定，不得摘词。",
        )
    except GuardStop as exc:
        if exc.decision != Decision.UNABLE:
            raise
        return {}
    if not isinstance(facts, dict) or set(facts) != set(sources):
        return {}
    result = {}
    for key, (role, text) in source_ids.items():
        fact = facts[key]
        allowed = _RATINGS if role == "conclusion" else {"documented", "absent"}
        if isinstance(fact, dict) and set(fact) == {"quote", "state"} and fact["quote"] == text and isinstance(fact["state"], str) and fact["state"] in allowed:
            result[role, text] = fact["state"]
    return result


def support(report: Report, facts: dict, *roles: str) -> str:
    outcomes = []
    for role in roles:
        values = {literal_support(role, value) or facts.get((role, value), "unknown") for value in report.supports.get(role, [])}
        if values:
            outcomes.append(next(iter(values)) if len(values) == 1 else "unknown")
    if "documented" in outcomes:
        return "documented"
    if "unknown" in outcomes or (len(outcomes) < len(roles) and str(report.item.file_url).lower().endswith(".pdf")):
        return "unknown"
    return "absent"


class UaesMsaAgentR4(UaesMsaAgentR3):
    """Apply m4 row scope, anchored GR&R conclusions, and same-part fallback-list coverage."""

    def plans(self, ctx: RuleContext, item: Any) -> tuple[list[PlanRow], list[dict]]:
        """(rows, tables) of every MSA plan table in `item`: a list whose heading row names a gauge and a characteristic."""
        rows: list[PlanRow] = []
        tables: list[dict] = []
        with self.workbook_of(ctx, item):
            for ws in ctx.workbook.worksheets:
                grid = self.grid(ctx, ws)
                for header, columns in self.plan_headers(grid):
                    table = {"sheet": ws.title, "header": header, "columns": columns, "item": item, "extra": {}}
                    tables.append(table)
                    last_row = max((r for r, _c in grid), default=header)
                    headings = {c: grid[(header, c)] for r, c in grid if r == header}
                    table["extra"] = {c: text for c, text in headings.items() if c not in columns.values() and "备注" not in _c(text)}
                    for row in range(header + 1, last_row + 1):
                        first = next((grid[(row, c)] for c in sorted(c for r, c in grid if r == row)), "")
                        if any(_c(first).startswith(marker) for marker in _PLAN_END):
                            break
                        values = {name: grid.get((row, column), "") for name, column in columns.items()}
                        # A serial number alone is preprinted stock, but even a remark or date
                        # on that row is evidence of actual use and keeps the row in scope.
                        if not any(r == row and c != columns.get("序号") for r, c in grid):
                            continue
                        values["_extra"] = "；".join(f"{_text(headings[c])}" for c in table["extra"] if not grid.get((row, c)))
                        place = self.READER.Place.of(f"“{ws.title}”第{row}行", ws, row, columns.get("量具名称") or columns.get("序号") or 1) if self.mine(item) else None
                        rows.append(PlanRow(item, ws.title, row, values, place))
        return rows, tables

    def list_part_heading(self, ctx: RuleContext, item: Any) -> bool:
        """Whether the part column `list_rows` reads in this special characteristic list is headed as a combined name and code."""
        if str(item.file_url).lower().endswith(".pdf"):
            return False
        headings = []
        with self.reading(ctx, item, self.READER.resources):
            for ws in ctx.workbook.worksheets:
                grid = self.grid(ctx, ws)
                for (row, _column), text in sorted(grid.items()):
                    equipment = any(term in _c(text) for term in ("测量设备", "measuringequipment", "检测设备", "测量工具", "量具"))
                    if row <= 12 and equipment and not any(term in _c(text) for term in ("能力", "capability")):
                        # The written heading keeps its line breaks ("产品名称/零件号\nName / Code"); the grid joins them.
                        heads = [str(ws.cell(r, c).value) for (r, c), t in sorted(grid.items()) if r == row and any(term in _c(t) for term in ("零件号", "件号", "partno", "/code"))]
                        headings += heads[:1]
                        break
        return bool(headings) and number_label(headings[0], combined=True) and not number_label(headings[0])

    def coverage(self, ctx: RuleContext, items: list[tuple[str, str, str, str]], parts: list[str], main: Any) -> tuple[list[tuple[str, Any]], dict[str, list[str]], list[str]]:
        defects, gauges, unresolved = super().coverage(ctx, items, parts, main)
        if not defects and not unresolved:
            for verified in gauges.values():
                for _ in verified:
                    ctx.record_execution()  # The returned coverage established a matching qualified study.
        return defects, gauges, unresolved

    @rule("特殊特性量具的分析报告覆盖与结论")
    def rule_6(self, ctx: RuleContext):
        if self.batch_files(_PGQP1):
            return super().rule_6(ctx)
        plan, main, _scanned = self.suite_plan(ctx)
        lists = [item for kind in _LISTS for item in self.batch_files(kind)]
        if not lists:
            raise unable("同批次既没有PGQP1，也没有特殊特性清单，没有可取特殊特性的来源，需人工复核。")
        if len(lists) > 1:
            raise unable(f"同批次未提交PGQP1，回退的特殊特性清单有{len(lists)}份，尚不能确定按哪一份核对，需人工复核。")
        found = self.list_rows(ctx, lists[0])
        name = original_name(lists[0])
        if found is None:
            raise unable(f"同批次未提交PGQP1，回退特殊特性清单《{name}》；未能在清单中定位测量设备栏，检查程序不能按“特性→量具”对齐，需人工复核。")
        if not found:
            raise unable(f"同批次未提交PGQP1，回退特殊特性清单《{name}》没有读到特性行，不能按空范围通过，需人工复核。")
        source = f"特殊特性清单《{name}》"
        planned = {row.values["零件号"].strip() for row in plan if row.values.get("零件号") and _c(row.values["零件号"]) not in _PLACEHOLDERS | _DECLARED_NA}
        local_plan, _tables = self.plans(ctx, self.audit_file)
        local_parts = {row.values["零件号"].strip() for row in local_plan if row.values.get("零件号") and _c(row.values["零件号"]) not in _PLACEHOLDERS | _DECLARED_NA}
        if len(local_parts) > 1:
            raise unable(f"本文件计划有多个零件号，不能唯一确定{source}的适用范围。")
        candidates = local_parts or planned
        own_part = next(iter(candidates)) if len(candidates) == 1 else ""
        own_codes = {code for report in self.reports_of(ctx, self.audit_file) for code in report.subject_codes}
        if own_part and own_codes and own_codes != {own_part}:
            raise unable(f"套件计划和本文件报告所写零件号不一致，不能确定{source}的适用范围。")
        if not own_part and len(own_codes) == 1:
            own_part = next(iter(own_codes))

        # A number must be the complete value of a dedicated part field. Only a column headed as a combined
        # name and code ("产品名称/零件号 Name / Code") holds a name beside the part; there the row's part is
        # its single whole ID atom, and a cell with more than one ID stays unresolved.
        combined = self.list_part_heading(ctx, lists[0])
        row_parts = []
        for _, raw, _, _, _ in found:
            if _c(raw) in _PLACEHOLDERS | _DECLARED_NA:
                row_parts.append("")
            elif combined and not labelled_number(raw) and len(ids := parts_in(raw)) == 1:
                row_parts.append(next(iter(ids)))
            else:
                row_parts.append(raw.strip())
        written = {part for part in row_parts if part}
        items = [(where, feature, value, gauge) for where, _p, feature, value, gauge in found]
        if len(written) > 1:
            if not own_part:
                raise unable(f"{source}列有多个零件号，未能确定本零件号，不能选择适用特性行。")
            items = [item for item, part in zip(items, row_parts) if part == own_part]
            if not items:
                raise unable(f"{source}没有明确归属本零件号{own_part}的行，不能按空范围通过。")
            source += f"中明确归属本零件号{own_part}的行"
        elif written and own_part and written != {own_part}:
            raise unable(f"{source}所列零件号与本零件号{own_part}不符，不能确认适用的特性范围。")
        part = own_part or next(iter(written), "")
        status, reason = self.landed(source, items, *self.coverage(ctx, items, [part] * len(items), main))
        if status == "UNABLE":
            raise unable(reason)
        return status, reason

    def sheet_report(self, ctx: RuleContext, item: Any, ws: Any) -> Report | None:
        grid = self.grid(ctx, ws)
        if not grid:
            return None
        words = [_c(text) for text in grid.values()]
        title = f"{ws.title} " + " ".join(text for (r, _c2), text in sorted(grid.items()) if r <= 4)
        has = lambda *terms: any(term in word for word in words for term in terms)  # noqa: E731
        if any(word.startswith("cgk=") or word == "cgk" for word in words):
            kind = "Cgk"
        elif ("kappa" in _c(ws.title) or "kappa" in words) and (not self.plan_headers(grid) or any(compact(_text(value).partition(":")[0]) in _RESULT for value in grid.values())):
            kind = "Kappa"
        elif has("计数型") and any(_CONCLUSION_LABEL.match(word) for word in words):
            kind = "Attr"
        elif has("重复性和再现性", "重复性与再现性", "重复性及再现性", "repeatability", "r&r", "grr") and any(_CONCLUSION_LABEL.match(word) for word in words):
            kind = "GRR"
        else:
            return None
        report = Report(kind, item, f"“{ws.title}”", title=title)
        text = " ".join(grid.values())
        report.text = _c(text)
        report.parts = set(_PART.findall(unicodedata.normalize("NFKC", text).upper()))
        report.names = [value for value in self.glabel(grid, (*self.READER.resources.GAUGE_NAME, "名称 Object", "测量工具名称")) if _c(value) not in _PLACEHOLDERS]
        report.numbers = [value for value in self.glabel(grid, (*self.READER.resources.GAUGE_NUMBER, "编号")) if _c(value) not in _PLACEHOLDERS]
        # A dimension unit belongs to a declared measurement field or to an
        # explicit measurement-unit field, never to the gauge range or the plan.
        dimension_labels = {compact(label): field for field, aliases in (("parameter", _PARAMETER), ("limit", _LIMIT), ("unit", _UNIT_LABEL)) for label in aliases}
        data: dict[str, list[tuple[int, int, str]]] = {"parameter": [], "limit": [], "unit": []}
        unit_places = []
        for (row, col), text in grid.items():
            heading, _, inline = _text(text).partition(":")
            unit = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", heading)
            field = dimension_labels.get(compact(unit[1] if unit else heading))
            if not field:
                continue
            if unit and field != "unit":
                unit_places.append((row, col, unit[2]))
            if inline.strip():
                data[field].append((row, col, inline.strip()))
            else:
                merged = ctx.ops.get_detailed_merge_info(ws, row, col)
                next_col = (merged.max_col if merged else col) + 1
                if value := grid.get((row, next_col)):
                    data[field].append((row, next_col, value))
        parameter_cells, limit_cells = data["parameter"], data["limit"]
        # A standalone unit field can describe the same row or an adjoining
        # registration row, not a separate section after intervening fields.
        value_rows = {r for r, _, _ in parameter_cells + limit_cells}
        scope = set(value_rows)
        rows = sorted({r for r, _ in grid})
        for before, after in zip(rows, rows[1:]):
            if before in value_rows:
                scope.add(after)
            if after in value_rows:
                scope.add(before)
        unit_places.extend(cell for cell in data["unit"] if cell[0] in scope)
        characteristic = [value for _, _, value in parameter_cells]
        units = {linear_unit(text) or "unresolved" for _, _, text in unit_places if _c(text) not in _PLACEHOLDERS}
        report.dimension_units = frozenset(units)
        report.study_cells = tuple(dict.fromkeys((ws, r, c) for r, c, _ in (*parameter_cells, *limit_cells, *unit_places)))
        report.studied = tuple(v for v in characteristic if _c(v) not in _PLACEHOLDERS)
        limits = [value for _, _, value in limit_cells]
        report.limits = tuple(v for v in limits if _c(v) not in _PLACEHOLDERS)
        characteristic += limits
        subjects = self.glabel(grid, ("零件号", "零件名称", "零件编号", "Part No.", "Part Number", "Part Name", "Product number", "测量对象: Measurement object"))
        report.subject_codes = frozenset(v.strip() for v in subjects if re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", v.strip()) and any(ch.isdigit() for ch in v))
        report.characteristic = " ".join(value for value in characteristic if _c(value) not in _PLACEHOLDERS)
        people = self.glabel(grid, ("测量人员", "评价人员", "Analysing staff", "Measure staff", "测量日期", "分析日期", "Analysing date", "Measuring date", "日期", "Date"))
        report.signature = (kind, tuple(_c(p) for p in people), tuple(report.studied))
        # A different file name or gauge number alone does not prove a distinct study.
        report.place = self.READER.Place.of(report.where, ws, 1, 1) if self.mine(item) else None

        def original(row: int, column: int) -> str:
            raw = ws.cell(row, column).value
            return raw if isinstance(raw, str) and not raw.startswith("=") else grid.get((row, column), "")

        for (row, column), cell in sorted(grid.items()):
            word = _c(cell)
            fields = re.split("[:：]", original(row, column), maxsplit=1)
            label, inline = fields[0], fields[1].strip() if len(fields) > 1 else ""
            if kind == "GRR":
                if compact(label) in _RESULT:
                    # Read the actual result field, not a rubric or an unselected
                    # checkbox further along the same row.
                    merged = ctx.ops.get_detailed_merge_info(ws, row, column)
                    next_col = (merged.max_col if merged else column) + 1
                    result = inline or original(row, next_col)
                    if result and not result.startswith("□"):
                        report.conclusions.append(result)
                        report.result_cells += ((ws, row, column if inline else next_col),)
                elif word in {"%grr", "%r&r", "ndc"} and (value := grid.get((row, column + 1))):
                    if re.fullmatch(r"-?\d+(?:\.\d+)?%?", _c(value)):
                        number = Decimal(_c(value).rstrip("%"))
                        if word == "ndc":
                            report.ndc = number
                        else:
                            if "%" in ws.cell(row, column + 1).number_format and not value.endswith("%"):
                                number *= 100
                            report.grr = number
                        report.result_cells += ((ws, row, column + 1),)
                elif re.match(r"^%(grr|r&r)=", word) and report.grr is None:
                    report.grr = self.formula_value(grid, row, column, ws=ws)
                elif re.match(r"^ndc=", word) and report.ndc is None:
                    report.ndc = self.formula_value(grid, row, column)
            elif kind in ("Kappa", "Attr"):
                if compact(label) in _RESULT:
                    values = [(column, inline)] if inline else self.right(grid, row, column, gap=0)
                    for c, value in values:
                        if value.endswith((":", "：")) or compact(value) in _RESULT:
                            break
                        report.conclusions.append(inline or original(row, c))
                        report.result_cells += ((ws, row, c),)
                elif word == "kappa" or ("kappa" in word and any(term in word for term in ("总体", "整体", "overall"))):
                    for c, value in self.right(grid, row, column):
                        if re.fullmatch(r"-?\d+(?:\.\d+)?", _c(value)):
                            (report.kappas if word == "kappa" else report.overall).append(Decimal(_c(value)))
                            report.result_cells += ((ws, row, c),)
            if kind == "GRR":
                role = _SUPPORT.get(compact(label))
                if role:
                    merged = ctx.ops.get_detailed_merge_info(ws, row, column)
                    c = column if inline.strip() else (merged.max_col if merged else column) + 1
                    value = inline.strip() or original(row, c)
                    report.supports.setdefault(role, []).append(value)
                    report.result_cells += ((ws, row, c if value else column),)
        return report

    @staticmethod
    def formula_value(grid: dict, row: int, column: int, ws: Any = None) -> Decimal | None:
        """The computed value under a formula label: the first number written after an "=" cell in the next rows."""
        for r in range(row + 1, row + 4):
            for c in range(max(1, column - 3), column + 6):
                if _c(grid.get((r, c), "")) == "=" and (value := grid.get((r, c + 1))) is not None and re.fullmatch(r"-?\d+(\.\d+)?%?", _c(value)):
                    number = Decimal(_c(value).rstrip("%"))
                    # A spreadsheet percentage cell stores a fraction (0.0415 = 4.15%).
                    if ws is not None and not _c(value).endswith("%") and "%" in ws.cell(r, c + 1).number_format:
                        number *= 100
                    return number
        return None

    def pdf_reports(self, item: Any) -> list[MsaReport]:
        import fitz

        found: list[MsaReport] = []
        records: dict[str, Report] = {}
        scanned = set(self.scanned(item))
        with fitz.open(str(item.file_url)) as document:
            pages = [
                (
                    page.number + 1,
                    [
                        (line["bbox"], raw)
                        for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
                        for line in block.get("lines", [])
                        if (raw := "".join(span["text"] for span in line["spans"]).strip())
                    ],
                )
                for page in document
                if page.number + 1 not in scanned
            ]
        for number, regions in pages:
            lines = [raw for _box, raw in regions]
            if not lines:
                continue
            text = " ".join(lines)
            head = " ".join(lines[:3])

            def after(label: str, offset: int = 1) -> str:
                index = next((i for i, line in enumerate(lines) if _c(line).rstrip(":") == _c(label)), None)
                return lines[index + offset] if index is not None and index + offset < len(lines) else ""

            def row_values(box):
                # Production PDFs can encode the label and its value as separate text runs.
                y = (box[1] + box[3]) / 2
                return sorted(((other, raw) for other, raw in regions if other[0] >= box[2] - 1 and abs((other[1] + other[3]) / 2 - y) <= 0.35 * (box[3] - box[1])), key=lambda entry: entry[0][0])

            def cite_value(report, box, label, raw):
                version = str((getattr(item, "file_metadata", {}) or {}).get("file_version_id") or getattr(item, "audit_id", ""))
                locator = {"kind": "pdf_region", "page": number, "bbox": list(box), "file_version_id": version, "fact": label}
                report.pdf_evidence.append(EvidenceSource(kind="structure", locator=json.dumps(locator, ensure_ascii=False), excerpt=raw[:500]))

            if any("重复性及再现性分析报告" in _c(line) or "gr&r报告" == _c(line) for line in lines):
                report = Report("GRR", item, f"第{number}页", title=head, text=_c(text))
                report.parts = set(_PART.findall(text.upper()))
                for box, raw in regions:
                    label, _sep, inline = re.split(r"([:：=])", raw, maxsplit=1) if re.search(r"[:：=]", raw) else (raw, "", "")
                    role = compact(label)
                    if role not in _RESULT | _SUPPORT.keys() | {"零件代号", "零件号", "量具名称", "量具编号", "特性", "测量参数", "%r&r", "%grr", "ndc"}:
                        continue
                    adjacent = row_values(box)
                    value = inline.strip()
                    where = box
                    if not value and adjacent:
                        where, value = adjacent[0]
                    if role in _RESULT:
                        if value:
                            report.conclusions.append(value)
                    elif role in _SUPPORT:
                        if not value:  # The blank or graphical cell is not proof that an attachment does not exist.
                            continue
                        report.supports.setdefault(_SUPPORT[role], []).append(value)
                    elif role in {"零件号", "零件代号"} and value:
                        report.subject_codes |= {value}
                    elif role == "量具名称" and value:
                        report.names.append(value)
                    elif role == "量具编号" and value:
                        report.numbers.append(value)
                    elif role in {"特性", "测量参数"} and value:
                        report.characteristic = value
                        report.studied += (value,)
                    elif role == "ndc" and re.fullmatch(r"\d+(?:\.\d+)?", _c(value)):
                        report.ndc = Decimal(_c(value))
                    elif role in {"%grr", "%r&r"} and re.fullmatch(r"\d+(?:\.\d+)?%", _c(value)):
                        report.grr = Decimal(_c(value).rstrip("%"))
                    else:
                        continue
                    if value:
                        cite_value(report, where, label, raw if inline.strip() else value)
                report.signature = ("GRR", tuple(report.numbers), _c(report.characteristic), number)
                found.append(report)
            elif "Attribute Report" in head:
                record = re.search(r"Record No:\s*(\S+)", text)
                key = record.group(1) if record else str(number)
                existing = records.get(key)
                if existing is None:
                    report = Report("Attr", item, f"第{number}页起（Record No {key}）", title=head)
                    report.parts = set(_PART.findall(text.upper()))
                    report.names = [after("Gage Name", 3)] if after("Gage Name", 3) else []
                    gauge_number = after("Gage Number", 3)
                    report.numbers = [gauge_number] if _c(gauge_number) not in _PLACEHOLDERS else []
                    report.characteristic = after("Denote Requirement", 5)
                    report.studied = (report.characteristic,) if report.characteristic else ()
                    report.signature = ("Attr", key)
                    records[key] = report
                    found.append(report)
                else:
                    report = existing
                report.text += _c(text)
                for box, line in regions:
                    word = _c(line)
                    if word in {"kappa", "overallkappa"}:
                        for where, raw in row_values(box):
                            if re.fullmatch(r"-?\d+(?:\.\d+)?", _c(raw)):
                                (report.kappas if word == "kappa" else report.overall).append(Decimal(_c(raw)))
                                cite_value(report, where, line, raw)
                    elif match := re.fullmatch(r"conclude(?:[:：]\s*(.+)|(Good|Marginal|Acceptable|Unacceptable|Poor|OK|NG|Pass|Fail))?", line, re.I):
                        # A PDF can concatenate the heading with the first rating. Never consume unlabelled words elsewhere on the page.
                        for where, raw in ([(box, match[1] or match[2])] if match[1] or match[2] else []) + row_values(box):
                            report.conclusions.append(raw)
                            cite_value(report, where, line, raw)
                if report.kappas or report.overall:
                    report.kind = "Kappa"
        return found

    def check_report(self, report: MsaReport, facts: dict | None = None) -> tuple[str, str] | None:
        """A report's own failure (NG), or an UNABLE reason, or None when its conclusion and indices pass."""
        if not isinstance(report, Report):
            return super().check_report(report)
        facts = facts or {}
        verdicts = [literal_rating(value) or facts.get(("conclusion", value), "unknown") for value in report.conclusions]
        if report.kind == "Cgk":
            return None
        opens: list[str] = []
        if not report.conclusions:
            opens.append("未读到结论")
        if "marginal" in verdicts:
            return "NG", f"{report.label}结论为有条件接受/临界类“{'；'.join(report.conclusions)}”，须整改后复评，按不通过处理"
        if "reject" in verdicts:
            return "NG", f"{report.label}结论“{'；'.join(report.conclusions)}”不是可接受"
        if "unknown" in verdicts:
            odd = [value for value, v in zip(report.conclusions, verdicts) if v == "unknown"]
            opens.append(f"结论“{'；'.join(odd)}”不能确定是否为可接受的写法")
        if report.kind == "GRR":
            if report.grr is None:
                opens.append("未能读出%GRR，底层指标是否达标尚未核实")
            elif report.grr > 30:
                return "NG", f"{report.label}%GRR为{report.grr.normalize()}%，大于30%，即使结论写可接受也按数据失真判不通过"
            elif report.grr > 10:
                answer = support(report, facts, "approval", "correction")
                if answer != "documented":
                    if answer == "unknown":
                        opens.append(f"%GRR为{report.grr}%，未能核实本次研究的客户批准或整改说明")
                    else:
                        return "NG", f"{report.label}%GRR为{report.grr}%（10%～30%），未附客户批准或整改说明"
            if report.ndc is None:
                opens.append("未能读出ndc，底层指标是否达标尚未核实")
            elif report.ndc < 5:
                answer = support(report, facts, "waiver")
                if answer != "documented":
                    if answer == "unknown":
                        opens.append(f"ndc为{report.ndc}，未能核实本次研究的公差带豁免说明")
                    else:
                        return "NG", f"{report.label}ndc为{report.ndc}，小于5且无公差带豁免说明"
        if report.kind == "Kappa":
            values = report.overall or report.kappas
            if not values:
                return "UNABLE", f"{report.label}未能读出Kappa值，底层指标是否达标尚未核实"
            if low := [value for value in values if value < Decimal("0.75")]:
                return "NG", f"{report.label}Kappa为{'、'.join(str(v) for v in low)}，低于0.75，即使结论写可接受也按数据失真判不通过"
        return ("UNABLE", report.label + "；".join(opens)) if opens else None

    @staticmethod
    def plan_feature_match(feature: str, report: MsaReport) -> bool | None:
        """The report must study the plan characteristic, not just mention a gauge or a number."""
        if not report.studied:
            return False if not report.limits else None
        observed = {_c(v) for v in report.studied}
        if len(observed) != 1:
            return None
        written = next(iter(observed))
        expected = _c(feature)
        if not expected or expected in _DECLARED_NA | _PLACEHOLDERS:
            return None
        # A named characteristic can be matched literally. An isolated number,
        # range or nominal/tolerance must establish the full physical interval.
        numeric = expected.replace("−", "-")
        if any(pattern.fullmatch(numeric) for pattern in (_RANGE, _COMBINED, _SINGLE)):
            wanted = length_bounds(expected, (), frozenset())
            seen = length_bounds(written, report.limits, getattr(report, "dimension_units", frozenset()))
            return wanted == seen if wanted is not None and seen is not None else None
        if written == expected:
            return True
        return MsaClauses.characteristic_match(feature, feature, report, unique_name=True, unique_requirement=True)

    @rule("计划量具与GRR/Kappa报告匹配及报告结论与指标")
    def rule_2(self, ctx: RuleContext):
        plan, main, scanned = self.suite_plan(ctx)
        underlying = self.reports_of(ctx, self.audit_file)
        own = [report for report in underlying if report.kind != "Cgk"]
        facts = read_statements(own)
        failures, opens, notes, passed = [], [], [], []
        for report in own:
            for cell in (*report.study_cells, *(report.result_cells if isinstance(report, Report) else ())):
                self.cite(*cell)
            if isinstance(report, Report):
                self.__dict__.setdefault("_cited", []).extend(report.pdf_evidence)
            result = self.check_report(report, facts)
            if report.kind in ("GRR", "Kappa"):
                ctx.record_execution()
            if result is None:
                passed.append(self.checked(report))
            elif result[0] == "NG":
                failures.append(result[1])
            else:
                opens.append(result[1])
        if without := [report for report in own if report.kind == "Kappa" and not report.overall and len(report.kappas) > 1]:
            notes.append(f"{without[0].label}{'等' + str(len(without)) + '份Kappa研究' if len(without) > 1 else ''}没有总体Kappa，已逐项核对读到的各个Kappa值（含单个评价者的分项值）")
        suite_level = self.mine(main)
        reports = self.all_reports(ctx)
        elsewhere: list[str] = []  # Defects of other files' gauges: not in this file's scope.
        # A suite without any plan table fails in every one of its files (product owner, 2026-11-24).
        if not plan:
            if scanned:
                opens.append(f"同批次《{'》《'.join(scanned)}》有没有文字层的页面，尚不能确认是否有测量系统分析计划表")
            else:
                failures.append("文件内及同批次文件中没有测量系统分析计划表")
        if suite_level:
            if not any(report.kind in ("GRR", "Kappa") for report in reports):
                if any(self.scanned(item) for item in self.suite()):
                    opens.append("套件包含扫描页，不能仅根据文本提取断定缺少GR&R或Kappa报告")
                else:
                    failures.append("文件内及同批次文件中没有重复性及再现性（GR&R）报告或Kappa报告")
        groups: dict[tuple[str, str, str], list[PlanRow]] = {}
        for row in plan:
            gauge = row.gauge
            where = f"《{original_name(row.item)}》“{row.sheet}”第{row.row}行"
            key = gauge_family(gauge) or _c(gauge)
            if not _c(gauge) or _c(gauge) in _PLACEHOLDERS:
                if suite_level:
                    opens.append(f"{where}量具名称{'为空' if not _c(gauge) else '为占位词“' + gauge + '”'}，该项未审")
                continue
            if gauge_kind(gauge) == "mixed":
                if self.mine(self.gauge_file(ctx, key, "", main)):
                    opens.append(f"{where}量具名称“{gauge}”跨计数型与计量型，无法判定属哪一类，移交工程裁定")
                continue
            groups.setdefault((key, row.values.get("零件号", ""), row.values.get("特性名称", "")), []).append(row)
        # A passing sibling never authenticates this file's own study.
        for report in own:
            plan_matches = [(part, feature) for (_key, part, feature), rows in groups.items() if report.named(rows[0].gauge)]
            if not plan_matches or report.kind not in ("GRR", "Kappa"):
                continue
            same_part = [feature for part, feature in plan_matches if report.subject_codes == {part}]
            if not same_part:
                if len(report.subject_codes) == 1 and all(part and _c(part) not in _PLACEHOLDERS | _DECLARED_NA for part, _ in plan_matches):
                    failures.append(f"{report.label}所写零件号{'、'.join(report.subject_codes)}与计划中该量具的零件号不符")
                else:
                    opens.append(f"{report.label}未能唯一核实对应计划行的零件号")
            else:
                checks = [self.plan_feature_match(feature, report) for feature in same_part]
                if True not in checks:
                    if None in checks:
                        opens.append(f"{report.label}所判特性未能与同零件、同量具的计划行明确对应")
                    else:
                        failures.append(f"{report.label}未写出与同零件、同量具计划行匹配的特性")
        matched_here: list[str] = []
        for (key, part, feature), rows in groups.items():
            gauge = rows[0].gauge
            counting = gauge_kind(gauge) == "counting"
            wanted = "Kappa" if counting else "GRR"
            candidates = [r for r in reports if r.kind == wanted and r.named(gauge)]
            readings = [(r, self.plan_feature_match(feature, r)) for r in candidates if r.subject_codes == {part}]
            matched = [r for r, ok in readings if ok is True]
            unknown = (
                not part
                or _c(part) in _DECLARED_NA | _PLACEHOLDERS
                or not feature
                or _c(feature) in _PLACEHOLDERS
                or bool(scanned)
                or any(not r.subject_codes or (part in r.subject_codes and len(r.subject_codes) > 1) for r in candidates)
                or any(ok is None for _, ok in readings)
            )
            ctx.record_execution()  # Match the specific part, gauge and characteristic before counting reports.
            distinct = {r.signature for r in matched}
            where = "、".join(f"第{row.row}行" for row in rows)
            label = "以评价者为对象的Kappa研究" if counting else "GR&R报告"
            owner = self.gauge_file(ctx, key, wanted, main)
            defect = ""
            if not matched and unknown:
                if self.mine(owner):
                    opens.append(f"计划表{where}“{gauge}”未能核实同一零件号及所判特性“{feature}”的报告")
                continue
            if not matched:
                near = [r for r in reports if r.named(gauge)]
                why = ""
                if near:
                    kinds = {"GRR": "GR&R报告", "Kappa": "Kappa研究", "Attr": "计数型分析（无Kappa值）", "Cgk": "Cg/Cgk报告"}
                    described = []
                    for r in near[:3]:
                        said = "“" + r.characteristic + "”" if _c(r.characteristic) else "未写"
                        described.append(f"{r.label}为{kinds[r.kind]}，零件号{'、'.join(sorted(r.subject_codes)) or '未写'}，所判特性{said}")
                    why = "（同名报告：" + "；".join(described) + "）"
                defect = f"计划表{where}“{gauge}”（零件号{rows[0].values.get('零件号') or '未写'}）没有零件号与量具名称匹配、且写明所判特性的{label}{why}"
            else:
                ids = {row.values.get("量具编号", "") for row in rows}
                inconsistent_plans = len({(r.sheet, r.values.get("序号")) for r in rows}) < len(rows) and len({original_name(r.item) for r in rows}) > 1
                if len(rows) > 1 and (inconsistent_plans or any(not v or _c(v) in _PLACEHOLDERS | _DECLARED_NA for v in ids) or (len(distinct) < len(ids) and len(matched) >= len(ids))):
                    if self.mine(owner):
                        opens.append(f"计划表{where}“{gauge}”的台数或独立研究份数未能确认")
                    continue
                if len(distinct) < len(ids):
                    if unknown and self.mine(owner):
                        opens.append(f"计划表{where}“{gauge}”的报告份数未能核实")
                        continue
                    defect = f"同类量具报告份数不足：计划表列出{len(ids)}台“{gauge}”（{where}），匹配的{label}只有{len(distinct)}份"
            if self.mine(owner):
                if defect:
                    failures.append(defect)
                else:
                    matched_here.append(f"计划表{where}“{gauge}”有{len(distinct)}份零件号、量具名称匹配且写明所判特性的{label}（{'、'.join(dict.fromkeys(r.label for r in matched))}）")
            elif defect:
                elsewhere.append(f"“{gauge}”的缺陷归《{original_name(owner)}》")
        if elsewhere:
            notes.append("计划表中" + "；".join(elsewhere) + "，不在本文件归属范围内")
        tail = ("；提示：" + "；".join(notes[:4])) if notes else ""
        if failures:
            return "NG", "；".join(failures[:5]) + ("等" if len(failures) > 5 else "") + "。" + tail
        if opens:
            raise unable("；".join(opens[:4]) + "，需人工复核。" + tail)
        done = []
        if passed:
            done.append("本文件报告" + "；".join(passed))
        done.extend(matched_here)
        if not done:
            if elsewhere or (underlying and all(report.kind == "Cgk" for report in underlying)):
                ctx.record_execution()  # The original or a scoped comparison establishes the local boundary.
                done.append("本文件没有GR&R或Kappa报告，已核对没有归本文件的量具缺陷")
            else:
                raise unable("尚不能核实本文件对应的报告或量具，不按空结果通过。" + tail)
        return "PASS", "；".join(done) + "。" + tail
