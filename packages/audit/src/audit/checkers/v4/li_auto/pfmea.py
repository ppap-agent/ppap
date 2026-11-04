"""L07 local PFMEA facts and same-part, same-version G/Z PFD evidence."""

from contextlib import contextmanager

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.pfmea_shared import PFD_FIELD_ALIASES
from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, PfmeaLocalChecks, _identity, _label, _rows, _step_key
from audit.engine.rule_engine import Decision, GuardStop
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, compact


class LiAutoPfmeaAgent(PfmeaLocalChecks, NativeFacts):
    EXPECTED_RULE_CATEGORIES = (
        "表头字段必填",
        "开始及修订日期对应履历",
        "过程项功能三个维度",
        "严重度取三维度最大值",
        "探测度取全部措施最小值",
        "过程流程图工序名称与PFMEA过程步骤一致",
        "过程流程图G/Z与PFMEA特殊特性一致",
    )
    DOC_TYPE = PpapDocumentType.PFMEA
    CLAUSE_BOUND_CHECKS = True

    @contextmanager
    def _pfd(self, ctx):
        candidates = self.get_related_files(PpapDocumentType.PROCESS_FLOW_CHART) if self.group_context else []
        if len(candidates) != 1:
            raise GuardStop(Decision.UNABLE, "本轮缺少唯一过程流程图，不能确定关联原件。")
        related = candidates[0]
        workbook = _load_workbook_auto(related.file_url)
        try:
            sheets = [s for s in workbook.worksheets if "过程流程图" in s.title and not any(t in s.title for t in _NON_DATA_SHEETS)]
            if len(sheets) != 1:
                raise GuardStop(Decision.UNABLE, "无法唯一确定关联过程流程图表。")
            ctx.bind_workbook(workbook, related.audit_id)
            part = self._target_label(ctx, ("零件号", "零件编号"))
            other = _label(ctx, workbook, ("零件号", "零件编号"), related.audit_id, sheets=sheets)
            if not part or not other or _identity(part) != _identity(other):
                raise GuardStop(Decision.UNABLE, "两份原件的零件号缺失或不一致。")
            revision = self._target_label(ctx, ("版本", "版本号", "修订版次"))
            other_revision = _label(ctx, workbook, ("版本", "版本号", "修订版次"), related.audit_id, sheets=sheets)
            if (revision or other_revision) and (not revision or not other_revision or _identity(revision) != _identity(other_revision)):
                raise GuardStop(Decision.UNABLE, "两份原件版本缺失或不一致。")
            for source, version, scope in [(ctx.workbook, self.audit_file.audit_id, self._target_sheets(ctx)), (workbook, related.audit_id, sheets)]:
                customer = _label(ctx, source, ("客户", "客户名称", "Customer", "Customer Name"), version, sheets=scope)
                if customer and _identity(customer) not in {"理想", "理想汽车", "liauto", "li"}:
                    raise GuardStop(Decision.UNABLE, "原件客户与本轮理想模式冲突。")
            yield workbook, related.audit_id, sheets
        finally:
            workbook.close()

    @rule("过程流程图工序名称与PFMEA过程步骤一致", clause_id="6")
    def rule_6(self, ctx):
        steps = {_step_key(row["step"])[1] for row in self._target_rows(ctx, "step") if row["step"]}
        with self._pfd(ctx) as (workbook, version, sheets):
            expected = {_identity(r["operation_name"]) for r in _rows(ctx, workbook, PFD_FIELD_ALIASES, ("operation_no", "operation_name"), version, sheets=sheets) if r["operation_name"]}
            if not steps or not expected:
                return "UNABLE", "缺少可核对的工序内容。"
            if steps != expected:
                return "NG", "两份原件的工序名称不一致。"
        return self._pass(ctx)

    @rule("过程流程图G/Z与PFMEA特殊特性一致", clause_id="7")
    def rule_7(self, ctx):
        target = self._target_rows(ctx, "step", "characteristic")
        with self._pfd(ctx) as (workbook, version, sheets):
            for row in _rows(ctx, workbook, PFD_FIELD_ALIASES, ("operation_name", "product_char_type", "process_char_type"), version, sheets=sheets):
                categories = {compact(row.get(k, "")) for k in ("product_char_type", "process_char_type")} & {"【G】", "【Z】"}
                if not categories:
                    continue
                matches = [r for r in target if _step_key(r["step"])[1] == _identity(row["operation_name"])]
                if len({_step_key(r["step"]) for r in matches}) > 1:
                    return "UNABLE", "同名工序存在多个编号，不能唯一确定对应行。"
                if not matches or not any(compact(r["characteristic"]) in {"G", "Z"} for r in matches):
                    return "NG", f"工序“{row['operation_name']}”的G/Z未在PFMEA特殊特性中体现。"
        return self._pass(ctx)
