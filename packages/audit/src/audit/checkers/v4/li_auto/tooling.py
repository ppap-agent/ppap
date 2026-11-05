"""L10 checks the nine source columns and actual photo placements."""

import re
import unicodedata

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v3.tooling_approval_agent import ToolingApprovalAgent
from audit.document_tools import effective
from audit.engine.rule_engine import Decision, GuardStop, Sheet, Table

from .facts import NativeFacts, columns_present, data_rows, image_regions, precheck_fact

_COLUMNS = ("一级分总成零件名称", "分总成组件或分序模具名称", "模具制造供应商", "模具存放地点", "零件质量评估", "模具分供方及实物VS数据&标准验收", "模具量产性能评估", "模具维保评估")


class LiAutoToolingAgent(NativeFacts, ToolingApprovalAgent):
    EXPECTED_RULE_CATEGORIES = ("零件供应商名称与提交日期字段检查", "关键列完整性与内容非空检查", "模具设计尺寸格式检查", "实物照片列检查", "编制与审核字段检查", "实物确认列内容检查")
    PRECHECK_METHODS = {"L10:1": "rule_1", "L10:2": "rule_2", "L10:5": "rule_5"}

    @rule("关键列完整性与内容非空检查")
    def rule_2(self, ctx):
        for row in data_rows(ctx, "模具验收报告", "模具验收主数据区", ("工艺类型", *_COLUMNS), trigger="工艺类型"):
            row.expect(*_COLUMNS, context="理想模具工艺类型")
        return "PASS", ""

    @staticmethod
    def _label_key(value):
        return "".join(unicodedata.normalize("NFKC", str(value if value is not None else "")).split()).casefold()

    def _column_targets(self, ctx, name):
        aliases = {self._label_key(value) for value in ctx.resource.columns[name].aliases}
        all_aliases = {self._label_key(alias) for spec in ctx.resource.columns.values() for alias in spec.aliases}
        targets, known_structure = [], False
        for ws in ctx.workbook.worksheets:
            if any(marker in ws.title for marker in ("说明", "指南", "履历")):
                continue
            matches, known_columns = [], set()
            for cells in ws.iter_rows(max_row=min(ws.max_row, 80)):
                for cell in cells:
                    label = self._label_key(cell.value)
                    if label in all_aliases:
                        known_columns.add(cell.column)
                    if label in aliases:
                        matches.append(cell)
            known_structure = known_structure or len(known_columns) >= 2
            if not matches:
                continue
            if len(matches) != 1:
                raise GuardStop(Decision.UNABLE, f"{name}存在多个候选区段，尚不能确认各数据行归属。")
            header = matches[0]
            ctx.note_cell(ws.title, header.row, header.column)
            merged = ctx.ops.get_detailed_merge_info(ws, header.row, header.column)
            first = (merged.max_row if merged else header.row) + 1
            end = ws.max_row
            footer_aliases = {self._label_key(alias) for field_name in ("编制字段", "审核字段") for alias in ctx.resource.fields[field_name].aliases}
            for cells in ws.iter_rows(min_row=first):
                if any(self._label_key(cell.value) in footer_aliases for cell in cells):
                    end = min(end, cells[0].row - 1)
                    break
            targets.append((ws, header.column, first, end, known_columns))
        if not targets:
            raise GuardStop(Decision.NG if known_structure else Decision.UNABLE, f"未定位到{name}列。")
        return targets

    def _column_values(self, ctx, name):
        count = 0
        for ws, column, first, last, business_columns in self._column_targets(ctx, name):
            for index in range(first, last + 1):
                if not any(ws.cell(index, col).value is not None for col in business_columns):
                    continue
                source = self.native_tables.get(ws.title)
                if source and effective(source, index, column)["state"].startswith("unknown"):
                    raise GuardStop(Decision.UNABLE, f"{name}单元格含未读取的公式、图形或错误值。")
                value = Sheet(ctx, ws).cell(index, column).value()
                count += 1
                yield value
        ctx.note_scan(yielded=count, finished=True)
        if not count:
            raise GuardStop(Decision.UNABLE, f"{name}没有可核实的业务数据行。")

    @rule("模具设计尺寸格式检查")
    def rule_3(self, ctx):
        for value in self._column_values(ctx, "模具设计尺寸"):
            text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
            if re.fullmatch(r"\d+(?:\.\d+)?×\d+(?:\.\d+)?×\d+(?:\.\d+)?", text.strip()) is None:
                return "NG", "模具设计尺寸须为数字×数字×数字。"
        return "PASS", "每个尺寸值均符合长×宽×高格式。"

    @rule("实物照片列检查")
    def rule_4(self, ctx):
        for ws, column, first, last, _ in self._column_targets(ctx, "实物照片"):
            source = self.native_tables.get(ws.title)
            if source is None:
                return "UNABLE", "尚不能从原件确认照片列中的图像。"
            found = False
            for region, _image in image_regions(self, source):
                if region[3] >= column >= region[1] and region[2] >= first and region[0] <= last:
                    ctx.note_cell(ws.title, region[0], region[1], excerpt="此位置有原件中可解码的嵌入图像对象；本条只核实图片存在，不认证照片内容。")
                    found = True
            if not found:
                return "UNABLE", "照片列尚未取得可核实的图像，需核对原件。"
        return "PASS", "每份已定位报告的照片列均有图像对象。"

    @rule("实物确认列内容检查")
    def rule_6(self, ctx):
        for value in self._column_values(ctx, "实物确认"):
            if self._label_key(value) not in {"有", "无"}:
                return "NG", "实物确认只允许有或无。"
        return "PASS", "实物确认取值已逐行核实。"

    @rule("编制与审核字段检查")
    def rule_5(self, ctx):
        # Source clause 5 requires nonempty values. Global image byte counts do
        # not prove that either of these particular fields has been filled.
        Table(ctx).field_block("模具验收头部字段区").expect("编制字段", "审核字段", context="理想模具编制/审核")
        return "PASS", ""

    def precheck(self):
        def block(ctx):
            return Table(ctx).field_block("模具验收头部字段区")

        checks = [
            precheck_fact(self, "L10:S1", lambda ctx: block(ctx).expect("零件供应商名称字段")),
            precheck_fact(self, "L10:S2", lambda ctx: block(ctx).expect("提交日期字段")),
        ]
        for item, name in [
            ("L10:S3", "工艺类型"),
            ("L10:S4", "一级分总成零件名称"),
            ("L10:S5", "分总成组件或分序模具名称"),
            ("L10:S6", "模具制造供应商"),
            ("L10:S7", "模具存放地点"),
            ("L10:S8", "零件质量评估"),
            ("L10:S9", "模具分供方及实物VS数据&标准验收"),
            ("L10:S10", "模具量产性能评估"),
            ("L10:S11", "模具维保评估"),
        ]:
            checks.append(precheck_fact(self, item, lambda ctx, name=name: columns_present(ctx, "模具验收报告", "模具验收主数据区", (name,)), code="B05"))
        checks.extend(
            [
                precheck_fact(self, "L10:S12", self.rule_3, code="W03"),
                precheck_fact(self, "L10:S13", self.rule_4, code="W01"),
                precheck_fact(self, "L10:S14", self.rule_5),
                precheck_fact(self, "L10:S15", self.rule_6, code="W03"),
            ]
        )
        return checks
