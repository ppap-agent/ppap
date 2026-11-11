"""UAES U03 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from datetime import date

from audit.checker_lib.source import SourceAudit, UnknownFact
from audit.engine.checkers.date_utils import normalize_excel_date
from resources import RESOURCE


class UaesLabQualificationAgent(SourceAudit):
    def rule_1(self):
        facts = self.native()
        facts.require_title("中国合格评定国家认可委员会")
        facts.require_text("CNAS")
        value = facts.field(("截止日期", "有效期至", "有效期截止日期", "证书有效期至"))
        deadline = normalize_excel_date(value)
        if deadline is None:
            raise UnknownFact("认可证书的截止日期未能可靠识别，需人工复核。")
        if deadline <= date.today():
            return "NG", "实验室资质已到期，截止日期必须晚于当前日期。"
        message = "资质截止日期晚于当前日期；CNAS标识及认可委员会名称已读取。"
        if self.group_context and any(
            any(term in str(file.file_metadata.get("original_filename", "")) for term in ("试验报告", "验证报告", "DVR", "DV报告", "PV报告")) for file in self.group_context.by_audit_id.values()
        ):
            message += "同批有试验报告，需人工比对测试机构与资质实验室是否一致。"
        status, visual_message = self.visual_result({"cnas_mark": "资质证书存在实际CNAS认可标识，不能仅有提及CNAS或空标识占位。"}, "证书实际CNAS认可标识", existential_fields=("cnas_mark",))
        return status, message + visual_message


def create_audit(audit_file, rule, group_context=None):
    return UaesLabQualificationAgent(audit_file, rule, group_context, resource=RESOURCE)
