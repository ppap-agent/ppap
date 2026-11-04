"""NIO table facts keep original cells and a unique current-round counterpart."""

from __future__ import annotations

import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto
from audit.checkers.v3.excel_agent import ExcelAgent
from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, _identity, _label
from audit.document_tools import snapshot
from audit.engine.rule_engine import Decision, GuardStop


def known_workbook(workbook, source):
    if source and Path(source).suffix.casefold() in {".xlsx", ".xlsm"}:
        for table in snapshot(source)["tables"]:
            for address, cell in table["cells"].items():
                if cell["state"] in {"unknown_formula_cache", "unknown_excel_error"}:
                    target = workbook[table["id"]][address]
                    target.value, target.data_type = "#N/A", "e"
    return workbook


def selected_sheets(workbook, keyword):
    sheets = [sheet for sheet in workbook.worksheets if keyword.casefold() in sheet.title.casefold() and not any(token in sheet.title.casefold() for token in _NON_DATA_SHEETS)]
    if not sheets:
        sheets = [
            sheet
            for sheet in workbook.worksheets
            if not any(token in sheet.title.casefold() for token in _NON_DATA_SHEETS)
            and any(keyword.casefold() in str(cell.value or "").casefold() for cells in sheet.iter_rows(max_row=min(sheet.max_row, 5), max_col=min(sheet.max_column, 20)) for cell in cells)
        ]
    if len(sheets) != 1:
        raise GuardStop(Decision.UNABLE, f"无法唯一确定{keyword}表，需确认原件。")
    return sheets


def _header(value, aliases):
    actual = "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()
    for alias in aliases:
        wanted = "".join(unicodedata.normalize("NFKC", alias).split()).casefold()
        if actual == wanted:
            return True
        # Bilingual headers may add an English translation, but a different
        # Chinese field such as 产品/工序规范/公差 is never the 产品 column.
        if wanted and not re.search(r"[a-z]", wanted) and "".join(re.findall(r"[\u4e00-\u9fff]", actual)) == "".join(re.findall(r"[\u4e00-\u9fff]", wanted)) and re.search(r"[\u4e00-\u9fff]", wanted):
            return True
    return False


def table_header(ctx, aliases, required, version, sheets):
    best = None
    for sheet in sheets:
        if sheet.max_row * sheet.max_column > 250000:
            raise GuardStop(Decision.UNABLE, "原件数据超出本次有界读取范围。")
        for row in range(1, min(sheet.max_row, 40) + 1):
            columns = {}
            span = 1
            for name, names in aliases.items():
                matches = []
                for column in range(1, min(sheet.max_column, 80) + 1):
                    first = sheet.cell(row, column).value
                    second = sheet.cell(row + 1, column).value if row < sheet.max_row else None
                    if _header(first, names):
                        matches.append((column, 1))
                    elif _header(str(first or "") + str(second or ""), names):
                        matches.append((column, 2))
                if len(matches) == 1:
                    columns[name] = matches[0][0]
                    span = max(span, matches[0][1])
            if best is None or len(columns) > len(best[3]):
                best = (sheet, row, span, columns)
    if best is None or not best[3]:
        raise GuardStop(Decision.UNABLE, "未能可靠定位数据表头。")
    sheet, header, span, columns = best
    for column in columns.values():
        ctx.note_cell(sheet.title, header, column, file_version_id=version)
    if not set(required).issubset(columns):
        missing = set(required) - set(columns)
        if any(cell.data_type in {"e", "f"} for cells in sheet.iter_rows(max_row=min(sheet.max_row, 40), max_col=min(sheet.max_column, 80)) for cell in cells):
            raise GuardStop(Decision.UNABLE, "表头范围存在不可读取内容，不能确认必需列是否缺失。")
        raise GuardStop(Decision.NG, "已定位数据表，但缺少必需列：" + "、".join(aliases[name][0] for name in sorted(missing)))
    return sheet, header, span, columns


def table_rows(ctx, workbook, aliases, required, version, sheets):
    sheet, header, span, columns = table_header(ctx, aliases, required, version, sheets)
    result = []
    for row in range(header + span, sheet.max_row + 1):
        item = {"__row_number": str(row), "__sheet_name": sheet.title}
        for name, column in columns.items():
            merged = ctx.ops.get_detailed_merge_info(sheet, row, column)
            if merged and merged.min_col != merged.max_col and column != merged.min_col:
                raise GuardStop(Decision.UNABLE, "数据横向合并跨越多个语义列，需确认真实列归属。")
            cell = sheet.cell(merged.min_row if merged else row, merged.min_col if merged else column)
            ctx.note_cell(sheet.title, cell.row, cell.column, file_version_id=version)
            if cell.data_type in {"e", "f"}:
                raise GuardStop(Decision.UNABLE, "数据单元格无法可靠读取，需确认原件。")
            item[name] = str(cell.value).strip() if cell.value is not None else ""
        values = [name for name in columns if item[name]]
        if values and not all(_header(item[name], aliases[name]) for name in values):
            result.append(item)
    if not result:
        raise GuardStop(Decision.UNABLE, "没有本条可核实的数据行，不能按空范围通过。")
    ctx.note_scan(yielded=len(result), finished=True)
    return result


class NioTableAgent(ExcelAgent):
    CLAUSE_BOUND_CHECKS = True
    SHEET_KEYWORD = ""

    def _load_workbook(self):
        workbook = super()._load_workbook()
        if getattr(self, "_known_workbook", None) is not workbook:
            known_workbook(workbook, getattr(workbook, "_ppap_source_path", None) or self._source_path)
            self._known_workbook = workbook
        return workbook

    def _sheets(self, ctx):
        return selected_sheets(ctx.workbook, self.SHEET_KEYWORD)

    def _data(self, ctx, aliases, required=None):
        return table_rows(ctx, ctx.workbook, aliases, tuple(aliases) if required is None else required, self.audit_file.audit_id, self._sheets(ctx))

    def _columns(self, ctx, aliases):
        table_header(ctx, aliases, tuple(aliases), self.audit_file.audit_id, self._sheets(ctx))
        return self._done(ctx)

    def _field(self, ctx, *labels):
        return _label(ctx, ctx.workbook, labels, self.audit_file.audit_id, sheets=self._sheets(ctx))

    @staticmethod
    def _done(ctx, applicable=True):
        ctx.mark_scope_complete()
        return ("PASS", "") if applicable else ("NA", "已核对全部数据行，本条条件不适用。")

    @contextmanager
    def _related(self, ctx, kind, keyword):
        candidates = self.get_related_files(kind) if self.group_context else []
        if len(candidates) != 1:
            raise GuardStop(Decision.UNABLE, f"缺少{keyword}或存在多份候选，需确认本轮对端。")
        related = candidates[0]
        workbook = _load_workbook_auto(related.file_url)
        try:
            known_workbook(workbook, getattr(workbook, "_ppap_source_path", None) or related.file_url)
            sheets = selected_sheets(workbook, keyword)
            ctx.bind_workbook(workbook, related.audit_id)
            for source, version, scope in [(ctx.workbook, self.audit_file.audit_id, self._sheets(ctx)), (workbook, related.audit_id, sheets)]:
                customer = _label(ctx, source, ("客户", "客户名称", "Customer", "Customer Name"), version, sheets=scope)
                if customer and _identity(customer) not in {"蔚来", "nio"}:
                    raise GuardStop(Decision.UNABLE, "原件客户与本轮蔚来模式冲突。")
            for labels, required in [(("零件号", "零件编号"), True), (("版本", "版本号", "修订版次"), False)]:
                own = self._field(ctx, *labels)
                other = _label(ctx, workbook, labels, related.audit_id, sheets=sheets)
                if (required or own or other) and (not own or not other or _identity(own) != _identity(other)):
                    raise GuardStop(Decision.UNABLE, f"关联原件{labels[0]}缺失或不一致，尚不能配对。")
            yield workbook, related.audit_id, sheets
        finally:
            workbook.close()

    def _paired_data(self, ctx, kind, keyword, aliases):
        with self._related(ctx, kind, keyword) as (workbook, version, sheets):
            return table_rows(ctx, workbook, aliases, tuple(aliases), version, sheets)
