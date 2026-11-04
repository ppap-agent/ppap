"""N05 checks the customer source, including its explicit clause-3 wording."""

from audit.checkers.v3.excel_agent import rule
from audit.checkers.v4.pfmea_local import _identity
from audit.kinds import PpapDocumentType
from audit.precheck_facts import checker_fact

from .tables import NioTableAgent


class NioProcessFlowAgent(NioTableAgent):
    DOC_TYPE = PpapDocumentType.PROCESS_FLOW_CHART
    SHEET_KEYWORD = "过程流程图"

    @rule("流程图表头字段", clause_id="1")
    def rule_1(self, ctx):
        for labels in [("零件号", "零件编号"), ("零件名称",), ("项目车型",), ("供应商名称",), ("代码",), ("编制日期",), ("修订日期",), ("版本",), ("团队",)]:
            if not self._field(ctx, *labels):
                return "NG", f"缺少或未填写{labels[0]}。"
        return self._done(ctx)

    def _characteristics(self, ctx, *, require_process=False):
        aliases = {key: [key] for key in ("产品特殊特性", "产品特性类别")}
        if require_process:
            aliases.update({key: [key] for key in ("过程特殊特性", "过程特性类别")})
        rows = self._data(ctx, aliases)
        for row in rows:
            if row["产品特性类别"] and not row["产品特殊特性"]:
                return "NG", "产品特性类别已填写，但同行产品特殊特性为空。"
        return self._done(ctx, any(row["产品特性类别"] for row in rows))

    @rule("产品特性与类别", clause_id="2")
    def rule_2(self, ctx):
        return self._characteristics(ctx)

    @rule("源第三条的过程列及产品条件", clause_id="3")
    def rule_3(self, ctx):
        # The original explicitly requires process columns, then states a
        # product-column condition. Do not silently substitute a new rule.
        return self._characteristics(ctx, require_process=True)

    @rule("每道工序操作描述", clause_id="4")
    def rule_4(self, ctx):
        rows = self._data(ctx, {key: [key] for key in ("工序名称", "操作描述")})
        for row in rows:
            if row["工序名称"] and not row["操作描述"]:
                return "NG", f"工序“{row['工序名称']}”缺少操作描述。"
        return self._done(ctx, any(row["工序名称"] for row in rows))

    @rule("每道工序图例", clause_id="5")
    def rule_5(self, ctx):
        kinds = ("制造", "运输", "存储", "检验", "返工", "报废")
        rows = self._data(ctx, {key: [key] for key in ("工序名称", *kinds)})
        for row in rows:
            if row["工序名称"] and not any(row[key] for key in kinds):
                return "NG", f"工序“{row['工序名称']}”的六类图例均为空。"
        return self._done(ctx, any(row["工序名称"] for row in rows))

    def _names(self, ctx):
        return [row["工序名称"] for row in self._data(ctx, {"工序名称": ["工序名称"]}) if row["工序名称"]]

    @rule("进料检验", clause_id="6")
    def rule_6(self, ctx):
        return self._done(ctx) if any("进料检验" in name or "IQC" in name.upper() for name in self._names(ctx)) else ("NG", "缺少进料检验或IQC工序。")

    @rule("出货检验", clause_id="7")
    def rule_7(self, ctx):
        return self._done(ctx) if any("出货" in name or any(token in name.upper() for token in ("FQC", "OQC")) for name in self._names(ctx)) else ("NG", "缺少出货、FQC或OQC工序。")

    @rule("返工下一行检验", clause_id="8")
    def rule_8(self, ctx):
        rows = self._data(ctx, {"工序名称": ["工序名称"]})
        applicable = False
        for index, row in enumerate(rows):
            name = row["工序名称"]
            if "返工" in name or "返修" in name:
                applicable = True
                if index + 1 >= len(rows) or int(rows[index + 1]["__row_number"]) != int(row["__row_number"]) + 1 or not any(token in rows[index + 1]["工序名称"] for token in ("检验", "重检")):
                    return "NG", f"工序“{name}”的下一行不是检验或重检。"
        return self._done(ctx, applicable)

    @rule("特性清单逐项覆盖", clause_id="9")
    def rule_9(self, ctx):
        rows = self._data(ctx, {key: [key] for key in ("产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别")})
        listed = self._paired_data(
            ctx,
            PpapDocumentType.PROCESS_SPECIAL_CHARACTERISTIC_LIST,
            "特殊特性清单",
            {"class": ["特性分类"], "description": ["特性描述"], "value": ["特性值"], "kind": ["特殊特性种类", "特殊特性类别"]},
        )
        actual = {
            (_identity(category), _identity(row[category + "特性类别"]), _identity(row[category + "特殊特性"]))
            for row in rows
            for category in ("产品", "过程")
            if row[category + "特性类别"] and row[category + "特殊特性"]
        }
        applicable = False
        for row in listed:
            if not (row["description"] or row["value"]):
                continue
            applicable = True
            feature = f"{row['description']}：{row['value']}"
            if (_identity(row["class"]), _identity(row["kind"]), _identity(feature)) not in actual:
                return "NG", f"特殊特性清单项目未在流程图覆盖：{feature}。"
        return self._done(ctx, applicable)

    def precheck(self):
        kinds = ("工序名称", "制造", "运输", "存储", "检验", "返工", "报废")
        return [
            checker_fact(self, "N05:S1", self._sheet_present, code="B05"),
            checker_fact(self, "N05:S2", self.rule_1, code="B05"),
            checker_fact(self, "N05:S3", self.rule_2, code="B05"),
            checker_fact(self, "N05:S4", self._process_precheck, code="B05"),
            checker_fact(self, "N05:S5", self.rule_4, code="B05"),
            checker_fact(self, "N05:S6", lambda ctx: self._columns(ctx, {key: [key] for key in kinds}), code="B05"),
            checker_fact(self, "N05:S7", self.rule_5, code="W06"),
            checker_fact(self, "N05:S8", self.rule_6, code="W01"),
            checker_fact(self, "N05:S9", self.rule_7, code="W01"),
            checker_fact(self, "N05:S10", self.rule_8, code="W06"),
            checker_fact(self, "N05:S11", self.rule_9, code="W01"),
        ]

    def _sheet_present(self, ctx):
        for sheet in self._sheets(ctx):
            ctx.note_cell(sheet.title, 1, 1)
        return self._done(ctx)

    def _process_precheck(self, ctx):
        # 00-A S4 explicitly uses process fields; the formal source's third
        # clause has different literal wording and remains a separate method.
        rows = self._data(ctx, {key: [key] for key in ("过程特殊特性", "过程特性类别")})
        for row in rows:
            if row["过程特性类别"] and not row["过程特殊特性"]:
                return "NG", "过程特性类别已填写，但同行过程特殊特性为空。"
        return self._done(ctx, any(row["过程特性类别"] for row in rows))
