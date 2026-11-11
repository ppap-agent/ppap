"""UAES engineering requirements, evaluated on complete source scopes."""

from __future__ import annotations

from decimal import Decimal

from audit.checker_lib.nio.excel_agent import rule

from .native import MissingFact, UaesSourceAgent, UnknownFact, blank, header, key, number


class UaesProcessFlowAgent(UaesSourceAgent):
    @rule("关键工序及特性标识与图例")
    def rule_1(self):
        return self.visual_result(
            {
                # Definitions come from the remarks column of the UAES rule document.
                "key_operation": "全图至少有一道工序带实际关键工序标记：K、K1…、★、◆、加粗、底色、虚框等任一形式。",
                "characteristic_marks": "产品/过程特性中的关键、重要或特殊特性有实际符号标注（CC、SC、YC、YS、★等）；不能只有未标注的文字描述。",
                "legend": "原件图例或注释定义了上述每种关键标记的含义。",
            },
            "关键工序、特性标记和标记定义",
        )

    @rule("全图活动符号一致性与检验图例")
    def rule_2(self):
        return self.visual_result(
            {
                "standard_shapes": "全部工序活动（加工/操作、移动/搬运、存储/存取/贮存、检验、返工/返修、报废）用标准化流程图形表达，不能只是纯文字框或列表；各图形含义以本图图例为准。",
                "complete_legend": "图例覆盖了全图实际使用的全部符号。",
                "consistent": "同一活动类型在全图使用相同符号。",
                "inspection_shapes": "所有进货、过程、最终、巡检、自检活动使用检验符号，且可与图例核对。",
            },
            "全图标准符号、完整图例、一致性及检验符号",
        )

    @rule("过程流程图实际受控章")
    def rule_3(self):
        return self.controlled_stamp()


class UaesCapabilityAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_capacity

        return precheck_capacity(self)

    REPORTS = ("工艺能力认证", "过程能力认证", "process capability")
    READINGS = ("读数次数", "读数个数", "样本数量", "sample size", "number of readings")

    def reports(self):
        # A capability report states its reading count; a summary or directory sheet does not.
        tables = [
            table for table in self.native().tables(self.REPORTS) if any(item.get("state") == "known" and header(item.get("value"), self.READINGS, exact=True) for item in table["cells"].values())
        ]
        if not tables:
            raise UnknownFact("未能确定工艺能力认证报告的完整范围，不能按空报告通过。")
        return tables

    def report_value(self, table, aliases):
        return self.native().field(aliases, table=table, numeric=True, exact=True)

    @rule("所有能力认证报告的样本数与严格Cpk阈值")
    def rule_1(self):
        for table in self.reports():
            count_text = self.report_value(table, self.READINGS)
            count = number(count_text)
            if count != count.to_integral_value() or count < 0:
                raise UnknownFact("读数次数不是可确认的非负整数。")
            if count < 125:
                # The report's own reading count is the size of the measured region it analysed.
                return "NG", f"工艺能力认证报告“{table['id']}”读数次数为{count}，少于125。"
            cpk = number(self.report_value(table, ("Cpk", "Process Capability (Cpk)", "Process Capability Cpk")))
            if cpk <= Decimal("1.33"):
                return "NG", f"工艺能力认证报告“{table['id']}”Cpk为{cpk}，必须严格大于1.33。"
        return "PASS", "所有已确定的工艺能力认证报告满足样本数量和Cpk要求。"

    @rule("所有能力认证报告的严格Ppk阈值")
    def rule_2(self):
        for table in self.reports():
            ppk = number(self.report_value(table, ("Ppk", "Performance index (Ppk)", "Performance index Ppk")))
            if ppk <= Decimal("1.67"):
                return "NG", f"工艺能力认证报告“{table['id']}”Ppk为{ppk}，必须严格大于1.67。"
        return "PASS", "所有已确定的工艺能力认证报告Ppk严格大于1.67。"

    def summary_table(self, aliases):
        """The capability summary list, by its title or else by its result and target columns."""
        facts = self.native()
        titled = facts.tables(("能力值汇总", "能力汇总"))
        if titled:
            return facts.table(("能力值汇总", "能力汇总"))
        found = []
        for table in facts.tables():
            try:
                facts.columns(table, aliases)
            except (UnknownFact, MissingFact):
                continue
            found.append(table)
        if not found:
            raise MissingFact("文件中不存在能力值汇总清单（CPK/PPK结果值、目标值及反应计划）。")
        if len(found) != 1:
            raise UnknownFact("能力值汇总清单有多份候选，需人工确认。")
        return found[0]

    @rule("能力汇总逐行结果目标及不足项措施")
    def rule_3(self):
        facts = self.native()
        aliases = {
            "cpk": ("CPK结果值", "CPK结果", "结果(CPK)", "CPK"),
            "ppk": ("PPK结果值", "PPK结果", "结果(PPK)", "PPK"),
            "cpk_target": ("CPK目标值", "CPK目标", "目标(CPK)"),
            "ppk_target": ("PPK目标值", "PPK目标", "目标(PPK)"),
            "reaction": ("反应计划", "反应计划或备注"),
        }
        table = self.summary_table(aliases)
        start, _ = facts.columns(table, aliases)
        # Optional subject columns extend membership without becoming new
        # required source fields. Results must not decide whether a row exists.
        for name, labels in {"characteristic": ("特性名称", "特性描述"), "sequence": ("序号", "编号", "特性编号"), "remark": ("备注",)}.items():
            matches, anchors = [], set()
            for column in range(1, table["cols"] + 1):
                item = facts.readers[table["id"]].cell(start, column)
                # One merged header cell is one column, however many grid columns it spans.
                if header(item.get("value"), labels) and item.get("anchor", column) not in anchors:
                    anchors.add(item.get("anchor", column))
                    matches.append(column)
            if len(matches) > 1:
                raise UnknownFact("能力汇总业务主体列有多个候选，需确认原件范围。")
            if matches:
                aliases[name] = labels
        rows = facts.rows(aliases, table=table)
        checked = 0
        for row in rows:
            subjects = [row[name] for name in ("characteristic", "sequence") if name in row]
            boundary = subjects if any(not blank(value) for value in subjects) else [row["cpk"], row["ppk"]]
            if any(key(value).startswith(marker) for value in boundary for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                break
            checked += 1
            # Every stated result and target is read before deciding; a low CPK does not excuse an unread PPK.
            # A characteristic without a stated target ("/") has nothing to compare against.
            pairs = [(number(row[result]), number(row[target])) for result, target in (("cpk", "cpk_target"), ("ppk", "ppk_target")) if str(row[target]).strip() not in {"/", "-", "—"}]
            low = any(actual < wanted for actual, wanted in pairs)
            if low and blank(row["reaction"]) and ("remark" not in row or blank(row["remark"])):
                return "NG", f"能力汇总第{row.row}行低于目标且没有反应计划或备注。"
        if not checked:
            raise UnknownFact("能力汇总没有可确认的业务行，不能按空范围通过。")
        return "PASS", "全部能力汇总行达到目标，或对应不足项已有反应计划/备注。"

    @rule("能力目录覆盖PGQP1特殊特性")
    def rule_4(self):
        return self.pgqp_directory(process_only=False)

    @rule("能力目录覆盖PGQP1全部过程特性")
    def rule_5(self):
        return self.pgqp_directory(process_only=True)

    def pgqp_directory(self, *, process_only):
        facts = self.native()
        # The directory page is usually its own worksheet named 目录.
        if not facts.tables(("目录",)):
            facts.require_text("目录")
        directory = facts.table(("目录",))
        related = self.related(("采购件质量计划", "PGQP1"), same=("part",))
        related.require_text("PGQP1")
        aliases: dict[str, tuple[str, ...]] = {"name": ("特性名称", "特性描述", "产品/过程特性", "过程特性")}
        if process_only:
            aliases["type"] = ("特性类别", "特性类型", "类别")
        else:
            aliases["mark"] = ("特殊特性", "特殊特性标识", "特性分类")
        rows = related.rows(aliases, title=("PGQP1",), anchor=("name",))
        required = 0
        for row in rows:
            if process_only and "过程" not in str(row["type"]):
                continue
            if not process_only and blank(row["mark"]):
                continue
            required += 1
            name = str(row["name"])
            if not any(name.strip() == str(fact.get("value", "")).strip() for fact in directory["cells"].values() if fact.get("state") == "known"):
                raise UnknownFact("能力目录尚不能确认覆盖关联PGQP1特性：" + name)
            facts.require_text(name)
        if not required:
            raise UnknownFact("关联PGQP1没有可确认的适用特性，不能按空范围通过。")
        return "PASS", "已按当前批次同零件的PGQP1核实全部适用特性目录。"
