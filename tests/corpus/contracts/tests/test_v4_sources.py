"""V4 shipped rule text is traceable to the original DOCX and 00-A matrix.

This checks provenance, not customer Checker correctness or runtime coverage.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from audit.presets import customer_presets

ROOT = Path(__file__).resolve().parents[4]
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def test_all_41_source_documents_and_route_entries_match_shipped_rule_inputs():
    route = (ROOT / "assets/PPAP PRD v3.7/预检规则/PPAP-00-A_客户与文件类型覆盖矩阵_路由表_v1.7.md").read_text()
    matrix = {}
    for line in route.splitlines():
        if re.match(r"\| [ULN]\d{2} \|", line):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if re.fullmatch(r"[ULN]\d{2}", cells[0]) and re.fullmatch(r"`[TE]\d{2}`", cells[2]):
                matrix[cells[0]] = (cells[2].strip("`"), cells[4].strip("*"))
    assert len(matrix) == 41
    presets = customer_presets()
    assert {rule.entry_id: (rule.source_type, rule.level) for rule in presets} == matrix
    for rule in presets:
        source = ROOT / rule.source_path
        assert hashlib.sha256(source.read_bytes()).hexdigest() == rule.source_sha256
        with ZipFile(source) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
        # Verify each stored locator against the source cell named by its header,
        # independently of the build-time extractor and its row numbering IDs.
        tables = document.findall(".//w:tbl", NS)
        for clause in rule.clauses:
            table_id, row_id = map(int, re.fullmatch(r"table:(\d+)/row:(\d+)", clause.locator).groups())
            rows = tables[table_id - 1].findall("w:tr", NS)
            headers = ["".join(text.text or "" for text in cell.findall(".//w:t", NS)).strip() for cell in rows[0].findall("w:tc", NS)]
            description = next(index for index, header in enumerate(headers) if header in {"规则说明", "审核规则"})
            cells = rows[row_id - 1].findall("w:tc", NS)
            text = "\n".join("".join(node.text or "" for node in paragraph.findall(".//w:t", NS)) for paragraph in cells[description].findall("w:p", NS)).strip()
            assert text == clause.text, (rule.entry_id, clause.source_number)
    subprocess.run([sys.executable, str(ROOT / "scripts/compile_v4_presets.py"), "--check"], cwd=ROOT, check=True)


def test_nio_t12_source_clauses_run_over_the_actual_template_and_controlled_changes():
    import io

    from audit.audits import RoundInput, RoundWork
    from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
    from audit.presets import preset_rule_body
    from openpyxl import load_workbook
    from product import run_round_from_bytes

    source = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板/蔚来/11 特殊特性清单.xlsx"
    # Independent expected results come from N04 source clauses: title prefix
    # is required; a row with part number/name requires every listed field.
    for missing, expected in ((None, ["PASS", "PASS"]), ("title", ["NG", "PASS"]), ("frequency", ["PASS", "NG"])):
        book = load_workbook(source)
        sheet = book.active
        sheet["A1"] = "特殊特性清单" if missing == "title" else "P12345 特殊特性清单"
        for column, value in {"A": 1, "B": "P12345", "C": "支架", "D": "产品", "E": "孔径", "F": "10±0.1", "I": "CC", "K": "CC", "L": "卡尺", "N": "定位防错", "O": "每件", "P": 1}.items():
            sheet[f"{column}4"] = None if column == "O" and missing == "frequency" else value
        content = io.BytesIO()
        book.save(content)
        book.close()
        body = preset_rule_body("蔚来", "N04")
        # Source clause order is deliberately different from Checker order.
        body["rules"].reverse()
        execution = freeze_target_rule_execution(body, "特殊特性清单.xlsx")
        target = RoundInput("target", "source", "frozen-source", "特殊特性清单.xlsx", "ready", rule_version_id="nio-t12-source", rule_body=body, rule_execution=execution)
        result = run_round_from_bytes(RoundWork("source-bound-round", "corpus", [target]), {"frozen-source": content.getvalue()})
        by_id = {finding["item_key"].split(":rule:", 1)[1]: finding["status"] for finding in result["findings"]}
        assert [by_id[clause.id] for clause in next(rule for rule in customer_presets() if rule.entry_id == "N04").clauses] == expected
        assert not result["coverage"]["unchecked"]


def test_nio_pfmea_controlled_template_keeps_source_fields_and_scoring_sheets_separate():
    import io

    from audit.audits import RoundInput, RoundWork
    from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
    from audit.presets import preset_rule_body
    from openpyxl import load_workbook
    from openpyxl.cell.cell import MergedCell
    from product import run_round_from_bytes

    source = ROOT / "assets/PPAP PRD v3.7/客户归类模板与规则_备份/模板/蔚来/9 潜在过程失效模式及后果分析.xlsx"
    book = load_workbook(source)
    sheet = book["第五版PFMEA表格"]
    for row in sheet.iter_rows(min_row=10):
        for cell in row:
            if not isinstance(cell, MergedCell):
                cell.value = None
    for address, value in {
        "F3": "供应商",
        "F4": "项目",
        "F5": "支架",
        "F6": "P12345",
        "M3": "上海",
        "M4": "2026-01-01",
        "M5": "2026-02-01",
        "M6": "质量/制造",
        "E10": "10 成型",
        "G10": "内部：压装\n客户工厂：装车\n最终用户：乘坐",
        "J10": "内部：返工（4）\n客户工厂：返工（6）\n最终用户：异响（5）",
        "K10": 6,
        "P10": "双感官检查（6）\n目视检查（8）",
        "Q10": 6,
        "S10": "CC",
    }.items():
        sheet[address] = value
    book["变更履历"]["F4"] = "2026-01-01"
    book["变更履历"]["F5"] = "2026-02-01"
    content = io.BytesIO()
    book.save(content)
    book.close()
    body = preset_rule_body("蔚来", "N02")
    target = RoundInput("target", "file", "version", "PFMEA.xlsx", "ready", rule_version_id="nio-n02", rule_body=body, rule_execution=freeze_target_rule_execution(body, "PFMEA.xlsx"))
    result = run_round_from_bytes(RoundWork("round", "tenant", [target]), {"version": content.getvalue()})
    # No PFD is provided: the five independent clauses must still execute.
    assert [row["status"] for row in result["findings"]] == ["PASS"] * 5 + ["UNABLE"] * 2, [(row["status"], row["suggestions"]) for row in result["findings"]]
