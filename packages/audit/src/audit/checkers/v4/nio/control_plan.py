"""N03 compares explicit source fields on verified current-round originals."""

from audit.checkers.v3.control_plan_agent import ControlPlanAgent as StageReader
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v4.pfmea_local import _identity, _step_key
from audit.kinds import PpapDocumentType as T

from .tables import NioTableAgent

_CP = {
    "number": ["零件/工序号"],
    "name": ["工序名称/操作描述"],
    "kind": ["特殊特性分类"],
    "product": ["产品"],
    "process": ["过程"],
    "value": ["产品/工序规范/公差"],
    "method": ["评价测量技术"],
    "frequency": ["频率", "频次"],
    "quantity": ["数量"],
}
_SCL = {
    "class": ["特性分类"],
    "description": ["特性描述"],
    "value": ["特性值"],
    "kind": ["特殊特性种类", "特殊特性类别"],
    "method": ["控制方法（制造过程）"],
    "frequency": ["频次"],
    "quantity": ["样本数"],
}


class NioControlPlanAgent(NioTableAgent):
    DOC_TYPE = T.CONTROL_PLAN
    SHEET_KEYWORD = "控制计划"

    def _stages(self, ctx):
        stages = set()
        for sheet in self._sheets(ctx):
            for cells in sheet.iter_rows(max_row=min(sheet.max_row, 40)):
                for cell in cells:
                    text = str(cell.value or "")
                    if any(mark in text for mark in ("■", "☑", "✓", "√", "✔", "☒", "▄")):
                        ctx.note_cell(sheet.title, cell.row, cell.column)
                        stages.update(StageReader._extract_selected_stages_from_cell(text))
        return stages

    @rule("勾选生产阶段", clause_id="1")
    def rule_1(self, ctx):
        return self._done(ctx) if "production" in self._stages(ctx) else ("NG", "未确认勾选SOP或生产。")

    @rule("特性分类对应产品或过程", clause_id="2")
    def rule_2(self, ctx):
        rows = self._data(ctx, {key: _CP[key] for key in ("kind", "product", "process")})
        applicable = False
        for row in rows:
            if row["kind"]:
                applicable = True
                if not (row["product"] or row["process"]):
                    return "NG", "特殊特性分类已填写，但同行产品及过程均为空。"
        return self._done(ctx, applicable)

    @rule("特殊特性的控制方法", clause_id="3")
    def rule_3(self, ctx):
        rows = self._data(ctx, {"kind": _CP["kind"], "control": ["控制方法"]})
        applicable = False
        for row in rows:
            if row["kind"] in {"关键特殊特性", "重要特殊特性", "[SC]", "[CC]"}:
                applicable = True
                if not row["control"]:
                    return "NG", "关键或重要特殊特性未填写控制方法。"
        return self._done(ctx, applicable)

    @rule("全部频率非空", clause_id="4")
    def rule_4(self, ctx):
        rows = self._data(ctx, {"number": _CP["number"], "name": _CP["name"], "frequency": _CP["frequency"]})
        active = [row for row in rows if row["number"] or row["name"]]
        if not active:
            return "UNABLE", "没有可核实的工序数据行。"
        return ("NG", "存在未填写频率或频次的工序行。") if any(not row["frequency"] for row in active) else self._done(ctx)

    @rule("控制计划表头及唯一类型", clause_id="5")
    def rule_5(self, ctx):
        labels = [
            ("控制计划编号",),
            ("联系人contact/电话phone", "联系人/电话"),
            ("日期date（编制compi.）", "日期（编制）"),
            ("日期date（修订update）", "日期（修订）"),
            ("版本Rev.", "版本"),
            ("页数",),
            ("页码",),
            ("零件号",),
            ("零件名称",),
            ("多功能小组",),
            ("公司",),
            ("公司代码",),
            ("公司批准/日期",),
            ("其它批准/日期（如需要）",),
            ("顾客工程批准/日期(如需要）",),
            ("顾客质量批准/日期(如需要)",),
        ]
        for aliases in labels:
            if not self._field(ctx, *aliases):
                return "NG", f"缺少或未填写{aliases[0]}。"
        return self._done(ctx) if len(self._stages(ctx)) == 1 else ("NG", "控制计划类型须有且仅有一项被勾选。")

    @rule("流程图的全部工序覆盖", clause_id="6")
    def rule_6(self, ctx):
        own = self._data(ctx, {key: _CP[key] for key in ("number", "name")})
        counterpart = self._paired_data(ctx, T.PROCESS_FLOW_CHART, "过程流程图", {"number": ["工序号"], "name": ["工序名称"]})
        actual = {(_identity(row["number"]), _identity(row["name"])) for row in own if row["number"] or row["name"]}
        for row in counterpart:
            if (row["number"] or row["name"]) and (_identity(row["number"]), _identity(row["name"])) not in actual:
                return "NG", f"流程图工序未在控制计划覆盖：{row['number']} {row['name']}。"
        return self._done(ctx)

    @rule("PFMEA特殊特性逐工序一致", clause_id="7")
    def rule_7(self, ctx):
        own = self._data(ctx, {key: _CP[key] for key in ("number", "name", "kind")})
        counterpart = self._paired_data(ctx, T.PFMEA, "PFMEA", {"step": ["过程步骤"], "kind": ["特殊特性"]})
        applicable = False
        for row in counterpart:
            if not row["kind"]:
                continue
            applicable = True
            matches = [item for item in own if (_identity(item["number"]), _identity(item["name"])) == _step_key(row["step"])]
            if not matches or not any(item["kind"] and _identity(item["kind"]) == _identity(row["kind"]) for item in matches):
                return "NG", f"PFMEA工序{row['step']}的特殊特性未在控制计划同工序体现。"
        return self._done(ctx, applicable)

    @rule("特性清单逐项覆盖", clause_id="8")
    def rule_8(self, ctx):
        own = self._data(ctx, {key: _CP[key] for key in ("product", "process", "kind", "value")})
        counterpart = self._paired_data(ctx, T.PROCESS_SPECIAL_CHARACTERISTIC_LIST, "特殊特性清单", {key: _SCL[key] for key in ("class", "description", "value", "kind")})
        actual = {
            (_identity(category), _identity(item["kind"]), _identity(item[field]), _identity(item["value"]))
            for item in own
            for field, category in (("product", "产品"), ("process", "过程"))
            if item["kind"] and item[field]
        }
        applicable = False
        for item in counterpart:
            if not (item["description"] or item["value"]):
                continue
            applicable = True
            if tuple(_identity(item[key]) for key in ("class", "kind", "description", "value")) not in actual:
                return "NG", f"特性清单项目未在控制计划覆盖：{item['description']}：{item['value']}。"
        return self._done(ctx, applicable)

    @rule("PFMEA四类措施覆盖", clause_id="9")
    def rule_9(self, ctx):
        own = self._data(ctx, {key: _CP[key] for key in ("name", "method")})
        controls = ("现行预防控制", "现行探测控制", "预防控制", "探测措施")
        counterpart = self._paired_data(ctx, T.PFMEA, "PFMEA", {"step": ["过程步骤"], **{key: [key] for key in controls}})
        applicable = False
        for item in counterpart:
            matches = [row for row in own if _identity(row["name"]) == _step_key(item["step"])[1]]
            for control in controls:
                if item[control]:
                    applicable = True
                    if not matches or not any(_identity(item[control]) in _identity(row["method"]) for row in matches):
                        return "NG", f"PFMEA工序{item['step']}的{control}未在控制计划评价测量技术中体现。"
        return self._done(ctx, applicable)

    @rule("特性清单方法数量频率一致", clause_id="10")
    def rule_10(self, ctx):
        own = self._data(ctx, {key: _CP[key] for key in ("product", "process", "kind", "method", "frequency", "quantity")})
        counterpart = self._paired_data(ctx, T.PROCESS_SPECIAL_CHARACTERISTIC_LIST, "特殊特性清单", {key: _SCL[key] for key in ("class", "description", "method", "frequency", "quantity")})
        applicable = False
        for item in own:
            if not item["kind"]:
                continue
            for field, category in (("product", "产品"), ("process", "过程")):
                if not item[field]:
                    continue
                applicable = True
                matches = [row for row in counterpart if _identity(row["class"]) == _identity(category) and _identity(row["description"]) == _identity(item[field])]
                if not matches or not any(all(_identity(row[key]) == _identity(item[key]) for key in ("method", "frequency", "quantity")) for row in matches):
                    return "NG", f"特性{item[field]}的评价测量技术、数量或频率与特性清单不一致。"
        return self._done(ctx, applicable)
