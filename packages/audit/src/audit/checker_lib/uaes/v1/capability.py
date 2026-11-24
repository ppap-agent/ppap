"""UAES U06: process capability studies, UAES v1.0 revision 3 (PCE:1–PCE:6).

`UaesCapabilityAgentR3` adds the clause methods below (`PceClauses`) to the UAES capability reader, reusing its report,
summary and directory readings. Verdicts are code over facts read from the originals (cells, the PDF text layer); no
model is asked to judge.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes import engineering
from audit.checker_lib.uaes.engineering import UaesCapabilityAgent
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet, Table


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def original_name(item: Any) -> str:
    """The name the user uploaded a same-round original under."""
    return str((getattr(item, "file_metadata", None) or {}).get("original_filename") or Path(str(item.file_url)).name)


_PGQP1 = "E12"
_CPK, _PPK = Decimal("1.33"), Decimal("1.67")
_DEFAULTS = {"目标CPK": _CPK, "目标PPK": _PPK}
_EMPTY = {"", "/", "-", "—", "na", "n/a", "无"}
# A stated planned sample count of a report (PCE:2's reminder).
_PLANNED = ("规定样本数", "规定样本量", "计划样本数", "计划样本量", "plannedsamplesize", "requiredsamplesize")
_DRAWING = ("图纸", "drawing")


def _text_count(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or not re.fullmatch(r"\d+(?:\.0+)?", str(raw).strip()):
        return None
    return Decimal(str(raw).strip())


class PceClauses(UaesCapabilityAgent):
    """Revision 3 clause methods over the declared UAES capability reader (`READER` is its module)."""

    READER: Any = None

    def finding(self, failures: list[str], errors: list[str], gaps: list[str], subject: str, passed: str) -> tuple[str, str]:
        """A clear failure first; then an index the original itself shows as a formula error, which is no value at or
        above the threshold; an index the program could not read leaves the clause open."""
        if failures:
            return "NG", "；".join(map(self.named, failures[:3])) + "。"
        if errors:
            return "NG", "工艺能力认证报告" + "；".join(map(self.named, errors[:3])) + "，原件中不是有效数值，不满足阈值要求。"
        if gaps:
            raise unable(f"检查程序未能读取全部工艺能力认证报告的{subject}：" + "；".join(map(self.named, gaps[:3])) + "，需人工复核。")
        return "PASS", passed

    # ---- PCE:1 -----------------------------------------------------------------------------------

    @rule("所有能力认证报告样本数不少于125且Cpk不低于1.33")
    def rule_1(self, ctx: RuleContext):
        R = self.READER
        failures: list[str] = []
        errors: list[str] = []
        gaps: list[str] = []
        for report in self.require_reports(ctx):
            readings = report.values.get("读数次数")
            if readings is not None and readings < 125:
                if report.bounded:
                    # A PDF page's readings grid converts with merged cells: its numbers bound the reading cells from above.
                    failures.append(R._placed(f"工艺能力认证报告{report.where}的实测结果区域（No.1–No.25列）有读数的单元格最多{readings}个，少于125", report.where))
                else:
                    failures.append(f"工艺能力认证报告{report.where}读数次数（Number of readings）为{readings}，少于125")
            cpk = report.values.get("Cpk")
            if cpk is not None and cpk < _CPK:
                failures.append(f"工艺能力认证报告{report.where}Process Capability (Cpk)为{cpk}，低于1.33")
            errors.extend(R._placed(f"{report.where}{report.errors[name]}", report.errors[name], report.where) for name in ("读数次数", "Cpk") if name in report.errors)
            if "读数次数" not in report.errors and (readings is None or (report.bounded and readings >= 125)):
                gap = report.gaps.get("读数次数", "实测结果区域的数值个数只是有读数单元格的上限，不少于125但不能确认")
                gaps.append(R._placed(f"{report.where}：" + gap, gap, report.where))
            if cpk is None and "Cpk" not in report.errors:
                gap = report.gaps.get("Cpk", "未读到Cpk")
                gaps.append(R._placed(f"{report.where}：" + gap, gap, report.where))
        return self.finding(failures, errors, gaps, "样本数与Cpk", "全部工艺能力认证报告样本数不少于125，Cpk均不低于1.33（恰为1.33按达标）。")

    # ---- PCE:2 -----------------------------------------------------------------------------------

    @rule("所有能力认证报告Ppk不低于1.67")
    def rule_2(self, ctx: RuleContext):
        R = self.READER
        failures, errors, gaps, notes = [], [], [], []
        reports = self.require_reports(ctx)
        for report in reports:
            ppk = report.values.get("Ppk")
            if "Ppk" in report.errors:
                errors.append(R._placed(f"{report.where}{report.errors['Ppk']}", report.errors["Ppk"], report.where))
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
        if status == "PASS" and self.unreadable_pdf():
            raise unable(f"{reason}另有未读页面，其Ppk未核实。{tail}")
        return status, reason + ("；" + tail if tail else "")

    def plan_reminders(self, ctx: RuleContext, reports) -> list[str]:
        """Compare only a stated plan with the same report's count. A merged PDF grid gives an upper bound, not an actual."""
        wanted = {compact(label) for label in _PLANNED}
        reminders = []
        for sheet in Table(ctx).find_sheets("能力报告", on_missing=Decision.IGNORE):
            related = [report for report in reports if str(report.where) == f"“{sheet.title}”"]
            if len(related) != 1 or related[0].bounded:
                continue
            readings = related[0].values.get("读数次数")
            if readings is None:
                continue
            rows, columns = self.extent(sheet)
            plans: dict[Any, Any] = {}
            for cells in sheet.ws.iter_rows(min_row=1, max_row=rows, max_col=columns):
                for i, cell in enumerate(cells):
                    if compact(cell.value) not in wanted:
                        continue
                    # Only its own adjacent value (including the edge of a merged label), not a different field.
                    merged = ctx.ops.get_detailed_merge_info(sheet.ws, cell.row, cell.column)
                    j = merged.max_col if merged else i + 1
                    if j >= len(cells):
                        continue
                    raw = _text_count(cells[j].value)
                    if raw is not None:
                        plans.setdefault(raw, cells[j])
            if len(plans) == 1:
                planned, cell = next(iter(plans.items()))
                if planned != readings:
                    self.cite(sheet, cell.row, cell.column)
                    reminders.append(f"提醒：“{sheet.title}”{cell.coordinate}规定样本数{planned}与本表实际读数{readings}不符")
            elif plans:
                reminders.append(f"“{sheet.title}”载明多个不同规定样本数，不能确定计划与实际读数是否相符")
        return reminders

    # ---- PCE:3 -----------------------------------------------------------------------------------

    @rule("能力值汇总清单及低于目标值行的反应计划")
    def rule_3(self, ctx: RuleContext):
        R = self.READER
        sheets = Table(ctx).find_sheets("能力汇总表", on_missing=Decision.IGNORE)
        if not sheets:
            if self.unreadable_pdf():
                raise unable("PDF原件有未读出表格或没有文字层的页面，尚不能确认是否有能力值汇总清单，需人工复核。")
            return "NG", "文件中没有能力值汇总清单（能力值汇总、能力汇总清单、CPK/PPK汇总、过程能力汇总等）。"
        checked, opens, defaulted, low_rows, errors = 0, [], 0, 0, []
        for sheet in sheets:
            section = self.section(sheet, "能力汇总区")
            columns = section.columns(optional=("能力特性", "目标CPK", "结果CPK", "目标PPK", "结果PPK", "反应计划", "汇总备注"))
            pairs = [(result, target) for result, target in (("结果CPK", "目标CPK"), ("结果PPK", "目标PPK")) if result in columns]
            if not pairs:
                raise unable(f"能力汇总“{sheet.title}”中未能定位CPK/PPK结果值列，检查程序未能读取，需人工复核。")
            headings = {compact(alias) for name in columns for alias in sheet.ctx.resource.columns[name].aliases} | {"cpk", "ppk", "规格", "特性", "公差"}
            last, _columns = self.extent(sheet)
            # Every row under the heading is read cell by cell: a merged serial column must not hide a characteristic.
            for row in range(section.resolved.header_row + 1, last + 1):
                # Raw cell values: a formula error ("#DIV/0!") is a written fact of the original, read as text.
                values = {name: sheet.ws.cell(row, column.header_col).value for name, column in columns.items()}
                texts = {name: compact(value) for name, value in values.items()}
                if any(texts.get(name, "").startswith(marker) for name in texts for marker in ("编制", "审核", "批准")):
                    break
                if any(text in headings for text in texts.values() if text):
                    continue
                # A result written as "/" states that the row has no capability value to compare.
                if all(texts.get(result, "") in _EMPTY for result, _target in pairs):
                    continue
                checked += 1
                low, low_column = [], 0
                for result, target in pairs:
                    actual_raw, wanted_raw = values.get(result), values.get(target)
                    column = columns[result].header_col
                    if texts.get(result, "") in _EMPTY:
                        continue
                    if texts[result].startswith("#"):
                        errors.append(R.Place.of(f"能力汇总“{sheet.title}”第{row}行{result}为错误值“{actual_raw}”", sheet, row, column))
                        continue  # judged below with the row's reaction plan
                    actual = R._number(actual_raw)
                    # "未载明目标值的，按 Cpk ≥ 1.33、Ppk ≥ 1.67 认定".
                    wanted = R._number(wanted_raw) if texts.get(target, "") not in _EMPTY else None
                    if wanted is None and texts.get(target, "") not in _EMPTY:
                        opens.append(R.Place.of(f"能力汇总“{sheet.title}”第{row}行{target}“{wanted_raw}”不能读为数值", sheet, row, columns[target].header_col))
                        continue
                    if wanted is None:
                        wanted, defaulted = _DEFAULTS[target], defaulted + 1
                    if actual is None:
                        opens.append(R.Place.of(f"能力汇总“{sheet.title}”第{row}行{result}“{actual_raw}”不能读为数值", sheet, row, column))
                        continue
                    if actual < wanted:
                        low.append(f"{result}{actual}低于目标{wanted}")
                        low_column = low_column or column
                planned = any(compact(values.get(name)) for name in ("反应计划", "汇总备注") if name in columns)
                if low:
                    low_rows += 1
                    if not planned:
                        self.cite(sheet, row, low_column)
                        return "NG", f"能力汇总“{sheet.title}”第{row}行{'，'.join(low)}，同行“反应计划”或“备注”没有内容。"
                elif planned and errors and str(errors[-1]).startswith(f"能力汇总“{sheet.title}”第{row}行"):
                    # A result shown as a formula error cannot be compared, but the row has its reaction plan either way.
                    errors.pop()
        opens = [*errors, *opens]
        if opens:
            raise unable("；".join(map(self.named, opens[:3])) + "，需人工复核。")
        if not checked:
            raise unable("能力汇总没有可确认的特性行，不能按空范围通过，需人工复核。")
        note = f"（{defaulted}处未载明目标值，按Cpk≥1.33、Ppk≥1.67认定）" if defaulted else ""
        if not low_rows:
            return "PASS", f"能力汇总全部{checked}行CPK/PPK均不低于目标值{note}；反应计划一项无判定对象，记不适用。"
        return "PASS", f"能力汇总共{checked}行，{low_rows}行低于目标值，均有反应计划或备注{note}。"

    # ---- PCE:4 / PCE:5 ---------------------------------------------------------------------------

    @rule("过程能力报告目录覆盖PGQP1全部特殊特性")
    def rule_4(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=False)

    @rule("过程能力报告目录覆盖PGQP1全部过程特性且研究页齐套")
    def rule_5(self, ctx: RuleContext):
        return self.pgqp_directory(ctx, process_only=True)

    def pgqp_directory(self, ctx: RuleContext, *, process_only: bool):
        R = self.READER
        directories = Table(ctx).find_sheets("能力目录", on_missing=Decision.IGNORE)
        plans = self.batch_files(_PGQP1)
        missing, unknown = [], []
        if not directories:
            if self.pdf and self.pdf_mentions("目录", "目次", "CONTENTS"):
                unknown.append("原件文字提到目录，但检查程序未能读出目录表格")
            elif self.unreadable_pdf():
                unknown.append("PDF原件有未读出表格或没有文字层的页面，尚不能确认是否有目录页")
            else:
                missing.append("过程能力分析报告没有目录页（目录、目次、CONTENTS等）")
        if not plans:
            self.need("采购件质量计划（PGQP1）")
            missing.append("同批次没有《采购件质量计划》独立文件（整份未交）")
        if missing:
            return "NG", "；".join(missing) + "。"
        if unknown:
            raise unable("；".join(unknown) + "，需人工复核。")
        if len(directories) != 1:
            raise unable(f"过程能力分析报告有多个目录页（{'、'.join(sheet.title for sheet in directories)}），尚不能确定按哪一个核对，需人工复核。")
        directory = directories[0]
        parts = {compact(value) for value in self.labelled(directory, R.capability.DIRECTORY_PART)} - _EMPTY
        if len(parts) != 1:
            raise unable(f"目录“{directory.title}”的零件号码{'未填写' if not parts else '有多个'}，检查程序不能确认同零件号的采购件质量计划，需人工复核。")
        part = next(iter(parts))
        names, process_rows = self.directory_rows(directory)
        others = []
        for item in plans:
            with self.reading(ctx, item, R.pgqp1):
                sheets = Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE)
                if not sheets:
                    others.append(f"《{original_name(item)}》（无PGQP1表）")
                    continue
                theirs = {compact(value) for sheet in sheets for value in self.labelled(sheet, R.capability.PGQP1_PART)} - _EMPTY
                if len(theirs) != 1:
                    raise unable("同批次采购件质量计划PGQP1的零件号未能唯一读取，检查程序不能确认是否同零件号，需人工复核。")
                if theirs != {part}:
                    others.append(f"《{original_name(item)}》（零件号{next(iter(theirs))}）")
                    continue
                if len(sheets) != 1:
                    raise unable("同零件号的采购件质量计划有多个PGQP1表，尚不能确定按哪一个核对，需人工复核。")
                if process_only:
                    return self.process_covered(ctx, sheets[0], directory, process_rows)
                return self.special_covered(sheets[0], directory, names)
        self.need("采购件质量计划（PGQP1）", part=part)
        return "NG", f"同批次没有零件号为{part}且含PGQP1表的《采购件质量计划》（整份未交；同批次：{'、'.join(others)}）。"

    def special_covered(self, plan: Sheet, directory: Sheet, names: set[str]):
        """PCE:4: every characteristic the PGQP1 lists in its SC/特殊特性 column is in the directory."""
        section = self.section(plan, "重要特性区")
        special = []
        for row in section.rows("特性项目", "特殊特性"):
            mark = str(row.value("特殊特性") or "").strip()
            heading = any(term in compact(value) for value in (row.value("特性项目"), mark) for term in ("characteristics", "items/项目", "sc/特殊特性", "对供应商的措施"))
            if row.is_present("特性项目") and compact(mark) not in _EMPTY and not heading:
                special.append((row.row_index, str(row.value("特性项目")).strip(), mark))
        if not special:
            raise unable("同零件号PGQP1的“SC/特殊特性”列没有内容，不能按空范围通过，需人工复核。")
        absent = [(row_index, name, mark) for row_index, name, mark in special if compact(name) not in names]
        if absent:
            row_index = absent[0][0]
            self.cite(plan, row_index, section.column("特性项目").header_col)
            listed = "、".join(f"第{r}行“{n}”" for r, n, _m in absent[:6]) + (f"等{len(absent)}项" if len(absent) > 6 else "")
            return "NG", f"PGQP1“SC/特殊特性”列列出的{len(special)}项特性中，{listed}不在过程能力分析报告目录“{directory.title}”中。"
        return "PASS", f"同零件号PGQP1“SC/特殊特性”列列出的{len(special)}项特性均在目录“{directory.title}”中。"

    def process_covered(self, ctx: RuleContext, plan: Sheet, directory: Sheet, process_rows):
        """PCE:5: the process characteristics are in the directory, and each one listed there has a study with Cpk or Ppk."""
        if process_rows is None:
            raise unable(f"目录“{directory.title}”中未能定位特别特性的“过程”列，检查程序不能确认哪些条目是过程特性，需人工复核。")
        studies = {}
        for report in self.reports(ctx):
            title = re.sub(r"^[“\"]|[”\"].*$", "", str(report.where))
            if report.values.get("Cpk") is not None or report.values.get("Ppk") is not None:
                studies[compact(title)] = report
        section = self.section(directory, "能力汇总区")
        name_column = section.column("能力特性").header_col
        unstudied = []
        for row_index in process_rows:
            name = str(Sheet(ctx, directory.ws).cell(row_index, name_column).value() or "").strip()
            if compact(name) not in studies:
                unstudied.append(f"第{row_index}行“{name}”")
        if unstudied:
            self.cite(directory, int(unstudied[0].split("第")[1].split("行")[0]), name_column)
            return "NG", f"报告未齐套：目录“{directory.title}”中标为过程特性的{'、'.join(unstudied[:5])}在报告内没有对应的能力研究页或研究页无可读能力值。"
        processes = [str(row.value("关键工艺")).strip() for row in self.section(plan, "关键工序区").rows("关键工艺", "工艺描述") if row.is_present("关键工艺")]
        if not processes:
            raise unable("同零件号PGQP1的关键工序表中没有可确认的过程，不能按空范围通过，需人工复核。")
        if process_rows:
            raise unable(f"目录“{directory.title}”有{len(process_rows)}个标为过程特性的条目且均有研究页，但PGQP1关键工序只描述工艺要求，检查程序不能逐项对应，需人工复核。")
        listed = f"同零件号PGQP1“2.Special Process关键工序”列出{len(processes)}道关键工序（{'、'.join(processes)}）"
        return "NG", f"目录“{directory.title}”没有任何标为过程特性的条目（特别特性“过程”列全空），而{listed}，其过程特性均未出现在目录中。"

    # ---- PCE:6 -----------------------------------------------------------------------------------

    @rule("图纸定量特殊特性纳入能力研究且规格一致")
    def rule_6(self, ctx: RuleContext):
        context = self.group_context
        drawings: list[Any] = []
        if context is not None:
            own = self.audit_file.audit_id
            typed = {item.audit_id for items in context.by_doc_type.values() for item in items}
            for kind, items in context.by_doc_type.items():
                if kind and any(term in compact(kind) for term in _DRAWING):
                    drawings.extend(item for item in items if item.audit_id != own)
            drawings.extend(item for item in context.by_audit_id.values() if item.audit_id != own and item.audit_id not in typed and str(item.file_url).lower().endswith(".pdf"))
            if any(not kind or any(term in compact(kind) for term in _DRAWING) for kind in context.unavailable_files.values()):
                raise unable("同批次有无法读取的原件，尚不能排除其中有图纸，需人工复核。")
        if not drawings:
            return "NA", "不适用：同批次未提交图纸，本条判定对象不存在（缺件计数在包级报告）。"
        names = "、".join(f"《{original_name(item)}》" for item in drawings[:4])
        raise unable(f"同批次有图纸或未识别类别的PDF文件{names}；图纸上的特殊特性标注与公差为图形符号，检查程序尚不能逐项读取定量特殊特性，需人工复核。")


# ---- the readers the leaf names ----


class UaesCapabilityAgentR3(PceClauses):
    READER = engineering
