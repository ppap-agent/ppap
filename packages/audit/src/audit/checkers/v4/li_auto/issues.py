"""L15 accepts parseable dates rather than a single display punctuation."""

from datetime import datetime, time

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.xir_assembly_issue_agent import XirAssemblyIssueAgent
from audit.engine.checkers.date_utils import normalize_excel_date

from .facts import NativeFacts, data_rows, precheck_fact


class LiAutoIssuesAgent(NativeFacts, XirAssemblyIssueAgent):
    EXPECTED_RULE_CATEGORIES = ("基础字段完整性审核", "问题关闭状态与描述关联审核", "整改措施要素完整性及时间有效性审核", "风险分类标识审核")
    PRECHECK_METHODS = {"L15:1:9663b311448fb23b5a82": "rule_1"}

    @rule("整改措施要素完整性及时间有效性审核")
    def rule_3(self, ctx):
        rows = data_rows(ctx, "主表", "问题跟踪区", ("问题描述列", "对应措施列", "时间节点列", "负责人列"))
        for row in rows:
            if row.is_header() or not row.is_present("问题描述列"):
                continue
            row.expect("对应措施列", "时间节点列", "负责人列", context="理想问题整改")
            raw = row.value("时间节点列")
            parsed = normalize_excel_date(raw)
            if parsed is None:
                return "UNABLE", "时间节点无法可靠解析，需核对原件。"
            deadline = raw if isinstance(raw, datetime) else datetime.combine(parsed, time.min)
            now = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.now()
            if deadline <= now:
                return "NG", f"问题行{row.row_index}的时间节点须晚于当前时间。"
        return "PASS", ""

    def _precheck_remediation(self, ctx):
        for row in data_rows(ctx, "主表", "问题跟踪区", ("问题描述列", "对应措施列", "时间节点列", "负责人列")):
            if row.is_present("问题描述列"):
                row.expect("对应措施列", "时间节点列", "负责人列", context="问题登记必填项")
        return "PASS", "问题整改必填项已核实。"

    def precheck(self):
        return [
            precheck_fact(self, "L15:S1", self.rule_1),
            precheck_fact(self, "L15:S2", self.rule_2, code="W01"),
            precheck_fact(self, "L15:S3", self._precheck_remediation),
            precheck_fact(self, "L15:S4", self.rule_4, code="W03"),
            {"id": "L15:S5", "source_id": "L15:S5", "status": "handoff", "code": "S03", "message": "时间节点有效性按源矩阵移交正式审核。", "certified": False},
        ]
