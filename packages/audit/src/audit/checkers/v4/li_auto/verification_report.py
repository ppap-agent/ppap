"""L03 is an untamplated report with literal verdict/type tokens."""

import unicodedata

from audit.checkers.v3.dpvr_excel_agent import DpvrExcelAgent
from audit.checkers.v3.excel_agent import rule
from audit.engine.rule_engine import Decision, GuardStop, Sheet

from .facts import NativeFacts, compact, image_regions

_LABELS = ("文件编号", "报告编号", "编号", "实验判定", "试验判定", "判定", "试验结论", "实验结论", "结论", "试验类型", "样品数量", "样本数量")


def _key(value):
    return compact(unicodedata.normalize("NFKC", str(value or ""))).casefold().rstrip(":")


class LiAutoVerificationReportAgent(NativeFacts, DpvrExcelAgent):
    EXPECTED_RULE_CATEGORIES = ("文件编号审核", "试验结论审核", "试验类型审核", "印章审核", "试验图片审核", "样品数量审核")

    def _field(self, ctx, aliases):
        values = set()
        keys = {_key(value) for value in aliases}
        known_labels = {_key(value) for value in _LABELS}
        for ws in ctx.workbook.worksheets:
            sheet = Sheet(ctx, ws)
            for cells in ws.iter_rows():
                for raw in cells:
                    text = unicodedata.normalize("NFKC", str(raw.value or "")).strip()
                    if ":" in text:
                        label, value = text.split(":", 1)
                        if _key(label) in keys and value.strip():
                            sheet.cell(raw.row, raw.column).value()
                            values.add(value.strip())
                            continue
                    if _key(raw.value) not in keys:
                        continue
                    sheet.cell(raw.row, raw.column).value()
                    merged = ctx.ops.get_detailed_merge_info(ws, raw.row, raw.column)
                    right_column = merged.max_col + 1 if merged else raw.column + 1
                    next_row = merged.max_row + 1 if merged else raw.row + 1
                    right = sheet.cell(raw.row, right_column).value()
                    if _key(right) in known_labels:
                        value = sheet.cell(next_row, raw.column).value()
                    else:
                        value = right
                    if value is not None and str(value).strip() and _key(value) not in known_labels:
                        values.add(str(value).strip())
        if len(values) > 1:
            raise GuardStop(Decision.UNABLE, "报告中同一字段有多个不同取值，需确认作用范围。")
        return next(iter(values), None)

    @rule("文件编号审核")
    def rule_1(self, ctx):
        return ("PASS", "") if self._field(ctx, ("文件编号", "报告编号", "编号")) else ("NG", "缺少或未填写文件/报告编号。")

    @rule("试验结论审核")
    def rule_2(self, ctx):
        value = self._field(ctx, ("实验判定", "试验判定", "判定", "试验结论", "实验结论", "结论"))
        if value is None:
            return "UNABLE", "尚不能定位试验结论。"
        return ("PASS", "") if value in {"符合", "通过", "合格", "P", "pass"} else ("NG", "结论须严格为符合、通过、合格、P或pass；不翻译或扩展近义词。")

    @rule("试验类型审核")
    def rule_3(self, ctx):
        value = self._field(ctx, ("试验类型",))
        return ("PASS", "") if compact(value).casefold() in {"dv", "pv"} else ("NG", "试验类型须严格为DV或PV。")

    @rule("印章审核")
    def rule_4(self, ctx):
        return "UNABLE", "原件印章内容尚未核实；颜色或图像对象不等于真实印章。"

    @rule("试验图片审核")
    def rule_5(self, ctx):
        phases = [("试验前", "实验前"), ("试验中", "实验中"), ("试验后", "实验后")]
        confirmed = set()
        for ws in ctx.workbook.worksheets:
            source = self.native_tables.get(ws.title)
            if source is None:
                continue
            labels = []
            for cells in ws.iter_rows():
                for cell in cells:
                    for index, aliases in enumerate(phases):
                        if _key(cell.value) in {_key(value) for value in aliases}:
                            ctx.note_cell(ws.title, cell.row, cell.column)
                            labels.append((index, cell.row, cell.column))
            for region, _ in image_regions(self, source):
                candidates = [(index, row, col) for index, row, col in labels if row <= region[0] <= row + 6 and region[1] <= col <= region[3]]
                if candidates:
                    nearest = max(row for _, row, _ in candidates)
                    phases_for_object = {index for index, row, _ in candidates if row == nearest}
                    if len(phases_for_object) == 1:
                        confirmed.update(phases_for_object)
        if confirmed == {0, 1, 2}:
            return "UNABLE", "三个阶段已定位到图像对象，但实际试验照片内容尚未核实。"
        return "UNABLE", "试验前、中、后三阶段图像尚未全部定位；文字或非空占位不能代替图片。"

    @rule("样品数量审核")
    def rule_6(self, ctx):
        return ("PASS", "") if self._field(ctx, ("样品数量", "样本数量")) else ("NG", "缺少或未填写样品/样本数量。")

    def precheck(self):
        from .precheck_l1 import precheck_report

        return precheck_report(self)
