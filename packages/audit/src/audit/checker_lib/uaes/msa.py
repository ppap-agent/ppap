"""UAES MSA plan coverage, gauge pairing and report conclusions."""

from audit.checker_lib.nio.excel_agent import rule

from .native import UaesSourceAgent, UnknownFact, blank, key

_COUNTING = ("人工", "目视", "目测", "通止规", "塞规", "塞尺")


class UaesMsaAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_msa

        return precheck_msa(self)

    PLAN = ("测量系统分析计划", "MSA计划表")

    @rule("MSA计划逐编号行除备注外内容完整")
    def rule_1(self):
        facts = self.native()
        table = facts.table(self.PLAN)
        start, columns = facts.columns(table, {"number": ("序号", "NO", "NO.")})
        required = []
        reader = facts.readers[table["id"]]
        for column in range(1, table["cols"] + 1):
            item = reader.cell(start, column)
            if item.get("state") != "known":
                raise UnknownFact("MSA计划表头无法完整识别。")
            if not blank(item.get("value")) and "备注" not in str(item["value"]):
                required.append(column)
        if len(required) < 2:
            raise UnknownFact("MSA计划未能确定完整表头范围。")
        headings = [key(facts.cell(table, start, column)) for column in required]
        count = 0
        for row in range(start + 1, table["rows"] + 1):
            serial = facts.cell(table, row, columns["number"])
            if any(key(serial).startswith(marker) for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                break
            values = [facts.cell(table, row, column) for column in required]
            if all(blank(value) for value in values) or [key(value) for value in values] == headings:
                continue
            count += 1
            if any(blank(value) for value in values):
                return "NG", f"MSA计划第{row}行存在非备注空字段。"
        if not count:
            raise UnknownFact("MSA计划没有可确认的编号行，不能按空范围通过。")
        return "PASS", "已逐编号行核实MSA计划除备注外的全部字段。"

    def report(self, *, kappa, gauge_number=None, gauge_name=None):
        terms = ("kappa",) if kappa else ("重复性", "再现性", "GRR")
        tables = self.native().tables(terms)
        reports = [self.native()] if tables else [self.related(terms)]
        matched = 0
        for facts in reports:
            for table in facts.tables(terms):
                title = (table["id"],)
                if gauge_number is not None and key(facts.field(("量具编号", "测量设备编号"), title=title)) != key(gauge_number):
                    continue
                if gauge_name is not None and key(facts.field(("量具名称", "测量设备名称"), title=title)) not in {key(gauge_name), key("综合检具")}:
                    continue
                matched += 1
                conclusion = facts.field(("结论", "最终结论", "conclusion"), title=title)
                if key(conclusion) != key("Acceptable" if kappa else "测量系统可以接受"):
                    return "NG", "Kappa/GRR报告最终结论不满足原规则。"
        if not matched:
            raise UnknownFact("尚未找到可唯一对应量具身份的Kappa/GRR报告。")
        return "PASS", "已核对量具身份及全部适用报告的最终结论。"

    @rule("计划每行量具类型编号与全部GRR/Kappa结论")
    def rule_2(self):
        facts = self.native() if self.native().tables(self.PLAN) else self.related(self.PLAN)
        rows = facts.rows({"name": ("量具名称", "测量设备名称"), "number": ("量具编号", "测量设备编号")}, title=self.PLAN, anchor=("number",))
        for row in rows:
            result = self.report(kappa=any(term in str(row["name"]) for term in _COUNTING), gauge_number=row["number"])
            if result[0] != "PASS":
                return result
        # In addition to plan matches, verify every supplied native report.
        for kappa, terms in [(True, ("kappa",)), (False, ("重复性", "再现性", "GRR"))]:
            for table in self.native().tables(terms):
                conclusion = self.native().field(("结论", "最终结论", "conclusion"), title=(table["id"],))
                if key(conclusion) != key("Acceptable" if kappa else "测量系统可以接受"):
                    return "NG", "存在非可接受结论的Kappa/GRR报告。"
        return "PASS", "MSA计划全部量具已按类型编号对应且全部报告结论可接受。"

    @rule("PGQP1重要特性计数型量具同编号Kappa")
    def rule_3(self):
        facts = self.related(("采购件质量计划", "PGQP1"))
        facts.require_text("PGQP1")
        rows = facts.rows({"mark": ("重要特性", "特殊特性", "特性分类"), "name": ("测量设备", "测量设备名称", "量具名称"), "number": ("量具编号", "测量设备编号")}, title=("PGQP1",), anchor=("name",))
        for row in rows:
            if not blank(row["mark"]) and any(term in str(row["name"]) for term in ("通止规", "塞规", "塞尺")):
                result = self.report(kappa=True, gauge_number=row["number"])
                if result[0] != "PASS":
                    return result
        return "PASS", "已完整读取PGQP1；适用计数量具均已对应同编号Kappa。"

    @rule("CP出货或GP12人工检测同名或综合检具Kappa")
    def rule_4(self):
        facts = self.related(("控制计划",))
        rows = facts.rows({"operation": ("工序名称", "操作描述", "工序名称/操作描述"), "method": ("评价测量技术", "评价/测量技术"), "gauge": ("量具名称", "测量设备")}, anchor=("operation",))
        for row in rows:
            if any(term in str(row["operation"]) for term in ("出货检验", "GP12")) and any(term in str(row["method"]) for term in ("人工", "目视", "目测")):
                result = self.report(kappa=True, gauge_name=row["gauge"])
                if result[0] != "PASS":
                    return result
        return "PASS", "已完整读取控制计划；适用人为出货检测均已对应同名或综合检具Kappa。"
