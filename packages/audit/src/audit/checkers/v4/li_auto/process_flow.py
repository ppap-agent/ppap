"""L11 exact required headers, conditional G/Z and real connector evidence."""

from pathlib import Path

from audit.checkers.v2.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent as LegacyFlow
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import Row, Table
from audit.kinds import PpapDocumentType

from .facts import NativeFacts, columns_present, compact, precheck_fact


class LiAutoProcessFlowAgent(NativeFacts):
    EXPECTED_RULE_CATEGORIES = ("表头八项完整且非空", "第一特性类别及产品特性", "第二特性类别及过程特性", "工序名称与操作描述检查", "工序定义完整性检查", "工序图例之间存在真实连线")
    PRECHECK_METHODS = {"L11:1:8b5696288e17e73fafb3": "rule_1", "L11:4:2b4112e6303b5d99b69a": "rule_4"}
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    rule_4 = LegacyFlow.rule_4
    rule_5 = LegacyFlow.rule_5
    _xml_local_name = LegacyFlow._xml_local_name
    _normalize_zip_target = LegacyFlow._normalize_zip_target
    _resolve_sheet_xml_path = LegacyFlow._resolve_sheet_xml_path
    _sheet_rels_path = LegacyFlow._sheet_rels_path
    _rows_with_connector_evidence = LegacyFlow._rows_with_connector_evidence
    _connector_endpoint_is_attached = LegacyFlow._connector_endpoint_is_attached

    @rule("表头八项完整且非空")
    def rule_1(self, ctx):
        Table(ctx).field_block("过程流程图头部字段区").expect(
            "零件号字段", "零件名称字段", "项目车型字段", "供应商名称字段", "代码字段", "编制日期字段", "版本字段", "团队字段", context="理想过程流程图表头"
        )
        return "PASS", "八项表头均已核实。"

    def _categories(self, ctx, ordinal):
        for sheet in Table(ctx).find_sheets("过程流程图"):
            section = sheet.section("过程流程主数据区")
            cols = section.columns("工序名称", "产品特殊特性", "过程特殊特性")
            ws = section.resolved.ws
            band = [cell for row in ws.iter_rows(min_row=section.resolved.header_row, max_row=max(column.data_start_row for column in cols.values()) - 1) for cell in row]
            product = {cell.column for cell in band if compact(cell.value) == "产品特性类别"}
            process = {cell.column for cell in band if compact(cell.value) == "过程特性类别"}
            headers = [next(iter(product)), next(iter(process))] if len(product) == len(process) == 1 else sorted({cell.column for cell in band if compact(cell.value) == "特性类别"})
            if len(headers) != 2:
                return "NG", "必须包含两个可唯一定位的特性类别列。"
            count = 0
            for index in range(max(column.data_start_row for column in cols.values()), ws.max_row + 1):
                if not any(compact(ws.cell(index, column).value) for column in {*(col.header_col for col in cols.values()), *headers}):
                    continue
                count += 1
                row = Row(ctx, ws, cols, index, section.resolved)
                merge = ctx.ops.get_detailed_merge_info(ws, index, headers[ordinal])
                if merge and merge.min_col != merge.max_col:
                    return "UNABLE", "特性类别跨越不同业务列，尚不能确定归属。"
                value = ws.cell(merge.min_row if merge else index, merge.min_col if merge else headers[ordinal])
                ctx.note_cell(ws.title, value.row, value.column)
                if value.data_type in {"e", "f"}:
                    return "UNABLE", "特性类别不可读取。"
                if compact(value.value) in {"【G】", "【Z】"}:
                    row.expect("产品特殊特性" if ordinal == 0 else "过程特殊特性", context="G/Z对应特性必须填写")
            ctx.note_scan(yielded=count, finished=True)
            if not count:
                return "UNABLE", "没有可核实的类别业务行。"
        return "PASS", "两个特性类别及对应内容已核实。"

    @rule("第一特性类别及产品特性")
    def rule_2(self, ctx):
        return self._categories(ctx, 0)

    @rule("第二特性类别及过程特性")
    def rule_3(self, ctx):
        return self._categories(ctx, 1)

    @rule("工序图例之间存在真实连线")
    def rule_6(self, ctx):
        required = ("工序定义-工序名称", "工序定义-制造", "工序定义-运输", "工序定义-存储", "工序定义-检验", "工序定义-返工", "工序定义-报废")
        for sheet in Table(ctx).find_sheets("过程流程图"):
            section = sheet.section("过程流程工序定义区")
            columns = section.columns(*required)
            rows = [r for r in section.rows_from(columns) if r.is_present(required[0])]
            if not rows:
                return "UNABLE", "没有可核实的工序图例。"
            for row in rows:
                row.expect_condition(required[0], lambda _, r=row: r.count_present(*required[1:]) > 0, context="每个工序必须有图例")
            indices = sorted({r.anchor(required[0]).row_index for r in rows})
            if len(indices) < 2:
                return "UNABLE", "只有一个工序，不能核实工序之间的连线。"
            if Path(str(self._source_path)).suffix.lower() not in {".xlsx", ".xlsm"}:
                return "UNABLE", "当前载体的连线尚未可靠读取。"
            covered = self._rows_with_connector_evidence(
                section.resolved.ws, row_min=min(indices), row_max=max(indices), col_min=min(c.header_col for c in columns.values()), col_max=max(c.header_col for c in columns.values())
            )
            if not all(i in covered for i in range(min(indices), max(indices) + 1)):
                return "NG", "工序图例之间缺少可核实的连线。"
        return "PASS", "已读取工序之间的连线。"

    def _precheck_category_columns(self, ctx):
        for sheet in Table(ctx).find_sheets("过程流程图"):
            section = sheet.section("过程流程主数据区")
            columns = set()
            business = section.columns("工序名称", "产品特殊特性", "过程特殊特性")
            for cells in sheet.ws.iter_rows(min_row=section.resolved.header_row, max_row=max(column.data_start_row for column in business.values()) - 1):
                for cell in cells:
                    if compact(cell.value) in {"特性类别", "产品特性类别", "过程特性类别"}:
                        sheet.cell(cell.row, cell.column).value()
                        columns.add(cell.column)
            if len(columns) != 2:
                return "NG", "应有两个特性类别列。"
        return "PASS", "已核实两个特性类别列。"

    def precheck(self):
        activity = ("工序定义-工序名称", "工序定义-制造", "工序定义-运输", "工序定义-存储", "工序定义-检验", "工序定义-返工", "工序定义-报废")
        return [
            precheck_fact(self, "L11:S1", self.rule_1),
            precheck_fact(self, "L11:S2", self._precheck_category_columns, code="B05"),
            precheck_fact(self, "L11:S3", self.rule_2),
            precheck_fact(self, "L11:S4", self.rule_3),
            precheck_fact(self, "L11:S5", self.rule_4),
            precheck_fact(self, "L11:S6", lambda ctx: columns_present(ctx, "过程流程图", "过程流程工序定义区", activity), code="B05"),
            precheck_fact(self, "L11:S7", self.rule_5, code="W06"),
            precheck_fact(self, "L11:S8", self.rule_6, code="W06"),
        ]
