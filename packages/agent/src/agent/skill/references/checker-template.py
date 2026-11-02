"""Runnable starting point. Change the clause id and the check before submitting."""

from audit.checkers.v3.excel_agent import ExcelAgent, rule
from audit.engine.rule_engine.context import RuleContext
from audit.engine.rule_engine.fluent import Table


class TaskAudit(ExcelAgent):
    CLAUSE_BOUND_CHECKS = True

    @rule("替换为本条款说明", clause_id="REPLACE")
    def check_clause(self, ctx: RuleContext) -> tuple[str, str]:
        # 尖括号里的名称不是本次文件的表或列，不能原样执行。
        # section = Table(ctx).sheet("<本次工作表>").section("<本次数据区>")
        # columns = section.columns("<列甲>", "<列乙>")
        # for row in section.rows_from(columns):
        #     observed = row.observation("<列甲>")
        #     if observed.state == "unresolved":
        #         ctx.note_unresolved("<无法读取>")
        #     elif observed.state == "known":
        #         row.expect("<列乙>", context="<本次缺少依据>")
        del ctx
        # 方法必须返回两个字符串。不写 return 不能成为通过。
        raise NotImplementedError("按本次生效条款和目标文件实现，不要保留这段模板")


def create_audit(audit_file, rule, group_context=None):
    try:
        from resources import RESOURCE
    except ImportError:
        return TaskAudit(audit_file, rule, group_context)
    return TaskAudit(audit_file, rule, group_context, resource=RESOURCE)
