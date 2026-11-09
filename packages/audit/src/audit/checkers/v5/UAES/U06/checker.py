"""UAES U06 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from decimal import Decimal

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, header, key, number
from resources import RESOURCE


class UaesCapabilityAgent(SourceAudit):
    REPORTS = ("工艺能力认证", "过程能力认证", "process capability")

    def reports(self):
        tables = self.native().tables(self.REPORTS)
        if not tables:
            raise UnknownFact("未能确定工艺能力认证报告的完整范围，不能按空报告通过。")
        return tables

    def report_value(self, table, aliases):
        return self.native().field(aliases, title=(table["id"],))

    def rule_1(self):
        facts = self.native()
        for table in self.reports():
            count_text = self.report_value(table, ("读数次数", "读数个数", "样本数量", "sample size", "number of readings"))
            count = number(count_text)
            if count != count.to_integral_value() or count < 0:
                raise UnknownFact("读数次数不是可确认的非负整数。")
            if count < 125:
                # A low declared count is not sufficient: the source also
                # accepts >=125 actual measurements. Count only a complete,
                # explicitly headed measurement region, not unrelated cells.
                try:
                    rows = facts.rows({"measurement": ("实测结果", "测量值", "measurement", "reading")}, title=(table["id"],))
                    actual = sum(not blank(row["measurement"]) and number(row["measurement"]).is_finite() for row in rows)
                except UnknownFact:
                    raise
                except Exception as exc:
                    from audit.checker_lib.source import MissingFact

                    if isinstance(exc, MissingFact):
                        raise UnknownFact("读数次数不足125，实测区域数量尚不能完整核实。") from exc
                    raise
                if actual < 125:
                    return "NG", "报告读数次数与完整实测区域数量均小于125。"
            cpk = number(self.report_value(table, ("Cpk", "Process Capability (Cpk)", "Process Capability Cpk")))
            if cpk <= Decimal("1.33"):
                return "NG", "工艺能力认证报告Cpk必须严格大于1.33。"
        return "PASS", "所有已确定的工艺能力认证报告满足样本数量和Cpk要求。"

    def rule_2(self):
        for table in self.reports():
            if number(self.report_value(table, ("Ppk", "Performance index (Ppk)", "Performance index Ppk"))) <= Decimal("1.67"):
                return "NG", "工艺能力认证报告Ppk必须严格大于1.67。"
        return "PASS", "所有已确定的工艺能力认证报告Ppk严格大于1.67。"

    def rule_3(self):
        facts = self.native()
        aliases = {
            "cpk": ("CPK结果值", "CPK结果", "CPK"),
            "ppk": ("PPK结果值", "PPK结果", "PPK"),
            "cpk_target": ("CPK目标值", "CPK目标"),
            "ppk_target": ("PPK目标值", "PPK目标"),
            "reaction": ("反应计划", "备注", "反应计划或备注"),
        }
        table = facts.table(("能力值汇总", "能力汇总"))
        start, _ = facts.columns(table, aliases)
        # Optional subject columns extend membership without becoming new
        # required source fields. Results must not decide whether a row exists.
        for name, labels in {"characteristic": ("特性名称", "特性描述"), "sequence": ("序号", "编号", "特性编号")}.items():
            matches = [column for column in range(1, table["cols"] + 1) if header(facts.readers[table["id"]].cell(start, column).get("value"), labels)]
            if len(matches) > 1:
                raise UnknownFact("能力汇总业务主体列有多个候选，需确认原件范围。")
            if matches:
                aliases[name] = labels
        rows = facts.rows(aliases, title=("能力值汇总", "能力汇总"))
        checked = 0
        for row in rows:
            subjects = [row[name] for name in ("characteristic", "sequence") if name in row]
            boundary = subjects if any(not blank(value) for value in subjects) else [row["cpk"], row["ppk"]]
            if any(key(value).startswith(marker) for value in boundary for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                break
            checked += 1
            cpk, cpk_target, ppk, ppk_target = (number(row[name]) for name in ("cpk", "cpk_target", "ppk", "ppk_target"))
            if cpk < cpk_target or ppk < ppk_target:
                if blank(row["reaction"]):
                    return "NG", "能力汇总存在低于目标且没有反应计划或备注的行。"
        if not checked:
            raise UnknownFact("能力汇总没有可确认的业务行，不能按空范围通过。")
        return "PASS", "全部能力汇总行达到目标，或对应不足项已有反应计划/备注。"

    def rule_4(self):
        return self.pgqp_directory(process_only=False)

    def rule_5(self):
        return self.pgqp_directory(process_only=True)

    def pgqp_directory(self, *, process_only):
        self.native().require_text("目录")
        related = self.related(("采购件质量计划", "PGQP1"))
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
            directory = self.native().table(("目录",))
            if not any(name.strip() == str(fact.get("value", "")).strip() for fact in directory["cells"].values() if fact.get("state") == "known"):
                raise UnknownFact("能力目录尚不能确认覆盖关联PGQP1特性：" + name)
            self.native().require_text(name)
        if not required:
            raise UnknownFact("关联PGQP1没有可确认的适用特性，不能按空范围通过。")
        return "PASS", "已按当前批次同零件的PGQP1核实全部适用特性目录。"


def create_audit(audit_file, rule, group_context=None):
    return UaesCapabilityAgent(audit_file, rule, group_context, resource=RESOURCE)
