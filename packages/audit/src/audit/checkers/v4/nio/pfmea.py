"""N02 uses exact process identities and CC/SC evidence from a verified pair."""

from __future__ import annotations

import re
from contextlib import contextmanager

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto, extract_rows_from_workbook, normalize_text
from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.checkers.v3.pfmea_shared import PFD_FIELD_ALIASES
from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from audit.kinds import PpapDocumentType

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


class NioPfmeaAgent(ExcelAgent):
    DOC_TYPE = PpapDocumentType.PFMEA
    CLAUSE_BOUND_CHECKS = True

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

    @rule("NIO表头字段必填", clause_id="1")
    def rule_1(self, ctx: RuleContext):
        for labels in [("公司名称",), ("车型项目",), ("零件名称",), ("零件号", "零件编号"), ("制造地址",), ("PFMEA开始日期",), ("PFMEA修订日期",), ("跨职能团队",)]:
            if not self._target_label(ctx, labels):
                return "NG", f"缺少或未填写{labels[0]}。"
        return self._pass(ctx)

    @rule("NIO开始及修订日期对应履历", clause_id="2")
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

    @rule("NIO过程项功能三个维度", clause_id="3")
    def rule_3(self, ctx: RuleContext):
        for row in self._target_rows(ctx, "step", "function"):
            if not _dimensions(row["function"]):
                return "NG", "过程项功能缺少内部、客户工厂或最终用户的独立内容。"
        return self._pass(ctx)

    @rule("NIO严重度取三维度最大值", clause_id="4")
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

    @rule("NIO探测度取全部措施最小值", clause_id="5")
    def rule_5(self, ctx: RuleContext):
        for row in self._target_rows(ctx, "step", "controls", "detection"):
            text = normalize_text(row["controls"])
            matches = list(re.finditer(r"([^()]+)\(([1-9]|10)\)", text))
            if not matches or "".join(match.group(0) for match in matches).replace(" ", "") != text.replace(" ", ""):
                return "UNABLE", "尚不能将每个探测措施与分值可靠对应，需核对原件。"
            if _score(row["detection"]) != min(int(match.group(2)) for match in matches):
                return "NG", "探测度不是现行探测措施分值的最小值。"
        return self._pass(ctx)

    @contextmanager
    def _pfd(self, ctx):
        if not self.group_context:
            raise GuardStop(Decision.UNABLE, "没有本轮关联上下文。")
        candidates = self.get_related_files(PpapDocumentType.PROCESS_FLOW_CHART)
        if len(candidates) != 1:
            raise GuardStop(Decision.UNABLE, "缺少过程流程图，或存在多份候选，需确认本轮对端。")
        related = candidates[0]
        workbook = _load_workbook_auto(related.file_url)
        try:
            sheets = [sheet for sheet in workbook.worksheets if "过程流程图" in sheet.title and not any(token in sheet.title for token in _NON_DATA_SHEETS)]
            if len(sheets) != 1:
                raise GuardStop(Decision.UNABLE, "无法唯一确定对端过程流程图表。")
            ctx.bind_workbook(workbook, related.audit_id)
            part = self._target_label(ctx, ("零件号", "零件编号"))
            other = _label(ctx, workbook, ("零件号", "零件编号"), related.audit_id, sheets=sheets)
            if not part or not other or _identity(part) != _identity(other):
                raise GuardStop(Decision.UNABLE, "两份原件的零件号缺失或不一致，尚不能配对。")
            for source, version, scope in [(ctx.workbook, self.audit_file.audit_id, self._target_sheets(ctx)), (workbook, related.audit_id, sheets)]:
                customer = _label(ctx, source, ("客户", "客户名称", "Customer", "Customer Name"), version, sheets=scope)
                if customer and _identity(customer) not in {"蔚来", "nio"}:
                    raise GuardStop(Decision.UNABLE, "原件客户标识与本轮蔚来模式冲突，需另开任务或确认。")
            revision = self._target_label(ctx, ("版本", "版本号", "修订版次"))
            other_revision = _label(ctx, workbook, ("版本", "版本号", "修订版次"), related.audit_id, sheets=sheets)
            if (revision or other_revision) and (not revision or not other_revision or _identity(revision) != _identity(other_revision)):
                raise GuardStop(Decision.UNABLE, "关联文件版本不一致或缺失，尚不能配对。")
            yield workbook, related.audit_id, sheets
        finally:
            workbook.close()

    @rule("NIO流程逐项一致", clause_id="6")
    def rule_6(self, ctx: RuleContext):
        target = self._target_rows(ctx, "step")
        steps = {_step_key(row["step"]) for row in target if row["step"]}
        with self._pfd(ctx) as (workbook, version, sheets):
            for row in _rows(ctx, workbook, PFD_FIELD_ALIASES, ("operation_no", "operation_name"), version, sheets=sheets):
                combined = " ".join([row.get("operation_no", ""), row.get("operation_name", "")]).strip()
                if combined and (_identity(row.get("operation_no", "")), _identity(row.get("operation_name", ""))) not in steps:
                    return "NG", f"PFD工序“{combined}”未在PFMEA过程步骤中体现。"
        return self._pass(ctx)

    @rule("NIO特性必须同为CC或SC", clause_id="7")
    def rule_7(self, ctx: RuleContext):
        target = self._target_rows(ctx, "step", "characteristic")
        with self._pfd(ctx) as (workbook, version, sheets):
            for row in _rows(ctx, workbook, PFD_FIELD_ALIASES, ("operation_name", "product_char_type", "process_char_type"), version, sheets=sheets):
                expected = {normalize_text(row.get(key, "")).upper() for key in ("product_char_type", "process_char_type")} & {"CC", "SC"}
                if not expected:
                    continue
                name = _identity(row["operation_name"])
                number = _identity(row.get("operation_no", ""))
                matches = [entry for entry in target if _step_key(entry["step"]) == (number, name)] if number else []
                if not matches:
                    by_name = [entry for entry in target if _step_key(entry["step"])[1] == name]
                    identities = {_step_key(entry["step"]) for entry in by_name}
                    if len(identities) > 1:
                        return "UNABLE", "同名工序有多个编号，尚不能确定特性对端。"
                    matches = by_name if not number or identities == {("", name)} else []
                if not matches or not any(normalize_text(entry["characteristic"]).upper() in {"CC", "SC"} for entry in matches):
                    return "NG", f"PFD工序“{row['operation_name']}”的CC/SC特性未在PFMEA对应步骤体现。"
        return self._pass(ctx)
