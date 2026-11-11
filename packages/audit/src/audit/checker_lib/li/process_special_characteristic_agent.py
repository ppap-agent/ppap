"""过程特性管理清单 Excel 审核代理。"""

from __future__ import annotations

from typing import Tuple

from audit.engine.rule_engine import Row, RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule


class ProcessSpecialCharacteristicAgent(ExcelAgent):
    """过程特性管理清单专业审核代理。"""

    DOC_TYPE = PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST
    # 合并单元格覆盖的每一行都是数据行，各自检查。
    # 风险评估列也是行锚点（排在最后，只在不取“过程/流程步骤”列时生效），高风险行即使步骤为空也能读到。
    RESOURCE_OVERRIDES = {
        "sections": {
            "主数据区": {
                "row_expand_merged_anchors": True,
                "row_anchors": ("序号", "过程/流程步骤", "零件编号", "特性分类", "现有管理方式风险评估"),
            }
        }
    }

    @rule("关键字段完整性检查")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        header = Table(ctx).field_block("头部字段区")
        required_fields = (
            "零件名称字段",
            "零件号字段",
            "供应商名称字段",
            "供应商编码字段",
        )

        header.expect(
            *required_fields,
            context="过程特性清单头部字段区",
        )

        return "PASS", ""

    @rule("过程特性完整性检查")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")
        section = sheets[0].section("主数据区")
        columns = section.columns(
            "过程/流程步骤",
            "部件(来料检时涉及)",
            "客户要求/功能要求",
            "特性名称",
            "检验岗位",
            "产品/过程特性分类",
            "名义值/规格中心值",
            "规格下限",
            "规格上限",
            "T1-特性监控方法",
            "测量设备",
            "测量方式",
            "防错控制",
            "检查控制",
            "抽样频率",
            "SPC监控",
            "SPC线上管理计划时间",
            "说明（关键要求/操作参数等）",
        )

        required_columns = (
            "部件(来料检时涉及)",
            "客户要求/功能要求",
            "特性名称",
            "检验岗位",
            "产品/过程特性分类",
            "名义值/规格中心值",
            "规格下限",
            "规格上限",
            "测量设备",
            "测量方式",
            "防错控制",
            "检查控制",
            "抽样频率",
            "SPC监控",
            "SPC线上管理计划时间",
            "说明（关键要求/操作参数等）",
        )

        checked_rows = 0
        for row in section.rows_from(columns):
            if not row.is_present("过程/流程步骤"):
                continue
            checked_rows += 1
            row.expect(*required_columns, context="过程/流程步骤")

        if checked_rows == 0:
            return "NG", "主数据区未识别到任何“过程/流程步骤”有内容的数据行"
        return "PASS", ""

    @rule("高风险管理项检查")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        table = Table(ctx)
        sheets = table.find_sheets("主表")
        section = sheets[0].section("主数据区")
        columns = section.columns(
            "过程/流程步骤",
            "现有管理方式风险评估",
            "高风险管理项预防措施",
        )

        # 步骤列有内容的行与风险评估列有内容的行都检查。
        risk_columns = {name: columns[name] for name in ("现有管理方式风险评估", "高风险管理项预防措施")}
        row_indices = {row.row_index for row in section.rows_from(columns)}
        row_indices |= {row.row_index for row in section.rows_from(risk_columns)}
        for row_index in sorted(row_indices):
            row = Row(ctx, section.resolved.ws, columns, row_index, section.resolved)
            is_high_risk = row.exact(
                "现有管理方式风险评估",
                "高风险",
                case_insensitive=False,
                compact=True,
            )
            if not is_high_risk:
                right = row.find_right("现有管理方式风险评估", max_offset=1)
                if right is not None and right.exact(
                    "高风险",
                    case_insensitive=False,
                    compact=True,
                ):
                    is_high_risk = True
            if not is_high_risk:
                continue

            row.expect("过程/流程步骤", "高风险管理项预防措施", context="高风险管理项")

        return "PASS", ""
