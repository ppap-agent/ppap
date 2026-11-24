"""UAES v1.0 m4 candidate: signed measurement-system clarifications."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import RuleContext

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
    _REJECT,
    MsaClauses,
    PlanRow,
    UaesMsaAgentR3,
    _c,
    _number,
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

_PARAMETER = ("测量参数", "Measure parameter", "Measuring parameter", "特性", "特 性", "Denote Requirement", "标注要求")
_LIMIT = ("参数规格", "Parameter specification")
_UNIT_LABEL = ("测量单位", "测量参数单位", "measurement unit", "unit of measurement")
_LINEAR_UNIT = r"(?:mm|μm|um|毫米|微米)"


def linear_unit(value: str | None) -> str | None:
    unit = _c(value or "")
    return {"mm": "mm", "毫米": "mm", "μm": "μm", "um": "μm", "微米": "μm"}.get(unit)


@dataclass
class Report(MsaReport):
    """Keep separately anchored conclusion and metric evidence for this revision."""

    result_cells: tuple = ()
    dimension_units: frozenset[str] = frozenset()
    allowance: bool = False
    waiver: bool = False


def verdict(text: str, *, grr: bool = False) -> str:
    """accept, marginal, reject or unknown for one written conclusion (signed MSA:2, or the original Kappa acceptance words)."""
    raw = unicodedata.normalize("NFKC", text).casefold()
    words = compact(raw).replace("＆", "&")
    if not words:
        return "unknown"
    if any(term in words for term in _MARGINAL):
        return "marginal"
    if any(term in words for term in _REJECT) or re.search(r"\bng\b", raw):
        return "reject"
    if grr:
        if any(term in words for term in ("若", "如果", "待", "是否", "尚未", "□", "pending")):
            return "unknown"
        # Actual engineering conclusion, not an English word embedded in commentary.
        ending = re.split("[,;。；]", words.strip("!。."))[-1]
        if ending in _ACCEPT or re.fullmatch(r"(?:the)?(?:gage|gauge|measurementsystem)(?:is(?:acceptable|ok(?:touse)?)|canaccept(?:it)?|canbeaccepted)", ending):
            return "accept"
        if re.fullmatch(r"(?:该|本|此)?(?:测量系统|量具)?(?:可接受|可以接受|能接受|合格|通过|满足要求|符合要求)", ending):
            return "accept"
    elif any(term in words for term in _ACCEPT):
        return "accept"
    return "unknown"


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

        # Preserve literal identity, including suffixes and case. Descriptive text may
        # follow a Bosch part code in a combined "product name / part number" column.
        def part_ids(value: str) -> set[str]:
            if not value or _c(value) in _PLACEHOLDERS | _DECLARED_NA:
                return set()
            codes = {token for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) if re.match(r"[Ff][A-Za-z0-9]{9,}", token)}
            return codes or {value.strip()}

        row_parts = [part_ids(part_text) for _where, part_text, _ch, _val, _g in found]
        written = set().union(*row_parts)
        items = [(where, feature, value, gauge) for where, _p, feature, value, gauge in found]
        if len(written) > 1:
            if not own_part:
                raise unable(f"{source}列有多个零件号，未能确定本零件号，不能选择适用特性行。")
            items = [item for item, ids in zip(items, row_parts) if ids == {own_part}]
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
        elif any(word == "kappa" for word in words) or "kappa" in _c(ws.title):
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
        measurement_grid = grid.copy()
        unit_places = self.label_cells(grid, _UNIT_LABEL)
        fields = {compact(label) for label in (*_PARAMETER, *_LIMIT)}
        for (row, col), text in grid.items():
            heading = re.fullmatch(rf"(.+?)\s*\(({_LINEAR_UNIT})\)(\s*[:：].*)?", unicodedata.normalize("NFKC", text), re.I | re.S)
            if heading and compact(heading[1]) in fields:
                measurement_grid[row, col] = heading[1] + (heading[3] or "")
                unit_places.append((row, col, heading[2]))
        parameter_cells = self.label_cells(measurement_grid, _PARAMETER)
        limit_cells = self.label_cells(measurement_grid, _LIMIT)
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
        for (row, column), cell in sorted(grid.items()):
            word = _c(cell)
            if kind == "GRR":
                if _CONCLUSION_LABEL.match(word):
                    # Read the actual result field, not a rubric or an unselected
                    # checkbox further along the same row.
                    inline = _text(cell).partition(":")[2].strip()
                    right = self.right(grid, row, column)
                    result = inline or (right[0][1] if right else "")
                    if result and not result.startswith("□"):
                        report.conclusions.append(result)
                        report.result_cells += ((ws, row, column if inline else right[0][0]),)
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
                if _CONCLUSION_LABEL.match(word):
                    values = [value for _col, value in self.right(grid, row, column)]
                    split = re.split(r"[:：]", _text(cell), maxsplit=1)
                    if len(split) == 2 and split[1].strip() and not _CONCLUSION_LABEL.match(_c(split[1])):
                        values.insert(0, split[1].strip())
                    report.conclusions.extend(values)
                elif word == "kappa":
                    report.kappas.extend(n for _col, value in self.right(grid, row, column) if (n := _number(value)) is not None and re.fullmatch(r"-?\d+(\.\d+)?", _c(value)))
                elif "kappa" in word and any(term in word for term in ("总体", "整体", "overall")):
                    report.overall.extend(n for _col, value in self.right(grid, row, column) if (n := _number(value)) is not None)
        if kind == "GRR":
            for labels, attr in [(("客户批准", "客户认可", "整改说明", "整改措施"), "allowance"), (("公差带豁免说明",), "waiver")]:
                for r, c, value in self.label_cells(grid, labels):
                    v = _c(value)
                    if (
                        v in _PLACEHOLDERS | _DECLARED_NA
                        or not v
                        or any(term in v for term in ("未批准", "未认可", "未豁免", "不批准", "不认可", "不适用", "待", "无", "否", "pending", "notapproved", "notapplicable"))
                    ):
                        continue
                    if value.endswith((":", "：")) or self.right(grid, r, c, gap=0) and _c(value) in ("备注", "日期", "责任人"):
                        continue
                    setattr(report, attr, True)
                    report.result_cells += ((ws, r, c),)
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
            pages = [(page.number + 1, [line.strip() for line in page.get_text().splitlines() if line.strip()]) for page in document if page.number + 1 not in scanned]
        for number, lines in pages:
            if not lines:
                continue
            text = " ".join(lines)
            head = " ".join(lines[:3])

            def after(label: str, offset: int = 1) -> str:
                index = next((i for i, line in enumerate(lines) if _c(line).rstrip(":") == _c(label)), None)
                return lines[index + offset] if index is not None and index + offset < len(lines) else ""

            if "重复性及再现性分析报告" in head:
                report = Report("GRR", item, f"第{number}页", title=head, text=_c(text))
                report.parts = set(_PART.findall(text.upper()))
                subject = after("零件代号")
                report.subject_codes = frozenset([subject]) if subject else frozenset()
                report.names = [after("量具名称")] if after("量具名称") else []
                report.numbers = [after("量具编号")] if after("量具编号") else []
                report.characteristic = after("特 性") or after("特性")
                report.studied = (report.characteristic,) if report.characteristic else ()
                report.conclusions = [after("结论")] if after("结论") else []
                for index, line in enumerate(lines):
                    if _c(line) in ("%r&r=", "%grr=") and index + 1 < len(lines) and re.fullmatch(r"\d+(\.\d+)?%", _c(lines[index + 1])):
                        report.grr = Decimal(_c(lines[index + 1]).rstrip("%"))
                if values := re.findall(r"NDC\s*=\s*(\d+(?:\.\d+)?)\s*$", "\n".join(lines), re.M):
                    report.ndc = Decimal(values[-1])
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
                for index, line in enumerate(lines):
                    word = _c(line)
                    if word == "kappa":
                        for value in lines[index + 1 : index + 4]:
                            if re.fullmatch(r"-?\d+(\.\d+)?", _c(value)):
                                report.kappas.append(Decimal(_c(value)))
                    elif word.startswith("conclude") or word in {"good", "acceptable", "marginal", "unacceptable", "poor", "bad", "ok", "ng", "pass", "fail"}:
                        rest = _text(line)[8:].strip() if word.startswith("conclude") else _text(line)
                        if rest:
                            report.conclusions.append(rest)
                if report.kappas:
                    report.kind = "Kappa"
        return found

    def check_report(self, report: MsaReport) -> tuple[str, str] | None:
        """A report's own failure (NG), or an UNABLE reason, or None when its conclusion and indices pass."""
        if not isinstance(report, Report):
            return super().check_report(report)
        verdicts = [verdict(value, grr=report.kind == "GRR") for value in report.conclusions]
        if report.kind == "Cgk":
            return None
        if not report.conclusions:
            return "UNABLE", f"{report.label}未读到结论"
        if "marginal" in verdicts:
            return "NG", f"{report.label}结论为有条件接受/临界类“{'；'.join(report.conclusions)}”，须整改后复评，按不通过处理"
        if "reject" in verdicts:
            return "NG", f"{report.label}结论“{'；'.join(report.conclusions)}”不是可接受"
        if "unknown" in verdicts:
            odd = [value for value, v in zip(report.conclusions, verdicts) if v == "unknown"]
            return "UNABLE", f"{report.label}结论“{'；'.join(odd)}”不能确定是否为可接受的写法"
        if report.kind == "GRR":
            if report.grr is None:
                return "UNABLE", f"{report.label}结论可接受，但未能读出%GRR，底层指标是否达标尚未核实"
            if report.grr > 30:
                return "NG", f"{report.label}%GRR为{report.grr.normalize()}%，大于30%，即使结论写可接受也按数据失真判不通过"
            if report.grr > 10 and not report.allowance:
                return "NG", f"{report.label}%GRR为{report.grr.normalize()}%（10%～30%），未附客户批准或整改说明"
            if report.ndc is None:
                return "UNABLE", f"{report.label}未能读出ndc，底层指标是否达标尚未核实"
            if report.ndc < 5 and not report.waiver:
                return "NG", f"{report.label}ndc为{report.ndc.normalize()}，小于5且无公差带豁免说明"
        if report.kind == "Kappa":
            values = report.overall or report.kappas
            if not values:
                return "UNABLE", f"{report.label}未能读出Kappa值，底层指标是否达标尚未核实"
            if low := [value for value in values if value < Decimal("0.75")]:
                return "NG", f"{report.label}Kappa为{'、'.join(str(v) for v in low)}，低于0.75，即使结论写可接受也按数据失真判不通过"
        return None

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
        if written == expected:
            return True
        # A plan can give endpoints while the report separates the nominal and
        # tolerance. Only a complete interval, not a shared nominal, proves identity.
        number = r"(\d+(?:\.\d+)?)"
        interval = re.fullmatch(number + r"[-~～–]" + number + rf"({_LINEAR_UNIT})?", expected)
        nominal = re.fullmatch(number + rf"({_LINEAR_UNIT})?", written)
        if interval and nominal and len(report.limits) == 1:
            lim = _c(report.limits[0]).strip("()").replace("−", "-")
            sym = re.fullmatch("±" + number + rf"({_LINEAR_UNIT})?", lim)
            asym = re.fullmatch(r"\+?" + number + "/-" + number + rf"({_LINEAR_UNIT})?", lim)
            if tol := sym or asym:
                report_units = set(report.dimension_units) if isinstance(report, Report) else set()
                report_units.update(unit for text in (nominal[2], tol[3] if asym else tol[2]) if (unit := linear_unit(text)))
                # Do not import the expected unit into an unlabelled report.
                expected_unit = linear_unit(interval[3])
                if len(report_units) != 1 or not expected_unit or "unresolved" in report_units:
                    return None
                observed_unit = next(iter(report_units))
                scale = {"mm": Decimal(1000), "μm": Decimal(1)}
                high = Decimal(tol[1])
                low = Decimal(tol[2]) if asym else high
                n = Decimal(nominal[1])
                return ((n - low) * scale[observed_unit], (n + high) * scale[observed_unit]) == (Decimal(interval[1]) * scale[expected_unit], Decimal(interval[2]) * scale[expected_unit])
        return MsaClauses.characteristic_match(feature, feature, report, unique_name=True, unique_requirement=True)

    @rule("计划量具与GRR/Kappa报告匹配及报告结论与指标")
    def rule_2(self, ctx: RuleContext):
        plan, main, scanned = self.suite_plan(ctx)
        underlying = self.reports_of(ctx, self.audit_file)
        own = [report for report in underlying if report.kind != "Cgk"]
        failures, opens, notes, passed = [], [], [], []
        for report in own:
            for cell in (*report.study_cells, *(report.result_cells if isinstance(report, Report) else ())):
                self.cite(*cell)
            result = self.check_report(report)
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
        if suite_level:
            if not plan:
                if scanned:
                    opens.append(f"同批次《{'》《'.join(scanned)}》有没有文字层的页面，尚不能确认是否有测量系统分析计划表")
                elif len(self.suite()) == 1:
                    failures.append("文件内及同批次文件中没有测量系统分析计划表")
            if not any(report.kind in ("GRR", "Kappa") for report in reports):
                if any(self.scanned(item) for item in self.suite()):
                    opens.append("套件包含扫描页，不能仅根据文本提取断定缺少GR&R或Kappa报告")
                else:
                    failures.append("文件内及同批次文件中没有重复性及再现性（GR&R）报告或Kappa报告")
        if not plan and len(self.suite()) > 1:
            opens.append("未能确认套件计划表" if scanned else "同批次缺测量系统分析计划表；缺件归属及各子文件结论尚未裁定")
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
