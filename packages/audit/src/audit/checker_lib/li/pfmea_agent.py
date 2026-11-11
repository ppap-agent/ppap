"""
潜在过程失效模式及后果分析(PFMEA)审核代理
规则：
1.识别表头区域是否包含“公司名称”、“车型项目”、“零件名称”、“零件号”、“制造地址”、“PFMEA开始日期”、“PFMEA修订日期”、“跨职能团队”字段，如果包含则继续识别，当“公司名称”、“车型项目”、“零件名称”、“零件号”、“制造地址”、“PFMEA开始日期”、“PFMEA修订日期”、“跨职能团队”字段对应内容均不为空则通过，否则不通过
2.识别是否有PFMEA表格、变更履历表格，如果不包含则不通过，如果包含则继续识别PFMEA表头区域是否包含“PFMEA开始日期”、“PFMEA修订日期”字段，如果包含则继续识别，当PFMEA表格中的“PFMEA开始日期”字段内容与变更履历表中“修订日期”第一行有效数据的内容一致，且PFMEA表格中的“PFMEA修订日期”字段内容与变更履历表中“修订日期”最后一行有效数据的内容一致则通过，否则不通过
3.识别是否有PFMEA表、过程流程图表，如果不包含则不通过，否则继续识别PFMEA表中是否包含“过程步骤”列，过程流程图中是否包含“工序号”、“工序名称”列，如果包含则继续识别，PFMEA表中的“过程步骤”列下内容是否与过程流程图中的“工序名称”内容一致，如果是则通过，否则不通过
4.识别PFMEA表中是否包含“过程项功能”列，如果包含，则继续识别“过程项功能”列下内容是否均包含三个维度，如果是则通过，否则不通过
5.识别是否有PFMEA表、过程流程图表，如果不包含则不通过，否则继续识别PFMEA表中是否包含“过程步骤”、“特殊特性”列，过程流程图中是否包含“工序号”、“工序名称”、“特性类别”列，如果包含则继续识别。当过程流程图中的“特性类别”列下内容为“【G】”或“【Z】”时，则以对应行的“工序名称”的内容在PFMEA表中“过程步骤”列下识别是否有内容相同的行，如果存在则继续在PFMEA表中识别对应行的“特殊特性”列下内容是否同样为“G”或“Z”则通过，否则不通过
6.识别PFMEA表中是否包含“失效影响”列，如果没有，则该审核项判定为NG；如果有，则继续识别“失效影响”列下的内容是否包含三个维度，且每个维度存在对应独立的严重度分值，如果不包含三个维度或每个维度没有独立对应严重度分值则不通过，否则继续识别PFMEA表中的“严重度”的分值是否为“失效影响”三个维度分值的最大值，如果是则通过，否则不通过
7.识别PFMEA表中是否包含“现行探测控制”列，如果没有，则该审核项判定为NG；如果有，则继续识别“现行探测控制”列下的内容是否为一个或多个探测措施，且每个探测措施存在对应独立的探测度分值，如果内容为空或每个探测措施没有独立对应探测度分值则不通过，否则继续识别PFMEA表中的“探测度”的分值是否为“现行探测控制”中分值的最小值，如果是则通过，否则不通过
"""

import logging
import re
from datetime import date
from typing import Any, Callable, Tuple

from audit.engine.checkers.date_utils import normalize_excel_date
from audit.engine.rule_engine import GuardStop, RuleContext, Table
from audit.kinds import PpapDocumentType

from .excel_agent import ExcelAgent, rule

logger = logging.getLogger(__name__)


class PfmeaAgent(ExcelAgent):
    """PFMEA专业审核代理"""

    DOC_TYPE = PpapDocumentType.PFMEA

    @staticmethod
    def _cross_table_result(ctx: RuleContext) -> Tuple[str, str]:
        """No 过程流程图 table fails the clause; comparing a present one is not implemented."""
        Table(ctx).sheet("PFMEA主表")
        flow_sheets = [ws.title for ws in ctx.workbook.worksheets if ws.sheet_state == "visible" and ("流程图" in ws.title or "flow" in ws.title.lower())]
        ctx.record_execution()
        if not flow_sheets:
            return "NG", "未包含过程流程图表。"
        return "UNABLE", "PFMEA与过程流程图的逐行比对尚未实现，需人工核对。"

    @rule("表头字段完整性检查")
    def rule_1(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则1：识别表头区域是否包含“公司名称”、“车型项目”、“零件名称”、“零件号”、“制造地址”、“PFMEA开始日期”、“PFMEA修订日期”、“跨职能团队”字段，
        如果包含则继续识别，当“公司名称”、“车型项目”、“零件名称”、“零件号”、“制造地址”、“PFMEA开始日期”、“PFMEA修订日期”、“跨职能团队”字段对应内容均不为空则通过，否则不通过
        """
        header_block = Table(ctx).field_block("PFMEA头部字段区")
        required_fields = (
            "公司名称字段",
            "车型项目字段",
            "零件名称字段",
            "零件号字段",
            "制造地址字段",
            "PFMEA开始日期字段",
            "PFMEA修订日期字段",
            "跨职能团队字段",
        )
        header_block.expect(
            *required_fields,
            context="PFMEA表头",
        )
        return "PASS", ""

    @rule("PFMEA开始/修订日期与变更履历一致性检查")
    def rule_2(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则2：识别是否有PFMEA表格、变更履历表格，如果不包含则不通过，如果包含则继续识别PFMEA表头区域是否包含“PFMEA开始日期”、“PFMEA修订日期”字段，
        如果包含则继续识别，当PFMEA表格中的“PFMEA开始日期”字段内容与变更履历表中“修订日期”第一行有效数据的内容一致，且PFMEA表格中的“PFMEA修订日期”字段内容与变更履历表中“修订日期”最后一行有效数据的内容一致则通过，否则不通过
        """
        table = Table(ctx)
        header_block = table.field_block("PFMEA头部字段区")
        header_block.expect("PFMEA开始日期字段", "PFMEA修订日期字段", context="PFMEA头部字段区")

        history_sheets = table.find_sheets("变更履历表")
        history_section = history_sheets[0].section("变更履历数据区")
        history_columns = history_section.columns("修订日期")

        revision_date_values: list[Any] = []
        for row in history_section.rows_from(history_columns):
            if not row.is_present("修订日期"):
                continue
            cell_value = row.value("修订日期")
            revision_date_values.append(cell_value)

        if not revision_date_values:
            return "NG", "变更履历工作表中修订日期列无有效数据"

        first_revision_date = normalize_excel_date(revision_date_values[0])
        last_revision_date = normalize_excel_date(revision_date_values[-1])

        if not first_revision_date or not last_revision_date:
            return "NG", "变更履历日期格式不合法或为空"

        def matches_date(expected_date: date) -> Callable[[Any], bool]:
            def _predicate(val: Any) -> bool:
                parsed = normalize_excel_date(val)
                return parsed is not None and parsed == expected_date

            return _predicate

        header_block.expect_condition(
            "PFMEA开始日期字段",
            matches_date(first_revision_date),
            context="与变更履历第一行修订日期一致",
        ).expect_condition(
            "PFMEA修订日期字段",
            matches_date(last_revision_date),
            context="与变更履历最后一行修订日期一致",
        )

        return "PASS", ""

    @rule("过程步骤与工序名称一致性检查")
    def rule_3(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则3：识别是否有PFMEA表、过程流程图表，如果不包含则不通过，否则继续识别PFMEA表中是否包含“过程步骤”列，过程流程图中是否包含“工序号”、“工序名称”列，
        如果包含则继续识别，PFMEA表中的“过程步骤”列下内容是否与过程流程图中的“工序名称”内容一致，如果是则通过，否则不通过
        """
        return self._cross_table_result(ctx)

    @rule("过程项功能维度检查")
    def rule_4(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则4：识别PFMEA表中是否包含“过程项功能”列，如果包含，则继续识别“过程项功能”列下内容是否均包含三个维度的内容，如果是则通过，否则不通过
        """
        sheet = Table(ctx).sheet("PFMEA主表")
        section = sheet.section("PFMEA主数据区")
        columns = section.columns("过程项功能")

        required_dimensions = {
            "内部": ["内部", "工厂内", "厂内", "内"],
            "客户工厂": ["客户工厂", "主机厂", "OEM", "顾客工厂"],
            # 维度别名必须保持独立，避免同一别名同时满足多个维度导致误判PASS。
            "最终用户": ["最终用户", "用户", "终端用户", "消费者"],
        }

        def has_all_dimensions(raw_value: Any) -> bool:
            if raw_value is None:
                return False
            cell_value = "".join(str(raw_value).split())
            for aliases in required_dimensions.values():
                if not any(alias in cell_value for alias in aliases):
                    return False
            return True

        rows = list(section.rows_from(columns))
        if not rows:
            has_main_rows = False
            for anchor_column in ("失效影响", "现行探测控制"):
                try:
                    fallback_rows = list(section.rows_from(section.columns(anchor_column)))
                except GuardStop:
                    continue
                if fallback_rows:
                    has_main_rows = True
                    break
            if has_main_rows:
                return "NG", "PFMEA主数据区中“过程项功能”列未找到有效数据行。"
            return "NG", "PFMEA主数据区未找到有效数据行（过程项功能/失效影响/现行探测控制均未命中）。"

        for row in rows:
            row.expect("过程项功能", context="过程项功能").expect_condition(
                "过程项功能",
                has_all_dimensions,
                context="过程项功能维度检查",
            )

        return "PASS", ""

    @rule("特殊特性一致性检查")
    def rule_5(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则5：识别是否有PFMEA表、过程流程图表，如果不包含则不通过，否则继续识别PFMEA表中是否包含“过程步骤”、“特殊特性”列，过程流程图中是否包含“工序号”、“工序名称”、“特性类别”列，
        如果包含则继续识别。当过程流程图中的“特性类别”列下内容为“【G】”或“【Z】”时，则以对应行的“工序名称”的内容在PFMEA表中“过程步骤”列下识别是否有内容相同的行，
        如果存在则继续在PFMEA表中识别对应行的“特殊特性”列下内容是否同样为“G”或“Z”则通过，否则不通过
        """
        return self._cross_table_result(ctx)

    @rule("失效模式、失效影响、失效严重度维度检查")
    def rule_6(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则6：识别PFMEA表中是否包含“失效影响”列，如果没有，则该审核项判定为NG；如果有，则继续识别“失效影响”列下的内容是否包含三个维度，且每个维度存在对应独立的严重度分值，如果不包含三个维度或每个维度没有独立对应严重度分值则不通过，否则继续识别PFMEA表中的“严重度”的分值是否为“失效影响”三个维度分值的最大值，如果是则通过，否则不通过
        """
        logger.debug("开始执行规则6：失效模式、失效影响、失效严重度维度检查")

        sheet = Table(ctx).sheet("PFMEA主表")
        section = sheet.section("PFMEA主数据区")
        columns = section.columns("失效影响", "严重度")

        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "PFMEA主数据区未找到有效数据行。"

        def has_enough_dimensions(val: Any) -> bool:
            return len(re.split(r"[，,;:：/\n\r]+", str(val or ""))) >= 3

        def severity_scores(val: Any) -> list[int]:
            # “100%产品返工”中的百分数是失效描述，不是严重度分值。
            return [int(x) for x in re.findall(r"\d+", re.sub(r"\d+(?:\.\d+)?\s*[%％]", "", str(val or "")))]

        def has_enough_scores(val: Any) -> bool:
            return len(severity_scores(val)) >= 3

        for row in rows:
            row.expect("失效影响", "严重度", context="失效影响及严重度")

            row.expect_condition("失效影响", has_enough_dimensions, context="失效影响维度数量")
            row.expect_condition("失效影响", has_enough_scores, context="失效影响严重度分值数量")

            failure_effect_value = row.value("失效影响", compact=True)
            effect_scores = severity_scores(failure_effect_value)
            if not effect_scores:
                row.expect_condition("失效影响", lambda _value: False, context="失效影响严重度分值数量")
            max_severity_score = max(effect_scores) if effect_scores else -1

            def matches_max_score(val: Any) -> bool:
                try:
                    return int(str(val or "").strip()) == max_severity_score
                except ValueError:
                    return False

            row.expect_condition("严重度", matches_max_score, context="严重度与失效影响最大分值一致性")

        return "PASS", ""

    @rule("现行探测控制及探测度维度检查")
    def rule_7(self, ctx: RuleContext) -> Tuple[str, str]:
        """
        规则7：识别PFMEA表中是否包含“现行探测控制”列，如果没有，则该审核项判定为NG；如果有，则继续识别“现行探测控制”列下的内容是否为一个或多个探测措施，且每个探测措施存在对应独立的探测度分值，如果内容为空或每个探测措施没有独立对应探测度分值则不通过，否则继续识别PFMEA表中的“探测度”的分值是否为“现行探测控制”中分值的最小值，如果是则通过，否则不通过
        """
        logger.debug("开始执行规则7：现行探测控制及探测度维度检查")

        sheet = Table(ctx).sheet("PFMEA主表")
        section = sheet.section("PFMEA主数据区")
        columns = section.columns("现行探测控制", "探测度")

        rows = list(section.rows_from(columns))
        if not rows:
            return "NG", "PFMEA主数据区未找到有效数据行。"

        def has_measures(val: Any) -> bool:
            val_str = re.sub(r"\d+[.、]", "", str(val or ""))
            return len(re.split(r"[，,;:：/\n\r]+", val_str)) >= 1

        def has_scores(val: Any) -> bool:
            val_str = re.sub(r"\d+[.、]", "", str(val or ""))
            return bool(re.search(r"\d+", val_str))

        for row in rows:
            row.expect("现行探测控制", "探测度", context="现行探测控制及探测度")

            row.expect_condition("现行探测控制", has_measures, context="现行探测控制有效措施")
            row.expect_condition("现行探测控制", has_scores, context="现行探测控制探测度分值")

            val_str = re.sub(r"\d+[.、]", "", str(row.value("现行探测控制", compact=True) or ""))
            detection_scores = [int(x) for x in re.findall(r"\d+", val_str)]
            if not detection_scores:
                row.expect_condition("现行探测控制", lambda _value: False, context="现行探测控制探测度分值")
            min_detection_score = min(detection_scores) if detection_scores else 10**9

            def matches_min_score(val: Any) -> bool:
                try:
                    return int(str(val or "").strip()) == min_detection_score
                except ValueError:
                    return False

            row.expect_condition("探测度", matches_min_score, context="探测度与现行探测控制最小分值一致性")

        return "PASS", ""
