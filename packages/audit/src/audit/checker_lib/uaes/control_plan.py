"""UAES CP row facts and same-round source reconciliation."""

import re

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date

from .native import MissingFact, UaesSourceAgent, UnknownFact, any_row, blank, key

_OP = ("工序名称", "操作描述", "工序名称/操作描述", "过程名称/操作描述")
_CHAR = ("产品/过程规格/公差", "产品特性", "过程特性", "特性名称", "产品", "过程")
_SPLIT = {"product": ("产品特性", "产品"), "process": ("过程特性", "过程")}


def _stage(value):
    """Prototype and pre-launch first: 试生产 also contains 生产."""
    text = key(value)
    if any(term in text for term in ("样件", "试生产", "prototype", "protype", "prelaunch", "pre-launch")):
        return "pre-production"
    if any(term in text for term in ("量产", "生产", "sop", "production")):
        return "production"
    return None


class UaesControlPlanAgent(UaesSourceAgent):
    def rows(self, extra=None, *, defer_unknown=False, continuation=True):
        # An operation's later rows leave its name blank; their characteristics still belong to it.
        return self.native().rows({"operation": _OP, **(extra or {})}, title=("控制计划", "control plan"), anchor=("operation",), defer_unknown=defer_unknown, continuation=continuation)

    def characteristics(self):
        """Templates put product and process characteristics in two columns or in one."""
        facts = self.native()
        try:
            facts.columns(facts.table(("控制计划", "control plan")), {"operation": _OP, **_SPLIT})
            return _SPLIT
        except (MissingFact, UnknownFact):
            return {"characteristic": _CHAR}

    @staticmethod
    def texts(row, names):
        return [str(row[name] or "") for name in names]

    @rule("全部工序生产资源")
    def rule_1(self):
        # Resources are stated once per operation; its continuation rows leave them blank.
        rows = self.rows({"resources": ("机器装置夹具工装", "机器/装置/夹具/工装", "生产资源", "机器设备工装", "生产设备", "设备/工装", "设备工装")}, defer_unknown=True, continuation=False)
        unresourced = []

        def lacks_resources(row):
            # Reading the operation first keeps a row of unknown scope undecided.
            operation = row["operation"]
            if blank(row["resources"]):
                unresourced.append(operation)
                return True
            return False

        if any_row(rows, lacks_resources):
            return "NG", "控制计划存在未说明生产资源的工序：" + str(unresourced[0])
        return "PASS", "全部工序的对应生产资源已读取。"

    @rule("生产追溯及先进先出实际措施")
    def rule_2(self):
        return self.visual_result(
            {"traceability": "控制计划说明可执行的零件生产过程追溯措施，而非只有追溯性标题。", "fifo": "控制计划说明可执行的先进先出措施，并能支持生产过程先进先出；只有FIFO术语不算。"},
            "实际生产追溯和先进先出措施",
        )

    def plan_type(self):
        """The one checked type: a form checkbox caption, or a typed field value."""
        from audit.checker_lib.excel import load_workbook
        from audit.engine.rule_engine.excel_ops import DefaultExcelOps

        path = str(self.audit_file.file_url)
        if path.lower().endswith((".xlsx", ".xlsm")):
            workbook = load_workbook(path)
            try:
                ops = DefaultExcelOps()
                labels = [label for sheet in workbook.worksheets for label in (ops.get_checkbox_labels(sheet, path) or [])]
            finally:
                workbook.close()
            checked = [caption for caption, state in labels if state]
            if labels:
                if len(checked) != 1:
                    raise UnknownFact("控制计划类型复选框没有唯一的实际勾选。")
                self.witness({"kind": "xlsx_form_control", "caption": checked[0]}, checked[0])
                return checked[0]
        # A cell can list every type with its own mark, e.g. "■样件 □试生产 □生产".
        facts = self.native()
        for value, locator in facts.observations():
            options = re.findall(r"([■☑✓√✔□☐])\s*([^■☑✓√✔□☐]+)", str(value))
            if len(options) >= 2 and any(_stage(text) for _mark, text in options):
                checked = [text.strip() for mark, text in options if mark not in "□☐"]
                if len(checked) != 1:
                    raise UnknownFact("控制计划类型选项没有唯一的实际勾选。")
                facts.witness(locator, value, facts.version)
                return checked[0]
        return facts.field(("控制计划类型", "类型", "阶段"))

    @rule("量产SOP类型及真实受控章")
    def rule_3(self):
        value = self.plan_type()
        stage = _stage(value)
        if stage is None:
            raise UnknownFact("尚不能从真实选择确认控制计划类型为量产/SOP。")
        if stage != "production":
            return "NG", f"控制计划类型为“{value}”，不是量产/SOP。"
        return self.controlled_stamp()

    @rule("特性类型列实际特性标记")
    def rule_4(self):
        rows = self.rows({"mark": ("特性类型", "特殊特性", "特殊/特性/分类")}, defer_unknown=True)
        if not any_row(rows, lambda row: not blank(row["mark"])):
            return "NG", "控制计划特性类型/特殊特性列没有实际标记。"
        return "PASS", "已读取实际特性标记。"

    @rule("跑道尺寸过程能力控制")
    def rule_5(self):
        names = self.characteristics()
        rows = self.rows({**names, "control": ("控制方法", "控制手段")})
        found = False
        for row in rows:
            if any("跑道" in text for text in self.texts(row, names)):
                found = True
                if not any(term in key(row["control"]) for term in ("spc", "x-r", "xbar-r", "x̄-r")):
                    return "NG", "跑道尺寸没有对应X-R图/SPC过程能力管控。"
        if not found:
            raise UnknownFact("尚不能确认跑道尺寸的适用范围，需核对原件。")
        return "PASS", "全部已识别跑道尺寸都有X-R/SPC过程能力管控。"

    @rule("CP身份字段图纸身份及最后更改日期")
    def rule_6(self):
        # The clause fails without a drawing, however the identity fields read.
        drawing = self.related(("图纸", "drawing"), same=("project", "part", "name"))
        facts = self.native()
        for aliases in [("供应商名称",), ("供应商代码",), ("编制人",), ("版本号", "版本"), ("零件名称",), ("零件号",)]:
            facts.field(aliases)
        facts.field(("受控日期", "创建日期", "修订日期"))
        drawing_date = normalize_excel_date(drawing.field(("日期", "图纸日期")))
        changes = facts.rows({"date": ("变更日期", "更改日期", "修订日期")}, title=("变更履历", "修订履历"), anchor=("date",))
        dates = [normalize_excel_date(row["date"]) for row in changes]
        if drawing_date is None or any(item is None for item in dates):
            raise UnknownFact("图纸日期或CP变更履历日期无法可靠读取。")
        last_date = dates[-1]
        if last_date is None:
            raise UnknownFact("CP最后一条更改日期无法读取。")
        if last_date <= drawing_date:
            return "NG", "CP最后一条变更履历日期没有晚于关联图纸日期。"
        return "PASS", "CP身份字段及同项目零件图纸一致，最后更改日期晚于图纸日期。"

    def cover(self, related, *, source_aliases, marked=False, outgoing=False, kind=None, require_spc=False):
        aliases = {"name": source_aliases}
        if marked:
            aliases["mark"] = ("特殊特性", "分类", "重要特性", "特性类别")
        if kind:
            aliases["kind"] = ("特性类别", "特性类型", "类别")
        source = related.rows(aliases, anchor=("name",))
        names = self.characteristics()
        target = self.rows({**names, **({"control": ("控制方法", "控制手段")} if require_spc else {})})
        count = 0
        for row in source:
            if marked and blank(row["mark"]):
                continue
            if kind and kind not in str(row["kind"]):
                continue
            count += 1
            matches = [
                item for item in target if key(row["name"]) in {key(text) for text in self.texts(item, names)} and (not outgoing or any(term in str(item["operation"]) for term in ("出货", "GP12")))
            ]
            if not matches:
                raise UnknownFact("CP尚不能确认覆盖关联要求：" + str(row["name"]))
            if require_spc and not any("spc" in key(item["control"]) for item in matches):
                return "NG", "图纸特殊尺寸的对应CP行没有SPC管控。"
        if not count:
            raise UnknownFact("关联原件没有可确认的适用项目，不能按空范围通过。")
        return "PASS", "已逐项核实同批同零件关联要求在CP中的对应内容。"

    @rule("PFD PFMEA PGQP1全部工序覆盖")
    def rule_7(self):
        flow, pfmea, plan = self.related_all(("过程流程图",), ("PFMEA",), ("采购件质量计划", "PGQP1"), same=("part",))
        plan.require_text("PGQP1")
        target = self.rows()
        for related, aliases in [(flow, ("工序", "工序名称")), (pfmea, ("工序/功能/要求", "过程项", "过程步骤")), (plan, ("工艺", "工序"))]:
            source = related.rows({"name": aliases}, anchor=("name",))
            for row in source:
                if not any(key(item["operation"]) == key(row["name"]) for item in target):
                    raise UnknownFact("CP尚不能确认覆盖关联工序：" + str(row["name"]))
        return "PASS", "三类关联原件的全部工序均已在CP对应。"

    @rule("PFD PFMEA PGQP1全部特殊特性覆盖")
    def rule_8(self):
        for related in self.related_all(("过程流程图",), ("PFMEA",), ("采购件质量计划", "PGQP1"), same=("part",)):
            result = self.cover(related, source_aliases=("功能/要求", "产品特性", "过程特性", "特性名称"), marked=True)
            if result[0] != "PASS":
                return result
        return "PASS", "三类关联原件全部特殊/重要特性已在CP对应。"

    @rule("PGQP1尺寸类特性出货检验覆盖")
    def rule_9(self):
        related = self.related(("采购件质量计划", "PGQP1"), same=("part",))
        related.require_text("PGQP1")
        return self.cover(related, source_aliases=_CHAR, outgoing=True, kind="尺寸")

    @rule("PGQP1产品审核型式试验覆盖")
    def rule_10(self):
        related = self.related(("采购件质量计划", "PGQP1"), same=("part",))
        related.require_text("PGQP1")
        return self.cover(related, source_aliases=("型式试验", "试验项目"))

    @rule("图纸CPK特殊尺寸对应SPC控制")
    def rule_11(self):
        related = self.related(("图纸", "drawing"), same=("project", "part"))
        return self.cover(related, source_aliases=("尺寸", "尺寸要求", "特性名称"), marked=True, kind="CPK", require_spc=True)

    @rule("图纸全部特殊尺寸和要求覆盖")
    def rule_12(self):
        related = self.related(("图纸", "drawing"), same=("project", "part"))
        return self.cover(related, source_aliases=("尺寸", "尺寸要求", "特性名称", "要求"), marked=True)
