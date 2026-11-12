"""UAES MSA plan coverage, gauge pairing and report conclusions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.rule_engine import Decision, RuleContext, Sheet, Table

from .excel import UaesExcelAgent, compact, unable
from .resources import combine, control_plan, pgqp1
from .resources import msa as resources

_COUNTING = ("人工", "目视", "目测", "通止规", "塞规", "塞尺")
_GO_NO_GO = ("通止规", "塞规", "塞尺")
_HUMAN = ("人工", "目视", "目测")
_PLACEHOLDERS = {"", "/", "-", "—", "na", "n/a", "无"}
_MSA, _PGQP1, _CP = "测量系统分析评价", "E12", "控制计划"


def _identity(value) -> str:
    """A gauge number or part number as written; a spreadsheet number keeps no trailing .0."""
    return re.sub(r"^(\d+)\.0+$", r"\1", compact(value))


@dataclass
class Report:
    kind: str
    where: str
    names: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    parts: set[str] = field(default_factory=set)
    conclusions: list[str] = field(default_factory=list)

    def accepted(self) -> bool:
        if self.kind == "Kappa":
            return bool(self.conclusions) and all(compact(value) == "acceptable" for value in self.conclusions)
        return bool(self.conclusions) and all(compact(value).rstrip("!。.").endswith(compact("测量系统可以接受")) for value in self.conclusions)


class UaesMsaAgent(UaesExcelAgent):
    DOCUMENT_MODULE = resources
    EXTRA_RESOURCE = combine(resources.DOCUMENT, pgqp1.DOCUMENT, control_plan.DOCUMENT)

    def plan_rows(self, ctx: RuleContext) -> list[tuple[str, str, str]]:
        """(gauge name, gauge number, place) of each numbered plan row in the workbook being read."""
        rows = []
        for sheet in Table(ctx).find_sheets("MSA计划表", on_missing=Decision.IGNORE):
            for row in self.section(sheet, "MSA计划区").rows("序号", "量具名称", "量具编号"):
                if row.is_present("序号"):
                    rows.append((str(row.value("量具名称") or "").strip(), str(row.value("量具编号") or "").strip(), f"“{sheet.title}”第{row.row_index}行"))
        return rows

    def reports(self, ctx: RuleContext) -> list[Report]:
        """Every GR&R and Kappa report in this file and in the same-round MSA files."""
        found = self.reports_in(ctx)
        for item in self.batch_files(_MSA):
            with self.reading(ctx, item, resources):
                found.extend(self.reports_in(ctx))
        return found

    def reports_in(self, ctx: RuleContext) -> list[Report]:
        found = []
        for kind, name in (("GRR", "GRR报告"), ("Kappa", "Kappa报告")):
            for sheet in Table(ctx).find_sheets(name, on_missing=Decision.IGNORE):
                report = Report(kind, f"“{sheet.title}”")
                report.names = {_identity(value) for value in self.labelled(sheet, resources.GAUGE_NAME)} - _PLACEHOLDERS
                report.numbers = {_identity(value) for value in self.labelled(sheet, resources.GAUGE_NUMBER)} - _PLACEHOLDERS
                report.parts = {_identity(value) for value in self.labelled(sheet, resources.PART_NUMBER)} - _PLACEHOLDERS
                report.conclusions = self.labelled(sheet, resources.GRR_VERDICT) if kind == "GRR" else self.kappa_conclusions(sheet)
                found.append(report)
        return found

    @staticmethod
    def kappa_conclusions(sheet: Sheet) -> list[str]:
        """Every appraiser's conclusion; a 计数型MSA sheet without appraiser rows states one closing conclusion."""
        wanted = {label.casefold() for label in resources.KAPPA_CONCLUSION}
        values = []
        rows, columns = UaesExcelAgent.extent(sheet)
        for cell in sheet.scan_non_empty(row_min=1, row_max=rows, col_min=1, col_max=columns):
            # The appraiser rows read "Conclusion"; the colon-ended "Conclusion:" is the summary line.
            if "".join(str(cell.value()).split()).casefold() in wanted:
                values.extend(str(other.value()).strip() for other in sheet.scan_non_empty(row_min=cell.row_index, row_max=cell.row_index, col_min=cell.col_index + 1, col_max=columns))
        return values or UaesExcelAgent.labelled(sheet, resources.ATTRIBUTE_CONCLUSION)

    def precheck(self):
        """Preparation facts still come from the snapshot reader."""
        from .native import UaesSourceAgent
        from .precheck_l1 import precheck_msa

        return precheck_msa(UaesSourceAgent(self.audit_file, self.rule, group_context=self.group_context))

    @rule("MSA计划逐编号行除备注外内容完整")
    def rule_1(self, ctx: RuleContext):
        plans = Table(ctx).find_sheets("MSA计划表", on_missing=Decision.IGNORE)
        if not plans:
            if unread := self.unread_pages():
                raise unable(f"原件第{'、'.join(map(str, unread))}页没有文字层，尚不能确认是否包含MSA计划表，需人工复核。")
            if self.pdf_mentions("测量系统分析计划", "MSA计划"):
                raise unable("原件文字提到MSA计划，但未能读出计划表格，需人工复核。")
            # The clause applies only "如果包含" an MSA plan table.
            return "NA", "文件不包含测量系统分析计划或MSA计划表，本条前提不成立。"
        checked = 0
        for sheet in plans:
            section = self.section(sheet, "MSA计划区")
            header, serial_column = section.resolved.header_row, section.column("序号").header_col
            # Every heading except 备注 must be filled on each business row, the serial first.
            rows, columns = self.extent(sheet)
            headings = [cell for cell in sheet.scan_non_empty(row_min=header, row_max=header, col_min=1, col_max=columns) if "备注" not in compact(cell.value())]
            names = [compact(cell.value()) for cell in headings]
            for row in range(header + 1, rows + 1):
                cells = [sheet.cell(row, heading.col_index) for heading in headings]
                values = [compact(cell.value()) for cell in cells]
                if any(compact(sheet.cell(row, serial_column).value()).startswith(marker) for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                    break
                if values == names or not any(values):
                    if any(self.uncomputed(sheet, row, heading.col_index) for heading in headings):
                        raise unable(f"MSA计划“{sheet.title}”第{row}行含没有计算结果的公式，需人工复核。")
                    continue
                checked += 1
                for heading, value in zip(headings, values):
                    if not value:
                        if self.uncomputed(sheet, row, heading.col_index):
                            raise unable(f"MSA计划“{sheet.title}”第{row}行{compact(heading.value())}是没有计算结果的公式，需人工复核。")
                        return "NG", f"MSA计划“{sheet.title}”第{row}行{compact(heading.value())}为空。"
        if not checked:
            raise unable("MSA计划没有可确认的业务行，不能按空范围通过。")
        return "PASS", "已逐行核实MSA计划序号及除备注外的全部字段。"

    @rule("计划每行量具类型编号与全部GRR/Kappa结论")
    def rule_2(self, ctx: RuleContext):
        plan = self.plan_rows(ctx)
        if not plan:
            plans = []
            for item in self.batch_files(_MSA):
                with self.reading(ctx, item, resources):
                    rows = self.plan_rows(ctx)
                if rows:
                    plans.append(rows)
            if not plans:
                if self.untabled() or self.unread_pages():
                    raise unable("PDF原件未能读出表格，尚不能确认是否有测量系统分析计划表，需人工复核。")
                return "NG", "文件及同批次原件中没有测量系统分析计划表。"
            if len({tuple((name, _identity(number)) for name, number, _where in rows) for rows in plans}) > 1:
                raise unable("同批次有多份内容不同的测量系统分析计划，尚不能确定按哪一份核对，需人工复核。")
            plan = plans[0]
        reports = self.reports(ctx)
        if not reports:
            if self.untabled() or self.unread_pages():
                raise unable("PDF原件未能读出表格，尚不能确认是否有重复性及再现性报告或Kappa报告，需人工复核。")
            return "NG", "文件及同批次原件中没有重复性及再现性报告或Kappa报告。"
        for name, number, where in plan:
            kind = "Kappa" if any(term in name for term in _COUNTING) else "GRR"
            if _identity(number) in _PLACEHOLDERS:
                return "NG", f"MSA计划{where}{name}量具编号为“{number}”，无法对应编号相同的{kind}报告。"
            if not any(report.kind == kind and _identity(number) in report.numbers for report in reports):
                return "NG", f"MSA计划{where}{name}（编号{number}）没有编号相同的{kind}报告。"
        for report in reports:
            if not report.conclusions:
                raise unable(f"{report.kind}报告{report.where}的结论尚未读取到，需人工复核。")
            if not report.accepted():
                expected = "Acceptable" if report.kind == "Kappa" else "测量系统可以接受"
                return "NG", f"{report.kind}报告{report.where}的结论“{'；'.join(report.conclusions)}”不是“{expected}”。"
        return "PASS", "MSA计划全部量具已按类型对应编号相同的报告，全部报告结论可接受。"

    def kappas(self, ctx: RuleContext) -> list[Report]:
        return [report for report in self.reports(ctx) if report.kind == "Kappa"]

    def only(self, kind: str, label: str):
        files = self.batch_files(kind)
        if not files:
            return None
        if len(files) > 1:
            raise unable(f"同批次有多份{label}，尚不能确定按哪一份核对。")
        return files[0]

    @rule("PGQP1重要特性计数型量具同编号Kappa")
    def rule_3(self, ctx: RuleContext):
        plan = self.only(_PGQP1, "采购件质量计划")
        if plan is None:
            return "NG", "同批次没有包含PGQP1的采购件质量计划。"
        gauges = []
        with self.reading(ctx, plan, pgqp1):
            for sheet in Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                for row in self.section(sheet, "重要特性区").rows("特性项目", "特殊特性", "测量设备"):
                    if row.is_present("特殊特性") and any(term in str(row.value("测量设备") or "") for term in _GO_NO_GO):
                        gauges.append((str(row.value("特性项目") or "").strip(), str(row.value("测量设备")).strip()))
            if not Table(ctx).find_sheets("PGQP1", on_missing=Decision.IGNORE):
                return "NG", "同批次采购件质量计划中没有PGQP1表。"
        if not gauges:
            return "PASS", "PGQP1重要特性中没有通止规、塞规、塞尺类计数型量具。"
        if not self.kappas(ctx):
            return "NG", "PGQP1重要特性使用计数型量具，但同批次没有Kappa报告。"
        # PGQP1 names the gauge type only; the clause pairs a Kappa study by its gauge number.
        item, gauge = gauges[0]
        raise unable(f"PGQP1重要特性“{item}”使用{gauge}，但PGQP1没有写量具编号，尚不能对应同编号的Kappa报告，需人工复核。")

    @rule("CP出货或GP12人工检测同名或综合检具Kappa")
    def rule_4(self, ctx: RuleContext):
        plan = self.only(_CP, "控制计划")
        if plan is None:
            return "NG", "同批次没有控制计划。"
        manual = []
        with self.reading(ctx, plan, control_plan):
            for sheet in Table(ctx).find_sheets("控制计划", on_missing=Decision.IGNORE):
                for row in self.section(sheet, "控制计划区").rows("工序名称", "评价测量技术"):
                    operation, method = str(row.value("工序名称") or ""), str(row.value("评价测量技术") or "")
                    if any(term in operation for term in ("出货检验", "GP12")) and any(term in method for term in _HUMAN):
                        # AIAG control plans name the gauge in the evaluation/measurement technique column.
                        manual.append((operation.strip(), method.strip(), f"“{sheet.title}”第{row.row_index}行"))
        if not manual:
            return "PASS", "控制计划出货检验/GP12工序没有人工、目视、目测检测。"
        kappas = self.kappas(ctx)
        if not kappas:
            return "NG", "控制计划出货检验/GP12有人为检测，但同批次没有Kappa报告。"
        for operation, method, where in manual:
            if any(compact("综合检具") in report.names or any(name and name in compact(method) for name in report.names) for report in kappas):
                continue
            if any(not report.names for report in kappas):
                raise unable(f"控制计划{where}“{operation}”采用{method}，同批次Kappa报告未写量具名称，尚不能确认量具名称相同或为综合检具，需人工复核。")
            return "NG", f"控制计划{where}“{operation}”采用{method}，同批次没有量具名称相同或为综合检具的Kappa报告。"
        return "PASS", "控制计划出货检验/GP12的人为检测均有量具名称相同或为综合检具的Kappa报告。"
