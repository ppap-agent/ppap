"""L16 samples are required for each test item, not only nonempty sample cells."""

from dataclasses import replace
from types import MappingProxyType

from audit.checkers.v3.annual_test_requirements_agent import AnnualTestRequirementsAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import get_doc_resource_by_doc_type
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, data_rows

_BASE = get_doc_resource_by_doc_type(PpapDocumentType.ANNUAL_TEST_REQUIREMENTS)
assert _BASE is not None
_COLUMNS = dict(_BASE.columns)
_COLUMNS["要求-样本数量"] = _COLUMNS["样本数量值"].model_copy(update={"canonical_name": "要求-样本数量", "section_name": "试验要求区"})
_RESOURCE = replace(_BASE, columns=MappingProxyType(_COLUMNS))


class LiAutoAnnualTestsAgent(NativeFacts, AnnualTestRequirementsAgent):
    EXPECTED_RULE_CATEGORIES = ("质量等级字段识别", "试验项目与测试频率一致性", "供应商名称格式审核", "样本数量列及内容识别", "试验要求栏完整性审核", "车型字段存在性及内容审核")
    PRECHECK_METHODS = {"L16:5:c605c183cd0072b18d1f": "rule_5", "L16:6:6de176019f93a8723bd3": "rule_6"}

    def __init__(self, audit_file, rule_input, group_context=None):
        super().__init__(audit_file, rule_input, group_context=group_context, resource=_RESOURCE)

    @rule("样本数量列及内容识别")
    def rule_4(self, ctx):
        for row in data_rows(ctx, "主表", "试验要求区", ("要求-试验项目", "要求-样本数量"), trigger="要求-试验项目"):
            if not any(character.isascii() and character.isdigit() for character in str(row.value("要求-样本数量") or "")):
                return "NG", "每个试验项目的样本数量须包含阿拉伯数字。"
        return "PASS", ""
