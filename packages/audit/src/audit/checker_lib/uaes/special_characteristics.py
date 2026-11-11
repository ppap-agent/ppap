"""U12 native facts; physical merged rows and uncalculated formulas stay visible."""

from audit.checker_lib.nio.excel_agent import rule

from .native import MissingFact, UaesSourceAgent, UnknownFact, blank, header, key

_ALIASES = {
    "编号": ("编号", "序号"),
    "特性名称": ("产品特性", "过程特性", "特殊特性名称"),
    "特性值": ("特性及公差", "特性值"),
    "特性类别": ("特性类型", "特性类别"),
}


class UaesSpecialCharacteristicsAgent(UaesSourceAgent):
    PRECHECK_METHODS = {"U12:1": "rule_1", "U12:2": "rule_2"}

    def identity(self, name):
        facts = self.native()
        try:
            return facts.field((name,))
        except UnknownFact:
            # Only a readable, truly empty immediate value is a proven empty
            # field. A missing/visual/ambiguous value remains unknown.
            for table in facts.tables():
                for row in range(1, min(10, table["rows"]) + 1):
                    for column in range(1, min(24, table["cols"])):
                        item = facts.readers[table["id"]].cell(row, column)
                        if item.get("state") == "known" and header(item.get("value"), (name,)):
                            merge = next((span for span in table.get("merges", []) if span[0] <= row <= span[2] and span[1] <= column <= span[3]), None)
                            next_column = merge[3] + 1 if merge else column + 1
                            if next_column <= table["cols"] and blank(facts.cell(table, row, next_column)):
                                raise MissingFact(name + "对应内容为空。") from None
            raise

    @rule("物料名称与编号必填")
    def rule_1(self):
        for name in ("物料名称", "物料编号"):
            self.identity(name)
        return "PASS", "已从原件核实物料名称与编号实际内容。"

    @rule("特殊特性列与全部物理行必填")
    def rule_2(self):
        # Every populated characteristic row belongs to the scope. A readable
        # empty number is a missing required field, not an absent business row.
        rows = self.native().rows(_ALIASES, title=("特殊特性", "特性清单"))
        count = 0
        for row in rows:
            number = row["编号"]
            if any(key(number).startswith(marker) for marker in ("编制/日期", "审核/日期", "编制人", "批准人")):
                break
            if blank(number):
                return "NG", "特性业务行编号为空。"
            count += 1
            for name in ("特性名称", "特性值", "特性类别"):
                if blank(row[name]):
                    return "NG", f"已编号特性行{name}为空。"
        if not count:
            raise UnknownFact("没有可核实特性业务行，不能按空范围通过。")
        return "PASS", "已读取全部物理业务行的编号、特性名称、值和类别。"
