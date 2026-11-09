"""Source-owned UAES checks; original rules and explicit evidence decide results."""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit
from resources import RESOURCE


def create_audit(audit_file, rule, group_context=None):
    return _Audit(audit_file, rule, group_context, resource=RESOURCE)


class _Audit(SourceAudit):
    def rule_1(self):
        return self.visual_result(
            {
                "key_operation": "全图至少有一个实际关键工序标记。",
                "characteristic_marks": "产品/过程特性中的关键、重要或特殊特性有实际符号标注；不能只有未标注的文字描述。",
                "legend": "原件图例或注释定义了上述每种关键标记的含义。",
            },
            "关键工序、特性标记和标记定义",
        )

    def rule_2(self):
        return self.visual_result(
            {
                "standard_shapes": "全部工序活动用标准化流程图形表达，不能只是纯文字框或列表。",
                "complete_legend": "图例覆盖了全图实际使用的全部符号。",
                "consistent": "同一活动类型在全图使用相同符号。",
                "inspection_shapes": "所有进货、过程、最终、巡检、自检活动使用检验符号，且可与图例核对。",
            },
            "全图标准符号、完整图例、一致性及检验符号",
        )

    def rule_3(self):
        return self.controlled_stamp()
