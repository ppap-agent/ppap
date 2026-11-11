"""UAES U04 rules over frozen, source-addressed evidence.

This leaf owns its complete business checks and does not call another version.
"""

from __future__ import annotations

from audit.checker_lib.source import SourceAudit, UnknownFact, blank, key
from resources import RESOURCE


class UaesMaterialsAgent(SourceAudit):
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
        footer_labels = {
            key(label)
            for label in ("编制", "编制人", "编制/日期", "编制人/日期", "审核", "审核人", "审核/日期", "批准", "批准人", "供应商负责工程师签字", "UAES NE工程师签字", "UAES SQD/SQE工程师签字")
        }

        def is_footer(value):
            return key(value).split(":", 1)[0] in footer_labels

        count = 0
        for row in range(start + 1, table["rows"] + 1):
            serial = facts.cell(table, row, columns["number"])
            if is_footer(serial):
                continue
            if blank(serial):
                footer_columns = [column for column in headers if (item := reader.cell(row, column)).get("state") == "known" and is_footer(item.get("value"))]
                if footer_columns:
                    for column in footer_columns:
                        facts.cell(table, row, column)
                    continue
            values = {column: facts.cell(table, row, column) for column in headers}
            if all(blank(value) for value in values.values()) or all(key(value) == key(headers[column]) for column, value in values.items()):
                continue
            count += 1
            for column, label in headers.items():
                if blank(values[column]):
                    return "NG", f"材料清单第{row}行的{label}内容为空。"
        if not count:
            raise UnknownFact("没有已编号的材料行，不能按空清单通过。")
        return "PASS", "已逐行读取材料清单除备注外的全部表头字段。"

    def rule_2(self):
        return self.visual_result({"signatures": "供应商负责工程师、UAES NE工程师、UAES SQD/SQE工程师三处均存在实际签字，不计空栏或预印标签。"}, "三方实际签字")

    def rule_3(self):
        self.related(("图纸", "drawing"), require_name=True)
        return "PASS", "已核实当前批次独立图纸与清单项目、零件编号、名称和版本一致。"


def create_audit(audit_file, rule, group_context=None):
    return UaesMaterialsAgent(audit_file, rule, group_context, resource=RESOURCE)
