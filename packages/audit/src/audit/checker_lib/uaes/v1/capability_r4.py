"""UAES v1.0 m4 candidate: process capability. The m3 size/Cpk check stays unchanged."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import fitz

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.engineering import Report
from audit.contracts import AuditInput
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet

from .capability import _CPK, _DRAWING, _EMPTY, _PGQP1, _PPK, UaesCapabilityAgentR3, compact, unable

# These are document labels, not inferred classifications of a filename or an operation.
_NAME = {"特性名称", "特性项目", "工程名", "尺寸编号", "特性编号", "items/项目", "characteristic", "特性"}
_PART = ("零件号码", "零件号", "Part No/零件号:", "Part number")
_SET = ("报告组号", "报告套号", "总报告编号", "报告套件编号", "report set id", "master report number")
_PROC = {"过程特性", "processcharacteristic", "过程特性名称"}
_SC = {"sc/特殊特性", "特殊特性", "特殊特性标识", "特殊特性标记"}
_MARK = {"√", "✓", "x", "*", "是", "yes", "sc", "cc", "kc", "k", "g", "s", "★", "●"}
_QUAL = {"定性", "目视", "纯目视", "外观目视", "qualitative", "visual"}
_TOL = ({"名义值", "公称值", "nominal", "spec"}, {"上公差", "plus", "uppertol."}, {"下公差", "minus", "lowertol."})
_SUM_COLS = {
    "name": _NAME,
    "Cpk": {"结果(cpk)", "cpk结果", "cpk实测值", "结果cpk", "实测cpk"},
    "Ppk": {"结果(ppk)", "ppk结果", "ppk实测值", "结果ppk", "实测ppk", "实测能力值"},
    "tCpk": {"目标(cpk)", "cpk目标", "cpk目标值", "目标cpk"},
    "tPpk": {"目标(ppk)", "ppk目标", "ppk目标值", "目标ppk", "客户要求能力值"},
    "plan": {"反应计划", "reactionplan"},
    "note": {"备注", "remark", "remarks"},
}


def header(sheet, aliases, required):
    """A single unambiguous row of field labels; never fill fields from the next data row."""
    rows, cols = sheet.ws.max_row, sheet.ws.max_column
    if rows > 2000 or cols > 256:
        raise unable(f"“{sheet.title}”的范围过大，不能确认已读全。")
    for cells in sheet.ws.iter_rows(max_row=rows, max_col=cols):
        labels = [(c.column, compact(c.value)) for c in cells if isinstance(c.value, str)]
        found = {key: [i for i, label in labels if label in opts] for key, opts in aliases.items()}
        if all(found[key] for key in required):
            if any(len(matches) > 1 for matches in found.values()):
                raise unable(f"“{sheet.title}”第{cells[0].row}行重复列名，不能唯一定位。")
            return cells[0].row, {key: col[0] for key, col in found.items() if col}
    return None


def metric(value, *, target=False):
    text = compact(value)
    if target:
        text = text.removeprefix("≥").removeprefix(">=")
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?", text):
        return Decimal(text)
    return None


class UaesCapabilityAgentR4(UaesCapabilityAgentR3):
    """Apply the signed m4 distinctions without altering previously installed readers."""

    def statistics_in_sheet(self, sheet):
        labels = {"label": {"descriptivestatistics", "統計値descriptivestatistics"}, "value": {"values"}}
        found = header(sheet, labels, ("label", "value"))
        if not found:
            return None
        start, cols = found
        report = Report(f"“{sheet.title}”")
        metrics = {
            "Cpk": {compact(v) for v in self.READER.capability.CPK} | {"cpk"},
            "Ppk": {compact(v) for v in self.READER.capability.PPK} | {"ppk"},
            "读数次数": {compact(v) for v in self.READER.capability.READINGS},
        }
        possible = False
        fields = {name: [] for name in metrics}
        for r in range(start + 1, sheet.ws.max_row + 1):
            label = compact(sheet.ws.cell(r, cols["label"]).value)
            for name, aliases in metrics.items():
                if label in aliases:
                    fields[name].append(sheet.ws.cell(r, cols["value"]))
            possible |= ("ppk" in label or label in {"performanceindex", "过程性能指数", "工程性能指数"}) and label not in metrics["Ppk"]
        for name, cells in fields.items():
            if len(cells) != 1:
                report.gaps[name] = f"{name}统计行{'重复' if cells else '缺失'}"
                continue
            cell = cells[0]
            if (number := metric(cell.value)) is not None:
                report.values[name] = number
            elif cell.data_type == "e":
                report.errors[name] = self.READER.Place.of(f"{name}为错误值“{cell.value}”", sheet, cell.row, cell.column)
            elif compact(cell.value) in _EMPTY and self.uncomputed(sheet, cell.row, cell.column):
                report.gaps[name] = f"{name}是没有计算结果的公式"
            elif compact(cell.value) not in _EMPTY:
                report.gaps[name] = f"{name}“{cell.value}”不是数值"
        ppk = fields["Ppk"]
        explicit_blank = len(ppk) == 1 and compact(ppk[0].value) in _EMPTY and "Ppk" not in report.gaps
        report.only_cpk = "Cpk" in report.values and not possible and (not ppk or explicit_blank)
        if report.only_cpk:
            cell = fields["Cpk"][0]
            self.cite(sheet, cell.row, cell.column)
        report.ppk_cell = (sheet, ppk[0].row, ppk[0].column) if len(ppk) == 1 else None
        return report

    def native_studies(self, ctx):
        for ws in ctx.workbook.worksheets:
            if ws.sheet_state != "visible":
                continue
            sheet = Sheet(ctx, ws)
            if report := self.statistics_in_sheet(sheet):
                yield sheet, report

    def r4_ppk_reports(self, ctx):
        if not self.pdf:
            found = [report for _, report in self.native_studies(ctx)]
            if not found:
                raise unable("原件未能定位工艺能力认证报告的统计区。")
            return found
        found = self.require_reports(ctx)
        # Read each independently identified grid page, not summary values: the
        # original reader only joins pages whose text contains *both* index labels.
        if not all(r.bounded for r in found):
            return found
        with fitz.open(str(self.audit_file.file_url)) as source:
            pages = [(p.number + 1, p.get_text(), bool(p.get_image_info())) for p in source]
        grid = [(n, t, images) for n, t, images in pages if re.search(r"\bteam\b", t, re.I) and re.search(r"\bno\.\s*1\b", t, re.I) and re.search(r"\bsample\s*1\b", t, re.I)]
        if len(grid) != len(found):
            return found
        for report, (n, t, images) in zip(found, grid):
            report.where = f"第{n}页"
            values = re.findall(r"(?im)^\s*ppk\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", t)
            report.values.pop("Ppk", None)
            if len(values) == 1:
                report.values["Ppk"] = Decimal(values[0])
            elif not images and re.search(r"(?im)^\s*cpk\s*=\s*[-+]?\d", t) and not re.search(r"\bppk\b|performance\s+index", t, re.I):
                report.only_cpk = True
            else:
                report.gaps["Ppk"] = f"第{n}页Ppk测得值未能唯一读取"
        return found

    @rule("所有能力认证报告Ppk不低于1.67")
    def rule_2(self, ctx: RuleContext):
        R = self.READER
        failures, errors, gaps, notes = [], [], [], []
        reports = self.r4_ppk_reports(ctx)
        for report in reports:
            ppk = report.values.get("Ppk")
            if ppk is not None and "Ppk" not in report.errors:
                ctx.record_execution()
                if getattr(report, "ppk_cell", None):
                    self.cite(*report.ppk_cell)
            if "Ppk" in report.errors:
                errors.append(R._placed(f"{report.where}{report.errors['Ppk']}", report.errors["Ppk"], report.where))
            elif ppk is None and getattr(report, "only_cpk", False):
                failures.append(f"工艺能力认证报告{report.where}仅列出Cpk而没有给出Ppk值")
            elif ppk is None:
                gap = report.gaps.get("Ppk", "未读到Ppk")
                gaps.append(R._placed(f"{report.where}：" + gap, gap, report.where))
            elif ppk < _PPK:
                failures.append(f"工艺能力认证报告{report.where}Performance index (Ppk)为{ppk}，低于1.67")
            readings = report.values.get("读数次数")
            if readings is not None and readings < 125:
                count = f"最多{readings}" if report.bounded else str(readings)
                notes.append(f"{report.where}实测读数{count}，少于125；样本量按PCE:1核对，和本条的Ppk比较分开列示")
        reminders = self.plan_reminders(ctx, reports)
        tail = "；".join(reminders[:3] + notes[:3])
        try:
            status, reason = self.finding(failures, errors, gaps, "Ppk", "全部已定位的工艺能力认证报告Ppk均不低于1.67（恰为1.67按达标）。")
        except GuardStop as stop:
            if tail and stop.decision == Decision.UNABLE:
                raise unable(f"{stop.message}；{tail}") from None
            raise
        if status == "PASS" and notes:
            raise unable(f"{reason.rstrip('。')}；{tail}；样本量不足是否同时影响PCE:2的结论尚待确认。")
        if status == "PASS" and any(report.values.get("读数次数") is None or report.bounded for report in reports):
            raise unable(f"{reason.rstrip('。')}；PCE:1所需的实际样本数未能核实，不能确定是否存在少于125的情形。{tail}")
        if status == "PASS" and not self.pdf:
            located = {str(report.where) for report in reports}
            for ws in ctx.workbook.worksheets:
                if ws.sheet_state == "visible" and f"“{ws.title}”" not in located and "能力认证报告" in compact(ws.title):
                    raise unable(f"另有“{ws.title}”研究页，尚未核实其Ppk，不按已读子集通过。")
        if status == "PASS" and self.unreadable_pdf():
            raise unable(f"{reason}另有未读页面，其Ppk未核实。{tail}")
        return status, reason + ("；" + tail if tail else "")

    def plan_targets(self, ctx, part):
        targets = {}
        for item in self.batch_files(_PGQP1):
            with self.reading(ctx, item, self.READER.pgqp1):
                for ws in ctx.workbook.worksheets:
                    if compact(ws.title) != "pgqp1":
                        continue
                    sheet = Sheet(ctx, ws)
                    for key in ("tCpk", "tPpk"):
                        table = header(sheet, _SUM_COLS, ("name", key))
                        if not table:
                            continue
                        other = self.identity(sheet, _PART)
                        if not part or not other:
                            raise unable("PGQP1列有能力目标，但零件号未能唯一核实。")
                        if part != other:
                            continue
                        start, cols = table
                        for r in range(start + 1, ws.max_row + 1):
                            name = compact(ws.cell(r, cols["name"]).value)
                            raw = ws.cell(r, cols[key]).value
                            if not name or compact(raw) in _EMPTY:
                                continue
                            wanted = metric(raw, target=True)
                            rec = (name, key)
                            if wanted is None or (rec in targets and targets[rec] != wanted):
                                raise unable(f"PGQP1第{r}行{key[1:]}目标未能唯一读取。")
                            targets[rec] = wanted
        return targets

    @rule("能力值汇总清单及低于目标值行的反应计划")
    def rule_3(self, ctx: RuleContext):
        checked = low_rows = 0
        opens = []
        saw_summary = False
        for ws in ctx.workbook.worksheets:
            sheet = Sheet(ctx, ws)
            if ws.sheet_state != "visible":
                continue
            # Four target/result columns themselves establish a summary, independent of its title.
            table = header(sheet, _SUM_COLS, ("name", "Cpk", "Ppk"))
            if not table:
                table = header(sheet, _SUM_COLS, ("name", "Ppk")) or header(sheet, _SUM_COLS, ("name", "Cpk"))
                if not table:
                    if "汇总" in compact(ws.title):
                        opens.append(f"“{ws.title}”疑似能力汇总，但未能核实结果列")
                    continue
            saw_summary = True
            start, cols = table
            overrides = self.plan_targets(ctx, self.identity(sheet, _PART))
            for r in range(start + 1, ws.max_row + 1):
                raw = {key: ws.cell(r, col).value for key, col in cols.items()}
                name = compact(raw.get("name"))
                if name in _EMPTY or name in _NAME:
                    continue
                checked += 1
                planned = any(compact(raw.get(key)) not in _EMPTY for key in ("plan", "note"))
                below, unknown = [], []
                for key, default in (("Cpk", _CPK), ("Ppk", _PPK)):
                    target_key = "t" + key
                    target = overrides.get((name, target_key))
                    if target is None:
                        stated = raw.get(target_key)
                        target = default if compact(stated) in _EMPTY else metric(stated, target=True)
                    actual = metric(raw.get(key))
                    if target is None or actual is None:
                        unknown.append(key)
                    else:
                        ctx.record_execution()
                        self.cite(sheet, r, cols[key])
                        if actual < target:
                            below.append((key, actual, target))
                if below and not planned:
                    k, a, t = below[0]
                    self.cite(sheet, r, cols[k])
                    return "NG", f"能力汇总“{ws.title}”第{r}行“{raw['name']}”{k}为{a}，低于目标{t}，同行没有反应计划或备注。"
                if below:
                    low_rows += 1
                if unknown:
                    opens.append(f"“{ws.title}”第{r}行{'、'.join(unknown)}目标或结果未能核实")
        if opens:
            raise unable("；".join(opens[:3]) + "，不能按达标处理。")
        if not saw_summary:
            if self.unreadable_pdf() or (self.pdf and self.pdf_mentions("汇总")):
                raise unable("PDF汇总清单结构未能完整读取。")
            return "NG", "文件中没有能力值汇总清单。"
        if not checked:
            raise unable("能力汇总没有可确认的特性行。")
        return "PASS", f"能力汇总共核对{checked}行，" + (f"{low_rows}行低于目标，均有反应计划或备注。" if low_rows else "全部达标，反应计划不适用。")

    @staticmethod
    def identities(sheet, labels, *, full_sheet=False):
        wanted = {compact(v) for v in labels}
        values = set()
        rows = sheet.ws.iter_rows() if full_sheet else sheet.ws.iter_rows(max_row=min(sheet.ws.max_row, 24), max_col=min(sheet.ws.max_column, 80))
        for row in rows:
            for c in row:
                if compact(c.value) not in wanted:
                    continue
                merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, c.row, c.column)
                end = merged.max_col if merged else c.column
                val = sheet.ws.cell(c.row, end + 1).value
                if val is not None and compact(val) not in _EMPTY:
                    values.add(str(val).strip())
        return values

    @classmethod
    def identity(cls, sheet, labels):
        values = cls.identities(sheet, labels)
        return values.pop() if len(values) == 1 else None

    def directory(self, ctx):
        aliases = {"name": _NAME - {"特性"}, "process": _PROC | {"过程"}, "nature": {"特性类型", "检验方式", "规格类型", "判定方式"}, "spec": {"规格值", "公差", "规格"}}
        dirs = []
        for ws in ctx.workbook.worksheets:
            if ws.sheet_state != "visible":
                continue

            def is_contents(v):
                return compact(v) in {"目录", "目次", "contents", "tableofcontents"}

            if is_contents(ws.title) or any(is_contents(c.value) for row in ws.iter_rows(max_row=min(4, ws.max_row), max_col=min(ws.max_column, 20)) for c in row):
                dirs.append(Sheet(ctx, ws))
        if not dirs:
            return None
        if len(dirs) != 1:
            raise unable("单份过程能力报告有多个目录，无法唯一确定各页范围。")
        sheet = dirs[0]
        h = header(sheet, aliases, ("name",))
        if not h:
            raise unable(f"目录“{sheet.title}”无可唯一定位的特性名称列。")
        start, cols = h
        process_col = cols.get("process")
        if not process_col:
            # A second-tier “过程” heading belongs to the directory, not to PGQP1.
            hits = [c.column for c in sheet.ws[start + 1] if compact(c.value) == "过程"]
            process_col = hits[0] if len(hits) == 1 else None
        rows = {}
        for r in range(start + 1, sheet.ws.max_row + 1):
            nc = sheet.ws.cell(r, cols["name"])
            name = compact(nc.value)
            if name in _EMPTY or name in _NAME:
                continue
            flag = compact(sheet.ws.cell(r, process_col).value) if process_col else ""
            qualitative = any(compact(sheet.ws.cell(r, cols[k]).value) in _QUAL for k in ("nature", "spec") if k in cols)
            rows.setdefault(name, []).append((nc.coordinate, flag, qualitative))
        if not rows:
            raise unable("目录中的特性行没有读出，不能按空集通过。")
        return self.identity(sheet, _PART), self.identity(sheet, _SET), rows

    def report_studies(self, ctx):
        studies = {}
        for sheet, report in self.native_studies(ctx):
            names = {compact(name) for name in self.identities(sheet, ("特性名称", "Description", "尺寸编号"), full_sheet=True)}
            if len(names) > 1:
                raise unable(f"“{sheet.title}”含有多个研究特性，尚未将每个实测指数对应到具体特性。")
            # The sheet name can identify a study only when the study itself
            # does not name its characteristic. One page is not two studies.
            name = next(iter(names), compact(sheet.title))
            studies.setdefault(name, []).append((sheet, report))
        return studies

    @rule("过程能力报告目录覆盖PGQP1全部特殊特性")
    def rule_4(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=False)

    @rule("过程能力报告目录覆盖PGQP1全部过程特性且研究页齐套")
    def rule_5(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=True)

    def pgqp_directory(self, ctx, *, process_only):
        plans = self.batch_files(_PGQP1)
        if not plans:
            self.need("采购件质量计划（PGQP1）")
            return "NG", "同批次没有《采购件质量计划》独立文件（整份未交）。"
        own = self.directory(ctx)
        if not own:
            if self.pdf:
                raise unable("PDF报告目录未能完整定位，不能确定是否缺页。")
            return "NG", "过程能力报告没有目录页。"
        part, family, local = own
        if not part:
            raise unable("报告目录未能唯一确定零件号码。")
        union = set(local)
        unclear = False
        possible_names = set()
        for item in self.batch_files("过程能力研究"):
            if Path(str(item.file_url)).suffix.lower() not in {".xls", ".xlsx", ".xlsm"}:
                unclear = True
                continue
            with self.reading(ctx, item, self.READER.capability):
                other = self.directory(ctx)
                if other is None:
                    # A report without a directory cannot be counted as a qualifying volume.
                    continue
                opart, ofamily, rows = other
                if not opart or (opart == part and (not family or not ofamily)):
                    possible_names.update(rows)
                elif opart == part and ofamily == family:
                    union.update(rows)
        studies = self.report_studies(ctx) if process_only else {}
        expected = None
        for item in plans:
            with self.reading(ctx, item, self.READER.pgqp1):
                for ws in ctx.workbook.worksheets:
                    if compact(ws.title) != "pgqp1":
                        continue
                    sheet = Sheet(ctx, ws)
                    p = self.identity(sheet, _PART)
                    if not p:
                        raise unable("PGQP1零件号未能唯一读取。")
                    if p != part:
                        continue
                    if expected is not None:
                        raise unable("同零件号有多个PGQP1，不能确定有效基准。")
                    expected, exhaustive = self.plan_characteristics(sheet, process_only)
                    plan_sheet = ws
        if expected is None:
            self.need("采购件质量计划（PGQP1）", part=part)
            return "NG", f"同批没有零件号{part}的独立PGQP1表（整份未交）。"
        missed = set()
        for name in expected:
            ctx.record_execution()
            if name not in union:
                missed.add(name)
        if missed:
            if unclear or missed <= possible_names:
                raise unable("目录尚缺条目，但同批报告分册的归属未核实，不能确认合并范围。")
            first = sorted(missed - possible_names)[0]
            cell = plan_sheet[expected[first][0]]
            self.cite(plan_sheet, cell.row, cell.column)
            return "NG", f"PGQP1 {expected[first][0]}“{first}”没有出现在同套已核实的目录中。"
        if not exhaustive:
            raise unable("PGQP1的已读过程参数有目录记录，但工艺描述未能拆出完整特性集合。")
        if not expected:
            raise unable("PGQP1中没有可确认的适用条目，不能按空范围通过。")
        if process_only:
            # Only the directories are pooled. A listing in a sibling is not evidence that
            # the target contains a study, and R4 did not decide cross-volume substitution.
            if set(expected) - set(local):
                raise unable("同套分册目录合并后覆盖，但部分条目仅在其他册；研究页不能据此跨册补足。")
            check = dict(expected)
            for n, items in local.items():
                if any(flag in _MARK for _, flag, _ in items):
                    check.setdefault(n, (items[0][0], all(q for _, _, q in items)))
            for name, (at, qualitative) in check.items():
                if qualitative is True or (qualitative is None and all(q for _, _, q in local[name])):
                    continue
                found = studies.get(name, [])
                if len(found) > 1:
                    raise unable(f"“{name}”对应多张研究页，未能逐一确认。")
                if not found or not any(k in found[0][1].values for k in ("Cpk", "Ppk")):
                    if self.pdf:
                        raise unable(f"“{name}”的PDF研究页未能确认。")
                    return "NG", f"报告未齐套：{at}“{name}”在本册没有对应的可读Cpk/Ppk研究页。"
        return "PASS", f"按同零件号PGQP1核对{len(expected)}项，目录覆盖。" + ("定量过程特性的本册研究页均有可读Cpk或Ppk。" if process_only else "")

    def plan_characteristics(self, sheet, process_only):
        fields = {"name": _NAME, "sc": _SC, "process": _PROC, "nature": {"特性类型", "检验方式", "规格类型", "判定方式"}}
        h = header(sheet, fields, ("process",) if process_only else ("name", "sc"))
        if not h and process_only:
            # A native process description can prove a missing item without establishing an
            # exhaustive PGQP1 list. A process/station name alone is never a characteristic.
            h = header(sheet, {"operation": {"process/工艺", "工艺"}, "description": {"description/描述", "工艺描述"}}, ("operation", "description"))
            certain = {}
            if h:
                start, cols = h
                for r in range(start + 1, sheet.ws.max_row + 1):
                    if any(compact(c.value).startswith(("3.specialrequirements", "3.特殊要求")) for c in sheet.ws[r][:3]):
                        break
                    cell = sheet.ws.cell(r, cols["description"])
                    if not sheet.ws.cell(r, cols["operation"]).value or not isinstance(cell.value, str):
                        continue
                    for clause in re.split(r"[，,；;]", unicodedata.normalize("NFKC", cell.value)):
                        explicit = re.fullmatch(
                            r"\s*([\u4e00-\u9fff]{2,16})\s*\((?:m?pa|kpa|bar|mm|°c|°f|rpm|nm)\)\s*[:：]?\s*[+-]?\d+(?:\.\d+)?(?:\s*[-~～至]+\s*[+-]?\d+(?:\.\d+)?)?\s*", clause, re.I
                        )
                        if explicit:
                            certain[compact(explicit[1])] = (cell.coordinate, False)
            if certain:
                return certain, False
        if not h:
            raise unable("PGQP1没有可逐项辨认的过程特性列或特殊特性标记列；工序名/是否做能力研究的勾选不能代替特性。")
        start, cols = h
        rows = {}
        for r in range(start + 1, sheet.ws.max_row + 1):
            vals = {key: sheet.ws.cell(r, col) for key, col in cols.items()}
            name_cell = vals.get("name")
            if any(compact(c.value).startswith(("5.productaudit", "5，产品审核", "5.产品审核")) for c in sheet.ws[r][:3]):
                break
            if process_only:
                mc = vals["process"]
                mark = compact(mc.value)
                if mark in _EMPTY or mark in _PROC or mark in {"否", "no", "普通"}:
                    continue
                if name_cell is not None and mark not in _MARK and mark != compact(name_cell.value):
                    raise unable(f"PGQP1 {mc.coordinate}的过程特性标记或名称与特性栏关系不明。")
                if mark not in _MARK:
                    name_cell = mc  # a named process characteristic, rather than a flag
            else:
                mc = vals["sc"]
                mark = compact(mc.value)
                if mark in _EMPTY or mark in _SC or mark in {"否", "no", "普通"}:
                    continue
                if mark not in _MARK:
                    raise unable(f"PGQP1 {mc.coordinate}不是可确定的特殊特性标志；规格或填色未经说明不能当作标记。")
            if not name_cell or compact(name_cell.value) in _EMPTY:
                raise unable(f"PGQP1 {mc.coordinate}对应特性未读出。")
            name = compact(name_cell.value)
            category = compact(vals["nature"].value) if "nature" in vals else ""
            qualitative = True if category in _QUAL else False if category in {"定量", "quantitative"} else None
            if name in rows and rows[name][1] != qualitative:
                raise unable(f"PGQP1 {mc.coordinate}“{name}”有不一致的特性类型。")
            rows[name] = (name_cell.coordinate, qualitative)
        return rows, True

    @staticmethod
    def component(value, *, diameter=False):
        raw = compact(value)
        if diameter:
            raw = raw.lstrip("⌀øφ")
        # Do not infer a nominal or a deviation from equal limits.
        return metric(raw)

    @staticmethod
    def compact_spec(value):
        """Only a stated nominal with both deviations; no nominal reconstructed from limits."""
        text = compact(value).lstrip("⌀øφ")
        number = r"(?:\d+(?:\.\d*)?|\.\d+)"
        sym = re.fullmatch(rf"([+-]?{number})±({number})", text)
        if sym:
            n, t = map(Decimal, sym.groups())
            return n, t, -t
        asym = re.fullmatch(rf"([+-]?{number})([+-]{number})/([+-]?{number})", text)
        if asym:
            return tuple(map(Decimal, asym.groups()))
        return None

    def research_spec(self, sheet):
        # Label and value may be separated by the merged label, never by a different field.
        components = [set(), set(), set()]
        for row in sheet.ws.iter_rows():
            for cell in row:
                label = compact(cell.value)
                combo = label in {"规格", "尺寸规格", "specification", "spec"}
                position = next((i for i, aliases in enumerate(_TOL) if label in aliases), None)
                if not combo and position is None:
                    continue
                merged = sheet.ctx.ops.get_detailed_merge_info(sheet.ws, cell.row, cell.column)
                value = sheet.ws.cell(cell.row, (merged.max_col if merged else cell.column) + 1)
                if combo and (triple := self.compact_spec(value.value)):
                    for seen, number in zip(components, triple):
                        seen.add(number)
                elif position is not None and (number := self.component(value.value, diameter=position == 0)) is not None:
                    # English MINUS names the amount subtracted by the template.
                    components[position].add(-number if label == "minus" else number)
                else:
                    continue
                self.cite(sheet, value.row, value.column)
        if any(len(seen) > 1 for seen in components):
            raise unable(f"“{sheet.title}”同一特性列有矛盾规格。")
        return tuple(next(iter(seen), None) for seen in components)

    @rule("图纸定量特殊特性纳入能力研究且规格一致")
    def rule_6(self, ctx: RuleContext):
        group = self.group_context
        drawings: list[AuditInput] = []
        if group is not None:
            typed = {item.audit_id for items in group.by_doc_type.values() for item in items}
            if any(i != self.audit_file.audit_id and i not in typed for i in group.by_audit_id) or any(
                not kind or any(t in compact(kind) for t in _DRAWING) for kind in group.unavailable_files.values()
            ):
                raise unable("同批次有未识别或无法读取的原件，尚不能排除其中有图纸。")
            for kind, items in group.by_doc_type.items():
                if any(t in compact(kind) for t in _DRAWING):
                    drawings.extend(item for item in items if item.audit_id != self.audit_file.audit_id)
        if not drawings:
            return "NA", "不适用：同批次未提交图纸，本条无判定对象（缺件仅在包级计数）。"
        # A PDF watermark or OCR numbers cannot establish an exhaustive drawing baseline. The
        # narrow structured path requires a drawing explicitly enumerating all marked characteristics.
        if len(drawings) != 1 or Path(str(drawings[0].file_url)).suffix.lower() != ".xlsx":
            raise unable("图纸的全部标注、对象和名义值/上下公差未能逐项读取，不能从目录或相同上下限推定通过。")
        if self.pdf:
            raise unable("尚未逐页读取PDF能力研究的特性与公差。")
        directories = self.directory(ctx)
        part = directories[0] if directories else None
        studies = self.report_studies(ctx)
        with self.reading(ctx, drawings[0], self.READER.capability):
            sheets = [Sheet(ctx, ws) for ws in ctx.workbook.worksheets if ws.sheet_state == "visible"]
            if len(sheets) != 1 or compact(sheets[0].title) not in {"图纸全部特殊特性", "图纸特殊特性全表", "allmarkedcharacteristics"}:
                raise unable("图纸未能识别为完整的特殊特性标注表。")
            sheet = sheets[0]
            if not part or self.identity(sheet, _PART) != part:
                raise unable("图纸和能力研究的零件号未核实一致。")
            spec_keys = ("nom", "up", "low")
            h = header(
                sheet, {"name": _NAME, "nature": {"特性类型", "类型", "定性/定量"}, "combined": {"规格", "尺寸规格", "图纸规格", "specification"}, **dict(zip(spec_keys, _TOL))}, ("name", "nature")
            )
            if not h:
                raise unable("未定位图纸特性明细的表头。")
            start, cols = h
            incomplete, missing, mismatches, count = [], [], [], 0
            for r in range(start + 1, sheet.ws.max_row + 1):
                nc = sheet.ws.cell(r, cols["name"])
                typ = compact(sheet.ws.cell(r, cols["nature"]).value)
                if compact(nc.value) in _EMPTY:
                    if typ:
                        incomplete.append(f"第{r}行特性名称未读出")
                    continue
                name = compact(nc.value)
                if typ in _QUAL:
                    continue
                if typ not in {"定量", "quantitative"}:
                    incomplete.append(f"{nc.coordinate}“{name}”未明确类型")
                    continue
                count += 1
                found = studies.get(name, [])
                if not found:
                    missing.append(f"{nc.coordinate}“{name}”")
                    continue
                if len(found) != 1 or not any(k in found[0][1].values for k in ("Ppk", "Cpk")):
                    incomplete.append(f"{nc.coordinate}“{name}”的实测研究值未能唯一核实")
                    continue
                drawn = tuple(self.component(sheet.ws.cell(r, cols[k]).value, diameter=i == 0) if k in cols else None for i, k in enumerate(spec_keys))
                if "combined" in cols and (spec := sheet.ws.cell(r, cols["combined"]).value) is not None:
                    alt = self.compact_spec(spec)
                    if alt is None or any(a is not None and a != b for a, b in zip(drawn, alt)):
                        incomplete.append(f"{nc.coordinate}规格未读出或有矛盾")
                        continue
                    drawn = alt
                observed = self.research_spec(found[0][0])
                self.cite(sheet, r, cols["name"])
                if None in drawn or None in observed:
                    incomplete.append(f"{nc.coordinate}“{name}”名义值或上下公差缺项，不把单侧上限补为零")
                else:
                    ctx.record_execution()
                    if observed != drawn:
                        mismatches.append(f"{nc.coordinate}“{name}”图纸{drawn}、研究页{observed}")
        if missing or mismatches:
            return "NG", "；".join((["没有对应研究页：" + "、".join(missing)] if missing else []) + mismatches) + "（目录记录不代替研究页，不按相同上下限替代逐项比较）。"
        if not count or incomplete:
            raise unable("；".join(incomplete[:4]) or "未找到可逐项核实的定量产品特殊特性。")
        return "PASS", f"图纸列明的{count}项定量特殊特性均有独立研究页及实测指数；名义值（去直径符号）、上公差和带符号的下公差逐项相同。"
