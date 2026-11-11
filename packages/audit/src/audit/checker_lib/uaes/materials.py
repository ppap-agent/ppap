"""UAES material list structure and independent drawing identity."""

from audit.checker_lib.nio.excel_agent import rule

from .native import UaesSourceAgent, UnknownFact, blank, key


class UaesMaterialsAgent(UaesSourceAgent):
    def precheck(self):
        from .precheck_l1 import precheck_materials

        return precheck_materials(self)

    @rule("材料清单除备注外逐字段非空")
    def rule_1(self):
        facts = self.native()
        table = facts.table(("材料", "分供方"))
        start, columns = facts.columns(table, {"number": ("序号", "编号", "NO")})
        for span in table.get("merges", []):
            if span[0] <= start <= span[2] and span[3] > span[1]:
                # Multi-tier choice groups are not one scalar required field.
                # Their native values need a scoped selection decoder.
                raise UnknownFact("材料清单含多层合并表头或选择组，尚不能把组内空格判为空字段。")
        for address, item in table["cells"].items():
            import re

            if int(re.sub(r"^[A-Z]+", "", address)) >= start or item.get("state") != "known":
                continue
            text = str(item.get("value") or "")
            if ":" in text or "：" in text:
                label = re.split(r"[:：]", text, maxsplit=1)[0].strip()
                if label:
                    facts.field((label,))
        headers = {}
        reader = facts.readers[table["id"]]
        for column in range(1, table["cols"] + 1):
            item = reader.cell(start, column)
            if item.get("state") != "known":
                raise UnknownFact("材料表头存在无法读取的内容。")
            if not blank(item.get("value")) and "备注" not in key(item["value"]):
                headers[column] = item["value"]
        if len(headers) < 2:
            raise UnknownFact("尚不能确定材料清单完整字段范围。")
        count = 0
        for row in range(start + 1, table["rows"] + 1):
            if blank(facts.cell(table, row, columns["number"])):
                continue
            count += 1
            for column, label in headers.items():
                if blank(facts.cell(table, row, column)):
                    return "NG", f"材料清单第{row}行的{label}内容为空。"
        if not count:
            raise UnknownFact("没有已编号的材料行，不能按空清单通过。")
        return "PASS", "已逐行读取材料清单除备注外的全部表头字段。"

    @rule("供应商及UAES两方工程师实际签字")
    def rule_2(self):
        return self.visual_result({"signatures": "供应商负责工程师、UAES NE工程师、UAES SQD/SQE工程师三处均存在实际签字，不计空栏或预印标签。"}, "三方实际签字")

    @rule("同项目同物料独立图纸的零件名称和编号")
    def rule_3(self):
        self.related(("图纸", "drawing"), require_name=True)
        return "PASS", "已核实当前批次独立图纸与清单项目、零件编号、名称和版本一致。"
