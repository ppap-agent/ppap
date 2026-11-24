"""UAES U18 decision methods for signed v1.0 revision 4."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl.utils.cell import get_column_letter

from audit.checker_lib.excel_agent import rule
from audit.checker_lib.uaes.v1.traceability import (
    NUMBERING,
    SIGN_FIELDS,
    UaesTraceabilityAgentR3,
    UaesTraceabilityDocxAgentR3,
    compact,
    placeholder,
)
from audit.engine.rule_engine import GuardStop, Sheet


def signature(agent: UaesTraceabilityAgentR3 | UaesTraceabilityDocxAgentR3) -> tuple[str, str]:
    """The supplier's handwritten signature and seal, read from the image of the original."""
    try:
        values, _unreadable = agent._from_legacy(lambda agent: agent.visual_observation(SIGN_FIELDS))
    except GuardStop as stop:
        return stop.status, "供应商签署栏的签字和印章未能读取：" + stop.message.rstrip("。")
    missing = [name for name, value in (("手写签字", values.get("supplier_signature")), ("供应商有效印章", values.get("supplier_seal"))) if value is False]
    if missing:
        return "NG", "代表供应商的签署栏缺少" + "、".join(missing)
    if any(values.get(field) is not True for field in SIGN_FIELDS):
        return "UNABLE", "供应商签署栏的签字或印章尚未能从原件核实，需人工复核"
    return "PASS", "代表供应商的签署栏有手写签字和供应商印章"


class UaesTraceabilityAgentR4(UaesTraceabilityAgentR3):
    """Reader for signed UAES v1.0 revision 4."""

    def signed(self) -> tuple[str, str]:
        return signature(self)

    def area_findings(self, sheet: Sheet) -> tuple[list[str], list[str], list[str], list[str]]:
        """(whole areas missing, local holes, placeholder cells, unread areas) of one titled sheet."""
        labels_of = self.READER.resources.TRACEABILITY_AREAS
        headers = {compact(label) for names in labels_of.values() for label in names}
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
            # Signing is a separate requirement; its printed fields/names are not summary data.
            footer = next((c.row_index for c in cells if re.fullmatch(r"(?:供应商)?(?:签署|签字|盖章|编制|审核|批准)(?:人|栏|处)?", self.text(c))), None)
            if footer is not None:
                last = footer - 1
                cells = [cell for cell in cells if cell.row_index <= last]
            content, labels = [], []
            for cell in cells:
                text = self.text(cell)
                if placeholder(text):
                    fillers.append(f"“{area}”{cell.ws.cell(cell.row_index, cell.col_index).coordinate}填“{cell.ws.cell(cell.row_index, cell.col_index).value}”")
                elif text not in headers and not re.fullmatch(r"追溯(?:信息|内容)?说明", text) and self.substance(text, labels_of[area]):
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
            # Only positions BETWEEN substantive entries can be local holes. A
            # sheet's styled extent, a header or a numbered template stub must
            # not extend the occupied interval. Merged cells occupy their span.
            anchors = {(cell.row_index, cell.col_index) for cell in content} | set(images)
            if not anchors:
                continue
            bins: dict[int, set[int]] = {0: set(), 1: set()}
            cover: dict[int, set[int]] = {0: set(), 1: set()}
            merged = list(getattr(sheet.ws.merged_cells, "ranges", ()))
            first_data = min(r for r, _ in anchors)
            col_headers = {cell.col_index: cell for cell in labels if cell.row_index < first_data}
            grid = len(col_headers) > 1
            for is_data, points in ((True, anchors), (False, [(c.row_index, c.col_index) for c in labels])):
                for r, c in points:
                    span = next((m for m in merged if m.min_row <= r <= m.max_row and m.min_col <= c <= m.max_col), None)
                    rows = range(max(heading.row_index, span.min_row), min(last, span.max_row) + 1) if span else (r,)
                    cols = range(span.min_col, span.max_col + 1) if span else (c,)
                    if is_data:
                        bins[0].update(rows)
                        bins[1].update(cols)
                    # In key/value forms a label column or section header is
                    # structure, not a missing value. In a grid only data count.
                    if is_data or not grid:
                        cover[0].update(rows)
                        cover[1].update(cols)
            for axis in (0, 1):
                used = bins[axis]
                for index in range(min(used) + 1, max(used)):
                    if index in cover[axis]:
                        continue
                    if axis == 0:
                        coord = (index, min(c for _, c in anchors))
                        detail = f"第{index}行"
                    else:
                        coord = (first_data, index)
                        label = col_headers.get(index)
                        detail = f"第{index}列" + (f"（{self.text(label)}）" if label else "")
                    sheet.cell(*coord).value()
                    holes.append(f"“{area}”{detail}整{'行' if axis == 0 else '列'}未填写")
        return whole, holes, fillers, unread


class UaesTraceabilityDocxAgentR4(UaesTraceabilityDocxAgentR3):
    """Inspect explicit grid boundaries in Word without turning unread content into absence."""

    @rule("供应商零件追溯表五个区域实质内容与供应商签章")
    def trace_1(self) -> tuple[str, str]:
        facts = self.native()
        resources = self.READER.resources
        facts.require_text(resources.TRACEABILITY_TITLE)
        labels_of = resources.TRACEABILITY_AREAS
        areas = {compact(name): name for name in labels_of}
        candidates = []
        for table in facts.data.get("tables", ()):
            headings = {}
            for row in range(1, table["rows"] + 1):
                for col in range(1, table["cols"] + 1):
                    address = f"{get_column_letter(col)}{row}"
                    cell = table["cells"].get(address, {})
                    if cell.get("anchor") == address and (name := areas.get(NUMBERING.sub("", compact(cell.get("value"))))):
                        if name in headings:
                            return "UNABLE", "原件同表重复列出追溯区域，尚不能确定各区域边界。"
                        headings[name] = (row, col)
            if set(headings) == set(labels_of):
                candidates.append((table, headings))
        if len(candidates) != 1:
            return super().trace_1() if not candidates else ("UNABLE", "原件存在多张完整追溯表，尚不能确定本表。")
        table, headings = candidates[0]
        order = sorted((row, col, name) for name, (row, col) in headings.items())
        if len({row for row, _, _ in order}) != len(order):
            return "UNABLE", "同行有多个追溯区标题，尚不能确定各自的行列边界。"
        whole, holes, fillers = [], [], []
        sign_footer = re.compile(r"(?:供应商)?(?:签署|签字|盖章|编制|审核|批准)(?:人|栏|处)?")
        headers = set().union(*(set(map(compact, entries)) for entries in labels_of.values()))
        unread_grid = any(item.get("state") != "known" for item in table["cells"].values()) or bool(table.get("visual_evidence"))
        for index, (start, heading_column, name) in enumerate(order):
            facts.cell(table, start, heading_column)
            end = order[index + 1][0] - 1 if index + 1 < len(order) else table["rows"]
            used: set[tuple[int, int]] = set()
            ignored: set[tuple[int, int]] = set()
            for row in range(start, end + 1):
                row_cells = [(col, table["cells"].get(f"{get_column_letter(col)}{row}", {})) for col in range(1, table["cols"] + 1)]
                if any(sign_footer.fullmatch(compact(cell.get("value"))) for _, cell in row_cells):
                    break
                for col, cell in row_cells:
                    value = compact(cell.get("value"))
                    if not value or cell.get("state") != "known":
                        continue
                    address = f"{get_column_letter(col)}{row}"
                    if placeholder(value):
                        if cell.get("anchor") == address:
                            facts.cell(table, row, col)
                            fillers.append(f"{name}{address}填“{value}”")
                    elif value in headers or value in areas or NUMBERING.sub("", value) in areas:
                        ignored.add((row, col))
                    else:
                        facts.cell(table, row, col)
                        used.add((row, col))
            if not used:
                whole.append(name)
                continue
            first_row = min(row for row, _ in used)
            # A multi-column header is a grid: only content fills a data row or column.
            # A labelled key/value layout has real label cells between values.
            grid = len({col for row, col in ignored if start < row < first_row}) > 1
            for axis, label in ((0, "行"), (1, "列")):
                occupied = {coord[axis] for coord in used}
                covered = occupied if grid else occupied | {coord[axis] for coord in ignored if coord[0] >= first_row}
                for position in range(min(occupied) + 1, max(occupied)):
                    if position not in covered:
                        holes.append(f"{name}第{position}{label}空白")
        status, signer = signature(self)
        if (unread_grid or facts.has_unread_content()) and (whole or holes):
            return ("NG", signer) if status == "NG" else ("UNABLE", "追溯表含未能读取的单元格或图片，不能据此认定区域或局部空洞。")
        problems = [
            *([f"区域整块缺失{len(whole)}处：" + "、".join(whole)] if whole else []),
            *([f"局部空洞{len(holes)}处：" + "；".join(holes)] if holes else []),
            *([f"填占位词{len(fillers)}处：" + "；".join(fillers)] if fillers else []),
            *([signer] if status == "NG" else []),
        ]
        if problems:
            return "NG", "供应商零件追溯表：" + "；".join(problems) + "。"
        if unread_grid:
            return "UNABLE", "追溯表含尚不能核对的原件内容。"
        return status, "供应商零件追溯表五个区域均有实质内容，无内部整行或整列空洞；" + signer + "。"
