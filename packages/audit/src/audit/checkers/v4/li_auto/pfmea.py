"""L07 local PFMEA facts and same-part, same-version G/Z PFD evidence."""

from contextlib import contextmanager

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.pfmea_shared import PFD_FIELD_ALIASES
from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, PfmeaLocalChecks, _identity, _label, _rows, _step_key, pair_identity
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
            if not pair_identity(part) or pair_identity(part) != pair_identity(other):
                raise GuardStop(Decision.UNABLE, "两份原件的零件号缺失或不一致。")
            revision = self._target_label(ctx, ("版本", "版本号", "修订版次"))
            other_revision = _label(ctx, workbook, ("版本", "版本号", "修订版次"), related.audit_id, sheets=sheets)
            if (revision or other_revision) and (not pair_identity(revision) or pair_identity(revision) != pair_identity(other_revision)):
                raise GuardStop(Decision.UNABLE, "两份原件版本缺失或不一致。")
            for source, version, scope in [(ctx.workbook, self.audit_file.audit_id, self._target_sheets(ctx)), (workbook, related.audit_id, sheets)]:
                customer = _label(ctx, source, ("客户", "客户名称", "Customer", "Customer Name"), version, sheets=scope)
                if customer and _identity(customer) not in {"理想", "理想汽车", "liauto", "li"}:
                    raise GuardStop(Decision.UNABLE, "原件客户与本轮理想模式冲突。")
            yield workbook, related.audit_id, sheets
        finally:
            workbook.close()

    def _pfd_characteristic_rows(self, ctx, workbook, version, sheets):
        # A generic first/second category pair is bound to its product/process
        # parent columns. We do not rewrite headers before collecting evidence.
        for sheet in sheets:
            generic = [(cell.row, cell.column) for row in sheet.iter_rows(max_row=min(sheet.max_row, 80)) for cell in row if compact(cell.value) == "特性类别"]
            if not generic:
                yield from _rows(ctx, workbook, PFD_FIELD_ALIASES, ("operation_name", "product_char_type", "process_char_type"), version, sheets=[sheet])
                continue
            if len(generic) != 2 or abs(generic[0][0] - generic[1][0]) > 2:
                raise GuardStop(Decision.UNABLE, "过程流程图特性类别不能唯一对应产品和过程。")
            top = min(row for row, _ in generic)
            bottom = max(row for row, _ in generic)
            labels = {}
            for row in sheet.iter_rows(min_row=max(1, top - 2), max_row=min(sheet.max_row, bottom + 2)):
                for cell in row:
                    value = compact(cell.value)
                    if value in {"工序名称", "产品特殊特性", "过程特殊特性"}:
                        if value in labels and labels[value][1] != cell.column:
                            raise GuardStop(Decision.UNABLE, "过程流程图有多个相同业务表头。")
                        labels[value] = (cell.row, cell.column)
            if set(labels) != {"工序名称", "产品特殊特性", "过程特殊特性"}:
                raise GuardStop(Decision.UNABLE, "尚不能确定特性类别的产品或过程父列。")
            categories = sorted(generic, key=lambda item: item[1])
            if not (labels["产品特殊特性"][1] < categories[0][1] < labels["过程特殊特性"][1] < categories[1][1]):
                raise GuardStop(Decision.UNABLE, "特性类别与产品、过程列的表头关系不明确。")
            columns = {"operation_name": labels["工序名称"][1], "product_char_type": categories[0][1], "process_char_type": categories[1][1]}
            for row, column in [*generic, *labels.values()]:
                ctx.note_cell(sheet.title, row, column, file_version_id=version)
            for index in range(max([bottom, *(row for row, _ in labels.values())]) + 1, sheet.max_row + 1):
                cells = {name: sheet.cell(index, column) for name, column in columns.items()}
                if not any(cell.value is not None for cell in cells.values()):
                    continue
                for cell in cells.values():
                    if cell.data_type in {"e", "f"}:
                        raise GuardStop(Decision.UNABLE, "关联工序或特性类别无法读取。")
                    ctx.note_cell(sheet.title, cell.row, cell.column, file_version_id=version)
                yield {name: str(cell.value or "").strip() for name, cell in cells.items()}

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
            for row in self._pfd_characteristic_rows(ctx, workbook, version, sheets):
                categories = {compact(row.get(k, "")) for k in ("product_char_type", "process_char_type")} & {"【G】", "【Z】"}
                if not categories:
                    continue
                matches = [r for r in target if _step_key(r["step"])[1] == _identity(row["operation_name"])]
                if len({_step_key(r["step"]) for r in matches}) > 1:
                    return "UNABLE", "同名工序存在多个编号，不能唯一确定对应行。"
                if not matches or not any(compact(r["characteristic"]) in {"G", "Z"} for r in matches):
                    return "NG", f"工序“{row['operation_name']}”的G/Z未在PFMEA特殊特性中体现。"
        return self._pass(ctx)
