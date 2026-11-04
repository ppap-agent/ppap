"""UAES CP row facts and same-round source reconciliation."""

from audit.checkers.v3.excel_agent import rule
from audit.engine.checkers.date_utils import normalize_excel_date

from .native import UaesSourceAgent, UnknownFact, blank, key

_OP = ("工序名称", "操作描述", "工序名称/操作描述")
_CHAR = ("产品/过程规格/公差", "产品特性", "过程特性", "特性名称", "产品", "过程")


class UaesControlPlanAgent(UaesSourceAgent):
    def rows(self, extra=None):
        return self.native().rows({"operation": _OP, **(extra or {})}, title=("控制计划", "control plan"), anchor=("operation",))

    @rule("全部工序生产资源")
    def rule_1(self):
        rows = self.rows({"resources": ("机器装置夹具工装", "机器/装置/夹具/工装", "生产资源", "机器设备工装")})
        for row in rows:
            if blank(row["resources"]):
                return "NG", "控制计划存在未说明生产资源的工序：" + str(row["operation"])
        return "PASS", "全部工序的对应生产资源已读取。"

    @rule("生产追溯及先进先出实际措施")
    def rule_2(self):
        return self.visual_result(
            {"traceability": "控制计划说明可执行的零件生产过程追溯措施，而非只有追溯性标题。", "fifo": "控制计划说明可执行的先进先出措施，并能支持生产过程先进先出；只有FIFO术语不算。"},
            "实际生产追溯和先进先出措施",
        )

    @rule("量产SOP类型及真实受控章")
    def rule_3(self):
        value = self.native().field(("控制计划类型", "类型", "阶段"))
        if key(value) not in {"量产", "sop", "量产sop", "production"}:
            raise UnknownFact("尚不能从真实选择确认控制计划类型为量产/SOP。")
        return self.controlled_stamp()

    @rule("特性类型列实际特性标记")
    def rule_4(self):
        rows = self.rows({"mark": ("特性类型", "特殊特性")})
        if not any(not blank(row["mark"]) for row in rows):
            return "NG", "控制计划特性类型/特殊特性列没有实际标记。"
        return "PASS", "已读取实际特性标记。"

    @rule("跑道尺寸过程能力控制")
    def rule_5(self):
        rows = self.rows({"characteristic": _CHAR, "control": ("控制方法", "控制手段", "频率")})
        found = False
        for row in rows:
            if "跑道" in str(row["characteristic"]):
                found = True
                if not any(term in key(row["control"]) for term in ("spc", "x-r", "xbar-r", "x̄-r")):
                    return "NG", "跑道尺寸没有对应X-R图/SPC过程能力管控。"
        if not found:
            raise UnknownFact("尚不能确认跑道尺寸的适用范围，需核对原件。")
        return "PASS", "全部已识别跑道尺寸都有X-R/SPC过程能力管控。"

    @rule("CP身份字段图纸身份及最后更改日期")
    def rule_6(self):
        facts = self.native()
        for aliases in [("供应商名称",), ("供应商代码",), ("编制人",), ("版本号", "版本"), ("零件名称",), ("零件号",)]:
            facts.field(aliases)
        facts.field(("受控日期", "创建日期", "修订日期"))
        drawing = self.related(("图纸", "drawing"), require_name=True)
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
        target = self.rows({"name": _CHAR, **({"control": ("频率", "控制方法")} if require_spc else {})})
        count = 0
        for row in source:
            if marked and blank(row["mark"]):
                continue
            if kind and kind not in str(row["kind"]):
                continue
            count += 1
            matches = [item for item in target if key(item["name"]) == key(row["name"]) and (not outgoing or any(term in str(item["operation"]) for term in ("出货", "GP12")))]
            if not matches:
                raise UnknownFact("CP尚不能确认覆盖关联要求：" + str(row["name"]))
            if require_spc and not any("spc" in key(item["control"]) for item in matches):
                return "NG", "图纸特殊尺寸的对应CP行没有SPC管控。"
        if not count:
            raise UnknownFact("关联原件没有可确认的适用项目，不能按空范围通过。")
        return "PASS", "已逐项核实同批同零件关联要求在CP中的对应内容。"

    @rule("PFD PFMEA PGQP1全部工序覆盖")
    def rule_7(self):
        target = self.rows()
        for terms, aliases in [(("过程流程图",), ("工序", "工序名称")), (("PFMEA",), ("工序/功能/要求", "过程项", "过程步骤")), (("采购件质量计划", "PGQP1"), ("工艺", "工序"))]:
            related = self.related(terms)
            if "PGQP1" in terms:
                related.require_text("PGQP1")
            source = related.rows({"name": aliases}, anchor=("name",))
            for row in source:
                if not any(key(item["operation"]) == key(row["name"]) for item in target):
                    raise UnknownFact("CP尚不能确认覆盖关联工序：" + str(row["name"]))
        return "PASS", "三类关联原件的全部工序均已在CP对应。"

    @rule("PFD PFMEA PGQP1全部特殊特性覆盖")
    def rule_8(self):
        for terms in [("过程流程图",), ("PFMEA",), ("采购件质量计划", "PGQP1")]:
            related = self.related(terms)
            result = self.cover(related, source_aliases=("功能/要求", "产品特性", "过程特性", "特性名称"), marked=True)
            if result[0] != "PASS":
                return result
        return "PASS", "三类关联原件全部特殊/重要特性已在CP对应。"

    @rule("PGQP1尺寸类特性出货检验覆盖")
    def rule_9(self):
        related = self.related(("采购件质量计划", "PGQP1"))
        related.require_text("PGQP1")
        return self.cover(related, source_aliases=_CHAR, outgoing=True, kind="尺寸")

    @rule("PGQP1产品审核型式试验覆盖")
    def rule_10(self):
        related = self.related(("采购件质量计划", "PGQP1"))
        related.require_text("PGQP1")
        return self.cover(related, source_aliases=("型式试验", "试验项目"))

    @rule("图纸CPK特殊尺寸对应SPC控制")
    def rule_11(self):
        related = self.related(("图纸", "drawing"))
        return self.cover(related, source_aliases=("尺寸", "尺寸要求", "特性名称"), marked=True, kind="CPK", require_spc=True)

    @rule("图纸全部特殊尺寸和要求覆盖")
    def rule_12(self):
        related = self.related(("图纸", "drawing"))
        return self.cover(related, source_aliases=("尺寸", "尺寸要求", "特性名称", "要求"), marked=True)
