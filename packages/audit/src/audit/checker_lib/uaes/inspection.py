"""UAES four-report scope and all supplier measurements from native columns."""

import math
import re

from audit.checker_lib.nio.excel_agent import rule
from audit.engine.checkers.dimension_measurement_policy import parse_quality_standard_range

from .native import UaesSourceAgent, UnknownFact, blank, key, number


class UaesInspectionAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_inspection

        return precheck_inspection(self)

    REPORTS = ("尺寸报告", "材料报告", "功能报告", "可靠性报告")

    def report(self, title):
        facts = self.native()
        table = facts.table((title,))
        row, columns = facts.columns(
            table,
            {
                "requirement": ("要求", "要求值", "技术要求", "Requirements"),
                "actual": ("供应商实测结果", "供应商实测值", "Supplier Actual Measurement Results", "Actual-Value Supplier/供应商实测值/Actual-Value Customer 客户实测值", "实测值"),
            },
        )
        actual = columns["actual"]
        reader = facts.readers[table["id"]]
        merged = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= actual <= span[3]), None)
        end = merged[3] if merged else actual
        # Some reports label each sample individually instead of a merged band.
        if merged is None:
            while end + 1 <= table["cols"] and re.fullmatch(r"(?:实测值|样品|sample)\s*\d+", str(reader.cell(row, end + 1).get("value", "")), re.I):
                end += 1
        facts.cell(table, row, actual)
        return table, row, columns["requirement"], list(range(actual, end + 1))

    @rule("尺寸报告供应商实测列为3的正整数倍")
    def rule_1(self):
        _table, _row, _requirement, measurements = self.report("尺寸报告")
        count = len(measurements)
        # The rule document's remark hands more than five columns to a reviewer.
        if count > 5:
            return "UNABLE", f"尺寸报告供应商实测值列数为{count}，超过5列，需人工判断是否覆盖多产线/多模穴。"
        if count % 3:
            return "NG", f"尺寸报告供应商实测值列数为{count}，不是3或3的倍数。"
        return "PASS", f"尺寸报告供应商实测值列数为{count}。"

    @rule("四报告全部要求实测范围与同行OK")
    def rule_2(self):
        facts = self.native()
        near = False
        for title in self.REPORTS:
            table, start, requirement, measurements = self.report(title)
            # Only a true overall result column can establish OK. Separate
            # blank OK/NOT OK columns require the actual mark to be decoded.
            _result_row, result_columns = facts.columns(table, {"result": ("结果", "判定", "评价", "Evaluation")})
            result_column = result_columns["result"]
            reader = facts.readers[table["id"]]
            separate_marks = key(reader.cell(_result_row + 1, result_column).get("value")) == "ok" and key(reader.cell(_result_row + 1, result_column + 1).get("value")).rstrip(".") in {
                "notok",
                "nok",
                "ng",
            }
            count = 0
            for row in range(start + 1, table["rows"] + 1):
                value = facts.cell(table, row, requirement)
                if blank(value):
                    continue
                if key(value) in {"要求", "requirements"}:
                    continue
                if any(term in str(value) for term in ("供应商负责人", "Supplier responsible", "编制", "批准")):
                    break
                count += 1
                result = facts.cell(table, row, result_column)
                if separate_marks:
                    rejected = facts.cell(table, row, result_column + 1)

                    def mark(value):
                        return bool(re.fullmatch(r"[×xX☑✓√✔]", str(value or "").strip()))

                    if mark(rejected):
                        return "NG", f"{title}第{row}行实际选中了NOT OK。"
                    if mark(result) and blank(rejected):
                        result = "OK"
                    elif blank(result) and blank(rejected):
                        raise UnknownFact("报告OK/NOT OK两栏没有可确认的实际选择。")
                    else:
                        raise UnknownFact("报告结果选择符号无法可靠识别或多选。")
                if key(result) != "ok":
                    if blank(result):
                        raise UnknownFact("报告结果栏未能确认实际OK选择，不能以空OK标签通过。")
                    return "NG", f"{title}第{row}行结果不是OK。"
                limits = parse_quality_standard_range(value)
                if limits is None:
                    if re.search(r"\d", str(value)):
                        raise UnknownFact("定量要求范围不能可靠解析：" + str(value))
                    continue
                lower, upper = limits
                actual = [float(number(facts.cell(table, row, column))) for column in measurements]
                if any(item < lower or item > upper for item in actual):
                    return "NG", f"{title}第{row}行存在超出要求范围的实测值。"
                if math.isfinite(lower) and math.isfinite(upper) and upper > lower and all(item >= upper - (upper - lower) * 0.1 for item in actual):
                    near = True
            if not count:
                raise UnknownFact(title + "没有可确认的要求行，不能按空范围通过。")
        return "PASS", "四份报告全部要求的实测值与同行OK已核实。" + ("所有实测值接近要求上限的项目，请关注过程能力分析报告。" if near else "")

    @rule("四报告适用要求与供应商负责人实际签字")
    def rule_3(self):
        for title in self.REPORTS:
            self.report(title)
        return self.visual_result(
            {"four_signatures": "尺寸、材料、功能、可靠性四份报告中，每份存在非空要求项的报告下方供应商负责人区域均有对应实际签字，不能是预印标签。"}, "四报告供应商负责人实际签字"
        )

    @rule("同物料号及名称的独立气泡图")
    def rule_4(self):
        self.related(("气泡图", "bubble"), require_name=True)
        return "PASS", "当前批次独立气泡图与报告物料号、名称及版本一致。"
