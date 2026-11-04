"""N02 uses exact process identities and CC/SC evidence from a verified pair."""

from __future__ import annotations

from contextlib import contextmanager

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto, normalize_text
from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.checkers.v3.pfmea_shared import PFD_FIELD_ALIASES
from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, PfmeaLocalChecks, _identity, _label, _rows, _step_key, pair_identity
from audit.engine.rule_engine import Decision, GuardStop, RuleContext
from audit.kinds import PpapDocumentType


class NioPfmeaAgent(PfmeaLocalChecks, ExcelAgent):
    DOC_TYPE = PpapDocumentType.PFMEA
    CLAUSE_BOUND_CHECKS = True

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
            if not pair_identity(part) or pair_identity(part) != pair_identity(other):
                raise GuardStop(Decision.UNABLE, "两份原件的零件号缺失或不一致，尚不能配对。")
            for source, version, scope in [(ctx.workbook, self.audit_file.audit_id, self._target_sheets(ctx)), (workbook, related.audit_id, sheets)]:
                customer = _label(ctx, source, ("客户", "客户名称", "Customer", "Customer Name"), version, sheets=scope)
                if customer and _identity(customer) not in {"蔚来", "nio"}:
                    raise GuardStop(Decision.UNABLE, "原件客户标识与本轮蔚来模式冲突，需另开任务或确认。")
            revision = self._target_label(ctx, ("版本", "版本号", "修订版次"))
            other_revision = _label(ctx, workbook, ("版本", "版本号", "修订版次"), related.audit_id, sheets=sheets)
            if (revision or other_revision) and (not pair_identity(revision) or pair_identity(revision) != pair_identity(other_revision)):
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
