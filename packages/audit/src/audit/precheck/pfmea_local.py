"""Verified local PFMEA checks shared by NIO and Li Auto source clauses.

This mixin owns no loading, routing, customer identity or cross-file selection.
The existing ExcelAgent supplies the audit input and RuleContext lifecycle.
"""

from __future__ import annotations

import re

from audit.checker_lib.nio.cross_file_extractors import extract_rows_from_workbook, normalize_text
from audit.checker_lib.nio.excel_agent import rule
from audit.contracts import AuditInput
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Decision, GuardStop, RuleContext

_FIELDS = {
    "step": ["过程步骤"],
    "characteristic": ["特殊特性", "特殊特性分类"],
    "function": ["过程项功能"],
    "effect": ["失效影响"],
    "severity": ["严重度", "严重度S"],
    "controls": ["现行探测控制"],
    "detection": ["探测度", "探测度D"],
}
_DIMENSIONS = ("内部", "客户工厂", "最终用户")
_NON_DATA_SHEETS = ("准则", "準則", "评分", "評分", "criteria", "数据库")


def _identity(value) -> str:
    # Preserve punctuation: P12-3 is not P123, and step 10.1 is not 101.
    return "".join(normalize_text(value).split()).casefold()


def pair_identity(value) -> str | None:
    """A pairing key must be actual source data, not a placeholder or a label."""
    normalized = _identity(value)
    placeholders = {"", "/", "-", "--", "—", "–", "na", "n/a", "n.a.", "none", "null", "tbd", "xx", "xxx", "xxxx", "无", "待填", "待定", "待确认", "未填写"}
    labels = {
        _identity(label)
        for label in (
            "零件号",
            "零件编号",
            "零件名称",
            "物料编号",
            "物料名称",
            "理想汽车零件号",
            "客户零件号",
            "Part number",
            "Part No",
            "Part No.",
            "版本",
            "版本号",
            "工程更改版本",
            "Revision",
            "版次",
            "修订版次",
            "项目",
            "项目名称",
            "车型项目",
            "项目代号",
            "UAES项目编号",
            "客户",
            "客户名称",
            "Customer",
            "Customer Name",
            "供应商名称",
            "供应商代码",
            "日期",
        )
    }
    return None if normalized in placeholders or normalized.rstrip(":：") in labels else normalized


def _label(ctx, workbook, labels, version, *, sheets=None):
    values = set()
    keys = {_identity(label) for label in labels}
    for sheet in sheets if sheets is not None else workbook.worksheets:
        if any(token in sheet.title.casefold() for token in ("履历", *_NON_DATA_SHEETS)):
            continue
        for cells in sheet.iter_rows(max_row=min(sheet.max_row, 40), max_col=min(sheet.max_column, 80)):
            for cell in cells:
                if _identity(cell.value).rstrip(":：") not in keys:
                    continue
                ctx.note_cell(sheet.title, cell.row, cell.column, file_version_id=version)
                right = cell.column + 1
                for merged in sheet.merged_cells.ranges:
                    if cell.coordinate in merged:
                        right = merged.max_col + 1
                        break
                value = sheet.cell(cell.row, right)
                if value.data_type in {"e", "f"}:
                    raise GuardStop(Decision.UNABLE, "身份或日期字段无法可靠读取，需确认原件。")
                ctx.note_cell(sheet.title, value.row, value.column, file_version_id=version)
                if normalize_text(value.value):
                    values.add(normalize_text(value.value))
    if len({_identity(value) for value in values}) > 1:
        raise GuardStop(Decision.UNABLE, "同一文件包含冲突的身份或日期字段，需确认。")
    return next(iter(values), "")


def _rows(ctx, workbook, aliases, required, version, *, sheets=None):
    def note(sheet, row, column):
        if workbook[sheet].cell(row, column).data_type in {"e", "f"}:
            raise GuardStop(Decision.UNABLE, "数据单元格无法可靠读取，需确认原件。")
        ctx.note_cell(sheet, row, column, file_version_id=version)

    rows = extract_rows_from_workbook(workbook, aliases, required_fields=required, note_cell=note, sheets=sheets)
    if not rows:
        raise GuardStop(Decision.UNABLE, "未取得可核实的数据行；不能判为通过。")
    ctx.note_scan(yielded=len(rows), finished=True)
    return rows


def _dimensions(value):
    text = normalize_text(value)
    matches = list(re.finditer(r"(内部|客户工厂|最终用户)\s*[:：]", text))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end() : end].strip()
        if match.group(1) in result or not content:
            return {}
        result[match.group(1)] = content
    return result if set(result) == set(_DIMENSIONS) else {}


def _step_key(value):
    text = normalize_text(value)
    match = re.fullmatch(r"((?:OP)?\d+(?:[.-]\d+)*)\s+(.+)", text, re.IGNORECASE)
    return (_identity(match.group(1)), _identity(match.group(2))) if match else ("", _identity(text))


def _score(value):
    text = normalize_text(value)
    return int(float(text)) if re.fullmatch(r"(?:[1-9]|10)(?:\.0+)?", text) else None


class PfmeaLocalChecks:
    audit_file: AuditInput

    @staticmethod
    def _target_sheets(ctx):
        sheets = [sheet for sheet in ctx.workbook.worksheets if "pfmea" in sheet.title.casefold() and not any(token in sheet.title.casefold() for token in _NON_DATA_SHEETS)]
        if len(sheets) != 1:
            raise GuardStop(Decision.UNABLE, "无法唯一确定PFMEA表。")
        return sheets

    def _target_label(self, ctx, labels):
        return _label(ctx, ctx.workbook, labels, self.audit_file.audit_id, sheets=self._target_sheets(ctx))

    def _target_rows(self, ctx, *fields):
        return _rows(ctx, ctx.workbook, {field: _FIELDS[field] for field in fields}, fields, self.audit_file.audit_id, sheets=self._target_sheets(ctx))

    @staticmethod
    def _pass(ctx):
        ctx.mark_scope_complete()
        return "PASS", ""

    @rule("表头字段必填", clause_id="1")
    def rule_1(self, ctx: RuleContext):
        for labels in [("公司名称",), ("车型项目",), ("零件名称",), ("零件号", "零件编号"), ("制造地址",), ("PFMEA开始日期",), ("PFMEA修订日期",), ("跨职能团队",)]:
            if not self._target_label(ctx, labels):
                return "NG", f"缺少或未填写{labels[0]}。"
        return self._pass(ctx)

    @rule("开始及修订日期对应履历", clause_id="2")
    def rule_2(self, ctx: RuleContext):
        sheets = [sheet for sheet in ctx.workbook.worksheets if "变更履历" in sheet.title]
        if len(sheets) != 1:
            return "UNABLE", "无法唯一确定变更履历表。"
        sheet = sheets[0]
        dates = []
        headers = [(cell.row, cell.column) for cells in sheet.iter_rows() for cell in cells if normalize_text(cell.value) == "修订日期"]
        if len(headers) != 1:
            return "UNABLE", "无法唯一确定履历的修订日期列。"
        row, column = headers[0]
        for index in range(row + 1, sheet.max_row + 1):
            value = sheet.cell(index, column).value
            if not normalize_text(value):
                continue
            ctx.note_cell(sheet.title, index, column)
            date = normalize_excel_date(value)
            if date is None:
                return "UNABLE", "履历日期无法可靠解析。"
            dates.append(date)
        if not dates:
            return "UNABLE", "履历中没有有效日期。"
        start = normalize_excel_date(self._target_label(ctx, ("PFMEA开始日期",)))
        revision = normalize_excel_date(self._target_label(ctx, ("PFMEA修订日期",)))
        if start is None or revision is None:
            return "UNABLE", "PFMEA日期无法可靠解析。"
        return self._pass(ctx) if (start, revision) == (dates[0], dates[-1]) else ("NG", "PFMEA开始/修订日期与履历首/末有效日期不一致。")

    @rule("过程项功能三个维度", clause_id="3")
    def rule_3(self, ctx: RuleContext):
        for row in self._target_rows(ctx, "step", "function"):
            if not _dimensions(row["function"]):
                return "NG", "过程项功能缺少内部、客户工厂或最终用户的独立内容。"
        return self._pass(ctx)

    @rule("严重度取三维度最大值", clause_id="4")
    def rule_4(self, ctx: RuleContext):
        for row in self._target_rows(ctx, "step", "effect", "severity"):
            dimensions = _dimensions(row["effect"])
            scores = []
            for content in dimensions.values():
                match = re.fullmatch(r".+(?:\(\s*(?:S\s*[:=]?\s*)?([1-9]|10)\s*\)|\bS\s*[:=]\s*([1-9]|10))\s*[;；。]?", content, re.IGNORECASE)
                if not match:
                    return "UNABLE", "失效影响分值格式尚不能可靠解析，需核对各维度的独立分值。"
                scores.append(int(match.group(1) or match.group(2)))
            if not scores or _score(row["severity"]) != max(scores):
                return "NG", "严重度不是三个失效影响维度分值的最大值。"
        return self._pass(ctx)

    @rule("探测度取全部措施最小值", clause_id="5")
    def rule_5(self, ctx: RuleContext):
        for row in self._target_rows(ctx, "step", "controls", "detection"):
            text = normalize_text(row["controls"])
            matches = list(re.finditer(r"([^()]+)\(([1-9]|10)\)", text))
            if not matches or "".join(match.group(0) for match in matches).replace(" ", "") != text.replace(" ", ""):
                return "UNABLE", "尚不能将每个探测措施与分值可靠对应，需核对原件。"
            if _score(row["detection"]) != min(int(match.group(2)) for match in matches):
                return "NG", "探测度不是现行探测措施分值的最小值。"
        return self._pass(ctx)
