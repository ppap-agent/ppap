"""UAES U18 candidate: part traceability statement, UAES v1.0 revision 3 (TRACE:1).

A leaf carries only checker.py and resources.py and may not import the UAES implementation. resources.py
declares the UAES traceability readers as carriers; at run time `Audit` extends the declared reader class
with the revision 3 clause method below, so the reader's sheet and PDF reading is reused. Verdicts are decided
in code; the image model only reads the supplier's signature and seal.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from audit.checker_lib.agents import AgentAudit
from audit.checker_lib.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, RuleContext, Sheet
from resources import RESOURCE

# TRACE:1 names these placeholder words; a hollow box or a black block is unfilled as well.
PLACEHOLDER_WORDS = (
    "见附表", "见附件", "待定", "TBD", "NA", "N/A", "—", "-", "/", "同上", "见上", "略", "*", "待补", "后续提供", "XX", "XXXX", "123456",
    "#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!",
)  # fmt: skip
UNFILLED_MARKS = "□☐█■▇▆"
NUMBERING = re.compile(r"^[\(（]?\d+[\.．、\)）]?")

SIGN_FIELDS = {
    "supplier_signature": "文件中代表供应商的签署栏（编制、审核、批准、供应商签字等）有手写签字或电子手写签名；打印体姓名、空白不算。",
    "supplier_seal": "文件中有供应商的有效印章（公章、受控章或电子签章）；只有“盖章处”文字或空白盖章栏不算。",
}


def compact(value: Any) -> str:
    """Comparable text: width-folded, spaces removed, case-folded, trailing colon dropped (as the UAES reader)."""
    return "".join(unicodedata.normalize("NFKC", "" if value is None else str(value)).split()).casefold().rstrip(":：")


PLACEHOLDERS = {compact(word) for word in PLACEHOLDER_WORDS}


def unable(message: str) -> GuardStop:
    return GuardStop(Decision.UNABLE, message)


def placeholder(text: str) -> bool:
    """A cell that writes only a placeholder word or unfilled marks."""
    own = compact(text)
    return own in PLACEHOLDERS or (bool(own) and all(char in UNFILLED_MARKS for char in own))


class TraceClauses:
    """TRACE:1 over the declared UAES traceability reader (`READER` is its module)."""

    READER: Any = None

    def signed(self) -> tuple[str, str]:
        """The supplier's handwritten signature and seal, read from the image of the original."""
        try:
            values, _unreadable = self._from_legacy(lambda agent: agent.visual_observation(SIGN_FIELDS))
        except GuardStop as stop:
            return stop.status, "供应商签署栏的签字和印章未能读取：" + stop.message.rstrip("。")
        missing = [name for name, value in (("手写签字", values.get("supplier_signature")), ("供应商有效印章", values.get("supplier_seal"))) if value is False]
        if missing:
            return "NG", "代表供应商的签署栏缺少" + "、".join(missing)
        if any(value is None for value in values.values()):
            return "UNABLE", "供应商签署栏的签字或印章尚未能从原件核实，需人工复核"
        return "PASS", "代表供应商的签署栏有手写签字和供应商印章"

    def pictured(self, sheet: Sheet) -> set[tuple[int, int]]:
        return {(anchor._from.row + 1, anchor._from.col + 1) for image in getattr(sheet.ws, "_images", []) if (anchor := getattr(image, "anchor", None)) is not None and hasattr(anchor, "_from")}

    def area_findings(self, sheet: Sheet) -> tuple[list[str], list[str], list[str], list[str]]:
        """(whole areas missing, local holes, placeholder cells, unread areas) of one titled sheet."""
        labels_of = self.READER.resources.TRACEABILITY_AREAS
        headings: dict[str, Any] = {}
        for cell in self.cells(sheet):
            name = NUMBERING.sub("", self.text(cell))
            for area in labels_of:
                if name == compact(area) and area not in headings:
                    headings[area] = cell
        whole = [f"“{area}”（原件中没有该区域）" for area in labels_of if area not in headings]
        holes: list[str] = []
        fillers: list[str] = []
        unread: list[str] = []
        pictures = self.pictured(sheet)
        order = sorted(headings.items(), key=lambda item: item[1].row_index)
        for index, (area, heading) in enumerate(order):
            heading.value()
            last = order[index + 1][1].row_index - 1 if index + 1 < len(order) else self.extent(sheet)[0]
            cells = [cell for cell in self.cells(sheet, row_min=heading.row_index, row_max=last) if (cell.row_index, cell.col_index) != (heading.row_index, heading.col_index)]
            content, labels = [], []
            for cell in cells:
                text = self.text(cell)
                if placeholder(text):
                    fillers.append(f"“{area}”{cell.ws.cell(cell.row_index, cell.col_index).coordinate}填“{cell.ws.cell(cell.row_index, cell.col_index).value}”")
                elif self.substance(text, labels_of[area]):
                    content.append(cell)
                else:
                    labels.append(cell)
            images = [place for place in pictures if heading.row_index <= place[0] <= last]
            where = f"“{sheet.title}”第{heading.row_index}至{last}行"
            if not content:
                if images:
                    unread.append(f"“{area}”区域（{where}）只有图片，没有文字内容，图片是否为追溯内容需人工确认")
                    continue
                if Path(str(self.audit_file.file_url)).suffix.lower() in {".xls", ".pdf"} and (reason := self.unseen()):
                    unread.append(f"“{area}”区域（{where}）没有读到文字内容；{reason}")
                    continue
                whole.append(f"“{area}”（{where}只有模板标签，实质内容为0）")
                continue
            for cell in content:
                cell.value()
            filled_rows = {cell.row_index for cell in content} | {row for row, _column in images}
            # The area's column header: its first row with template labels and nothing filled. Below it, a labelled
            # row with nothing filled is a row hole, and a header column with nothing filled is a column hole.
            label_rows = sorted({cell.row_index for cell in labels} - filled_rows)
            if not label_rows:
                continue
            header = label_rows[0]
            below = [row for row in filled_rows if row > header]
            if not below:
                continue
            for row in label_rows[1:]:
                texts = "、".join(str(cell.ws.cell(cell.row_index, cell.col_index).value).strip() for cell in labels if cell.row_index == row)
                holes.append(f"“{area}”第{row}行（“{texts}”）整行未填写")
            filled_columns = {cell.col_index for cell in content if cell.row_index > header} | {column for row, column in images if row > header}
            for cell in labels:
                if cell.row_index == header and cell.col_index not in filled_columns:
                    holes.append(f"“{area}”列“{str(cell.ws.cell(cell.row_index, cell.col_index).value).strip()}”整列未填写")
        return whole, holes, fillers, unread

    @rule("供应商零件追溯表五个区域实质内容与供应商签章")
    def trace_1(self, ctx: RuleContext):
        wanted = compact(self.READER.resources.TRACEABILITY_TITLE)
        titled = [sheet for sheet in self.sheets(ctx) if wanted in compact(sheet.title) or any(wanted in self.text(cell) for cell in self.cells(sheet, row_max=5))]
        if not titled:
            return self.absent("“供应商零件追溯”表")
        findings = [self.area_findings(sheet) for sheet in titled]
        # Several titled sheets: the most complete one is the table.
        whole, holes, fillers, unread = min(findings, key=lambda found: (len(found[0]), len(found[1]) + len(found[2]), len(found[3])))
        sign_status, sign_text = self.signed()
        problems = []
        if whole:
            problems.append(f"区域整块缺失{len(whole)}处（致命）：" + "；".join(whole))
        if holes:
            problems.append(f"局部空洞{len(holes)}处（主要）：" + "；".join(holes[:8]))
        if fillers:
            problems.append(f"填占位词{len(fillers)}处：" + "；".join(fillers[:8]))
        if sign_status == "NG":
            problems.append(sign_text)
        if problems:
            note = f"。另：{sign_text}" if sign_status in {"UNABLE", "ERR"} else ""
            return "NG", "供应商零件追溯表" + "。".join(problems) + note + "。"
        if unread:
            raise unable("供应商零件追溯表" + "；".join(unread) + "，需人工复核。")
        if sign_status == "ERR":
            raise GuardStop(Decision.ERR, "供应商零件追溯表五个区域均有实质内容；" + sign_text + "。")
        if sign_status == "UNABLE":
            raise unable("供应商零件追溯表五个区域均有实质内容；" + sign_text + "。")
        return "PASS", "供应商零件追溯表五个区域均有实质内容，没有整行或整列空洞和占位词；" + sign_text + "。"


class TraceDocxClauses:
    """TRACE:1 over the declared UAES Word traceability reader: areas by their fields, signature and seal by image."""

    READER: Any = None

    @rule("供应商零件追溯表五个区域实质内容与供应商签章")
    def trace_1(self):
        facts = self.native()
        resources = self.READER.resources
        facts.require_text(resources.TRACEABILITY_TITLE)
        fillers = []
        for name in resources.TRACEABILITY_AREAS:
            value = facts.field((name,), title=(resources.TRACEABILITY_TITLE,))
            if placeholder(str(value)):
                fillers.append(f"“{name}”填“{value}”")
        status, sign = TraceClauses.signed(self)
        if status == "NG" or fillers:
            return "NG", "供应商零件追溯表" + "；".join([*([f"填占位词：{'、'.join(fillers)}"] if fillers else []), *([sign] if status == "NG" else [])]) + "。"
        return "UNABLE", "Word原件读到五个区域的内容，但未核对整行或整列空洞；" + sign + "，需人工复核。"

    def _from_legacy(self, read: Any) -> Any:
        return read(self)


class Audit(AgentAudit):
    """Runs the declared UAES reader extended with this leaf's clause method (see the module docstring)."""

    CLAUSES: dict[str, type] = {"UaesTraceabilityAgent": TraceClauses, "UaesTraceabilityDocxAgent": TraceDocxClauses}

    def _implementation(self) -> Any:
        if self._agent is None and self._failure is None:
            reader = super()._implementation()
            if reader is None:
                return None
            base = type(reader)
            close = getattr(reader, "close", None)
            if callable(close):
                close()
            try:
                extended = type(base.__name__ + "R3", (self.CLAUSES[base.__name__], base), {"READER": sys.modules[base.__module__]})
                self._agent = extended(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._agent, self._failure = None, exc
                raise
        return super()._implementation()


def create_audit(audit_file, rule, group_context=None):
    return Audit(audit_file, rule, group_context, resource=RESOURCE)
