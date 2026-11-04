"""L17 warranty checks with calendar-month approval expiry."""

import calendar
from datetime import datetime

from audit.checkers.v3.cross_file_extractors import _load_workbook_auto
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.psw_excel_agent import PswExcelAgent
from audit.checkers.v4.pfmea_local import _NON_DATA_SHEETS, _identity, _rows, pair_identity
from audit.engine.checkers.docx_workbook_agent import StructuredDocxWorkbookMixin
from audit.engine.checkers.psw_docx_evidence import PswDocxEvidenceMixin
from audit.engine.rule_engine import Decision, GuardStop, Table
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, precheck_fact


class LiAutoPswAgent(NativeFacts, PswExcelAgent):
    EXPECTED_RULE_CATEGORIES = (
        "生产件批准交付物文件清单附件检查",
        "重量字段格式检查",
        "印章及授权声明检查",
        "供应商名称格式检查",
        "签字与盖章完整性检查",
        "关键字段非空检查",
        "提交原因选项检查",
        "认可情况及结果勾选检查",
        "条件批准日期时效检查",
        "条件批准补充评论检查",
    )
    PRECHECK_METHODS = {"L17:6:c9ac5fd9716f43cdb8ff": "rule_6"}

    def _finalize_audit_result(self, result, *, rule_no, phase, ctx=None):
        finalized = super()._finalize_audit_result(result, rule_no=rule_no, phase=phase, ctx=ctx)
        # Legacy PSW tracing adds broad carrier evidence. Keep the actual V4
        # cell reads as well, including the independently submitted checklist.
        precise = self._evidence_from_reads(ctx) if ctx is not None else None
        if precise and finalized.evidence:
            combined = finalized.evidence.model_copy(update={"sources": (*finalized.evidence.sources, *precise.sources)})
            return finalized.model_copy(update={"evidence": combined})
        return finalized

    @staticmethod
    def _delivery_sheets(workbook):
        return [sheet for sheet in workbook.worksheets if any(name in sheet.title for name in ("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单"))]

    @staticmethod
    def _identity_field(ctx, labels, version, sheets):
        keys = {_identity(label) for label in labels}
        declared = False
        values = set()
        for sheet in sheets:
            if any(token in sheet.title.casefold() for token in ("履历", *_NON_DATA_SHEETS)):
                continue
            if sheet.max_row * sheet.max_column > 250000:
                raise GuardStop(Decision.UNABLE, "身份声明超出本次有界读取范围，尚不能确认没有另行声明。")
            for row in sheet.iter_rows():
                for cell in row:
                    if _identity(cell.value).rstrip(":：") not in keys:
                        continue
                    declared = True
                    ctx.note_cell(sheet.title, cell.row, cell.column, file_version_id=version)
                    right = cell.column + 1
                    for merged in sheet.merged_cells.ranges:
                        if cell.coordinate in merged:
                            right = merged.max_col + 1
                            break
                    value = sheet.cell(cell.row, right)
                    ctx.note_cell(sheet.title, value.row, value.column, file_version_id=version)
                    identity = _identity(value.value)
                    if value.data_type in {"e", "f"}:
                        raise GuardStop(Decision.UNABLE, "已声明的身份字段无法可靠读取，不能继承保证书身份。")
                    values.add(identity)
        if len(values) > 1:
            raise GuardStop(Decision.UNABLE, "同一原件存在冲突的身份声明，不能配对。")
        return declared, next(iter(values), "")

    @staticmethod
    def _could_contain_delivery_list(kind):
        if not kind or _identity(kind) in {"unknown", "未知", "未知类型"}:
            return True
        if any(name in str(kind) for name in ("生产件批准交付物文件清单", "生产件批准提交清单", "PSW交付物清单")):
            return True
        typed = PpapDocumentType.from_string(str(kind))
        if typed != PpapDocumentType.UNKNOWN:
            return typed == PpapDocumentType.PSW
        if str(kind) == "T17":
            return True
        from audit.presets import customer_presets

        if any(preset.source_type == kind for preset in customer_presets()):
            return False
        # An unclassified source may contain an embedded delivery table.
        return True

    @rule("生产件批准交付物文件清单附件检查")
    def rule_1(self, ctx):
        candidates = []
        opened = []
        local = self._delivery_sheets(ctx.workbook)
        if local:
            candidates.append((ctx.workbook, self.audit_file.audit_id, local))
        try:
            if not local and self.group_context:
                if any(self._could_contain_delivery_list(kind) for kind in self.group_context.unavailable_files.values()):
                    raise OSError("本轮关联原件读取失败，不能核实唯一交付物清单。")
                references = dict(self.group_context.by_audit_id)
                known_types = {}
                for kind, entries in self.group_context.by_doc_type.items():
                    for ref in entries:
                        references[ref.audit_id] = ref
                        known_types.setdefault(ref.audit_id, []).append(kind)
                for ref in references.values():
                    if ref.audit_id == self.audit_file.audit_id:
                        continue
                    if ref.audit_id in known_types and not any(self._could_contain_delivery_list(kind) for kind in known_types[ref.audit_id]):
                        continue
                    workbook = _load_workbook_auto(ref.file_url)
                    opened.append(workbook)
                    sheets = self._delivery_sheets(workbook)
                    if sheets:
                        candidates.append((workbook, ref.audit_id, sheets))
            if len(candidates) != 1 or len(candidates[0][2]) != 1:
                return "UNABLE", "缺少唯一生产件批准交付物文件清单，需确认本轮原件。"
            workbook, version, sheets = candidates[0]
            ctx.bind_workbook(workbook, version)
            embedded = workbook is ctx.workbook
            main_sheets = [sheet for sheet in ctx.workbook.worksheets if sheet not in local] if embedded else ctx.workbook.worksheets
            for original, original_version, scope in [(ctx.workbook, self.audit_file.audit_id, main_sheets), (workbook, version, sheets)]:
                _, customer = self._identity_field(ctx, ("客户", "客户名称", "Customer", "Customer Name"), original_version, scope)
                if customer and _identity(customer) not in {"理想", "理想汽车", "liauto", "li"}:
                    return "UNABLE", "原件客户与本轮理想模式冲突，不能配对。"
            fields = [
                (("理想汽车零件号", "零件号", "零件编号"), True),
                (("工程更改版本", "版本", "版本号"), False),
                (("项目名称", "车型项目", "项目编号", "项目代号", "项目标识", "UAES项目编号"), False),
            ]
            # A standalone delivery-list original has no warrant to pair.
            # Its attachment-completeness clause remains a unary check.
            for labels, required in fields if main_sheets else ():
                _, own = self._identity_field(ctx, labels, self.audit_file.audit_id, main_sheets)
                declared, other = self._identity_field(ctx, labels, version, sheets)
                # An embedded checklist may inherit the warrant's identity only
                # when it has no separate declaration of that identity field.
                if embedded and not declared:
                    other = own
                if required or own or other or (embedded and declared):
                    if not pair_identity(own) or pair_identity(own) != pair_identity(other):
                        return "UNABLE", "保证书与交付物清单零件号、项目或版本缺失或不一致，不能配对。"
            rows = _rows(ctx, workbook, {"item": ["项目", "交付物", "提交项目"], "attachment": ["附件"]}, ("item", "attachment"), version, sheets=sheets)
            checked = 0
            for row in rows:
                if not row["item"] or _identity(row["item"]) == _identity("零件认证相关"):
                    continue
                checked += 1
                if not row["attachment"]:
                    return "NG", "交付物清单业务行的附件未填写。"
            return ("PASS", "已核实交付物附件。") if checked else ("UNABLE", "清单没有可核实的业务行。")
        finally:
            for workbook in opened:
                workbook.close()

    @rule("印章及授权声明检查")
    def rule_3(self, ctx):
        return "UNABLE", "尚未核实章面文字及部门章授权中的公司章；图像形状或红色区域不能证明本条通过。"

    @rule("签字与盖章完整性检查")
    def rule_5(self, ctx):
        return "UNABLE", "尚未分别核实实际授权签名与盖章；同一印章不能同时充当签名证据。"

    @rule("条件批准日期时效检查")
    def rule_9(self, ctx):
        main_sheet = Table(ctx).sheet("PSW主表")
        disposition, _, row = self._resolve_warrant_disposition(main_sheet, self._checked_controls(main_sheet))
        if disposition in {"missing_field", "invalid_selection"}:
            return "NG", "认可情况缺失或勾选不唯一。"
        if disposition != "conditional":
            return "PASS", "本条条件未触发。"
        if row is None:
            return "UNABLE", "不能定位条件批准日期。"
        approved = self._extract_conditional_approval_date(main_sheet, row)
        if approved is None:
            return "UNABLE", "条件批准日期不可可靠读取。"
        today = datetime.now().date()
        offset = today.year * 12 + today.month - 1 - 3
        year, month = divmod(offset, 12)
        month += 1
        earliest = today.replace(year=year, month=month, day=min(today.day, calendar.monthrange(year, month)[1]))
        return ("PASS", "条件批准日期在三个日历月内。") if earliest <= approved <= today else ("NG", "条件批准日期不在当前日期之前的三个日历月内。")

    def _precheck_disposition(self, ctx):
        sheet = Table(ctx).sheet("PSW主表")
        disposition, _, _ = self._resolve_warrant_disposition(sheet, self._checked_controls(sheet))
        if disposition in {"missing_field", "invalid_selection"}:
            return "NG", "认可情况字段缺失，或选项未保持唯一。"
        return "PASS", "认可情况字段及唯一勾选已核实。"

    def precheck(self):
        seal = precheck_fact(self, "L17:S3", self.rule_3, code="W03")
        # A combined company/department seal check cannot attribute a negative
        # result separately to either subcondition without visual evidence.
        if seal["status"] == "defect":
            seal.update(status="unknown", code=None, message="尚不能分别核实公司章或部门授权章，请核对原件。")
        department = dict(seal, id="L17:S4", source_id="L17:S4")
        return [
            precheck_fact(self, "L17:S1", self.rule_1, code="W01"),
            precheck_fact(self, "L17:S2", self.rule_2, code="W03"),
            seal,
            department,
            precheck_fact(self, "L17:S5", self.rule_4, code="W03"),
            precheck_fact(self, "L17:S6", self.rule_5, code="W03"),
            precheck_fact(self, "L17:S7", self.rule_6),
            precheck_fact(self, "L17:S8", self.rule_7),
            precheck_fact(self, "L17:S9", self._precheck_disposition),
            precheck_fact(self, "L17:S10", self.rule_8),
            precheck_fact(self, "L17:S11", self.rule_9, code="W06"),
            precheck_fact(self, "L17:S12", self.rule_10),
        ]


class LiAutoPswDocxAgent(PswDocxEvidenceMixin, StructuredDocxWorkbookMixin, LiAutoPswAgent):
    """Read Word original evidence through the existing structured adapter."""
