"""Li Auto source facts shared by the customer-specific checks.

This augments existing native/Fluent readers. It does not interpret uploaded
rules, generate checks, calculate formulas, or replace the Engine result model.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from audit.checkers.v3.excel_agent import ExcelAgent
from audit.document_tools import snapshot
from audit.engine.rule_engine import Decision, GuardStop, Table


def compact(value):
    return "".join(str(value if value is not None else "").split())


def number(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        parsed = Decimal(str(value).strip())
        if not parsed.is_finite():
            raise ValueError
        return parsed
    except (InvalidOperation, ValueError):
        raise GuardStop(Decision.UNABLE, "数值无法可靠读取，不能完成本条核对。") from None


class NativeFacts(ExcelAgent):
    """Preserve unknown native cells while reusing the existing Excel agents."""

    def _load_workbook(self):
        workbook = super()._load_workbook()
        if getattr(self, "_native_facts_workbook", None) is workbook:
            return workbook
        self.native_tables = {}
        source = getattr(workbook, "_ppap_source_path", None) or self._source_path
        if source and Path(source).suffix.lower() in {".xlsx", ".xlsm"}:
            native = snapshot(source)
            self.native_tables = {table["id"]: table for table in native["tables"]}
            for table in native["tables"]:
                for address, cell in table["cells"].items():
                    if cell["state"] in {"unknown_formula_cache", "unknown_excel_error"}:
                        workbook[table["id"]][address] = "#N/A"
                        workbook[table["id"]][address].data_type = "e"
        self._native_facts_workbook = workbook
        return workbook

    def _finalize_rule_result(self, *, status, message, ctx):
        if ctx.has_unresolved():
            return "UNABLE", "存在未算出的公式或不可读取单元格，本条结果尚未核实。"
        if str(status).upper() == "PASS" and ctx.finished_scans_yielded_nothing():
            return "UNABLE", "本次数据范围为空，不能按空范围通过。"
        return super()._finalize_rule_result(status=status, message=message, ctx=ctx)


def data_rows(ctx, sheet_name, section_name, required, *, trigger=None):
    result = []
    for sheet in Table(ctx).find_sheets(sheet_name):
        section = sheet.section(section_name)
        columns = section.columns(*required)
        result.extend(row for row in section.rows_from(columns) if trigger is None or row.is_present(trigger))
    if not result:
        raise GuardStop(Decision.UNABLE, "没有本条可核实的数据行，不能按空范围通过。")
    return result


def precheck_fact(agent, item_id, check, *, code="W02"):
    """Run one explicit source-structure check with its own read evidence."""
    ctx = None
    status, message = "unknown", "本项尚未核实。"
    try:
        ctx = agent.rule_ctx(agent._load_workbook())
        result = check(ctx)
        decision, message = result if isinstance(result, tuple) else ("PASS", "已核实原件结构。")
        if ctx.has_unresolved():
            decision, message = "UNABLE", "存在不可读取的单元格，本项尚未核实。"
        status = {"PASS": "satisfied", "NG": "defect", "ERR": "error", "UNABLE": "unknown", "NA": "not_checked"}.get(decision, "unknown")
    except GuardStop as exc:
        status = {"NG": "defect", "ERR": "error"}.get(exc.status, "unknown")
        message = exc.message
    except Exception as exc:
        status, message = "error", f"原件结构读取失败：{type(exc).__name__}。"
    evidence = agent._evidence_from_reads(ctx) if ctx is not None else None
    if status == "satisfied" and (not evidence or not evidence.sources):
        status, message = "unknown", "尚未取得原件定位，不能声明本项已核实。"
    return {
        "id": item_id,
        "source_id": item_id,
        "status": status,
        "code": code if status == "defect" else None,
        "message": message,
        "certified": False,
        "critical": False,
        "evidence": evidence.model_dump(mode="json") if evidence else None,
    }


def sheet_present(ctx, sheet_name):
    sheet = Table(ctx).sheet(sheet_name)
    sheet.cell(1, 1).value()


def columns_present(ctx, sheet_name, section_name, names):
    section = Table(ctx).sheet(sheet_name).section(section_name)
    for column in section.columns(*names).values():
        ctx.note_cell(section.resolved.ws.title, section.resolved.header_row, column.header_col)


def identity_values(ctx, sheet_name, labels, *, before_row):
    """Read header identity only, excluding identically named business columns."""
    from audit.engine.rule_engine import Sheet

    ws = Table(ctx).sheet(sheet_name).ws
    sheet = Sheet(ctx, ws)
    for label in labels:
        values = []
        for cells in ws.iter_rows(max_row=max(1, before_row - 1)):
            for raw in cells:
                if compact(raw.value).rstrip(":：") != compact(label):
                    continue
                sheet.cell(raw.row, raw.column).value()
                merged = ctx.ops.get_detailed_merge_info(ws, raw.row, raw.column)
                right = merged.max_col + 1 if merged else raw.column + 1
                values.append(sheet.cell(raw.row, right).value())
        if not values:
            raise GuardStop(Decision.NG, f"表头缺少{label}字段。")
        if any(not compact(value) for value in values):
            raise GuardStop(Decision.NG, f"表头{label}字段未填写。")
        if len({compact(value) for value in values}) > 1:
            raise GuardStop(Decision.UNABLE, f"表头{label}字段取值冲突。")
