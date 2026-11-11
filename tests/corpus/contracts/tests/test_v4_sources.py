"""V4 shipped rule text is traceable to the original DOCX and 00-A matrix.

This checks provenance, not customer Checker correctness or runtime coverage.
"""

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
    route = (ROOT / "assets/uaes/prechecks/PPAP-00-A_客户与文件类型覆盖矩阵_路由表_v1.7.md").read_text()
    matrix = {}
    for line in route.splitlines():
        if re.match(r"\| [ULN]\d{2} \|", line):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if re.fullmatch(r"[ULN]\d{2}", cells[0]) and re.fullmatch(r"`[TE]\d{2}`", cells[2]):
                matrix[cells[0]] = (cells[2].strip("`"), cells[4].strip("*"))
    assert len(matrix) == 41
    presets = customer_presets()
    assert {rule.entry_id: (rule.source_type, rule.level) for rule in presets if rule.entry_id in matrix} == matrix
    # NIO entries outside the matrix carry the Li Auto document of the same type, clause for clause.
    by_entry = {rule.entry_id: rule for rule in presets}
    derived = [rule for rule in presets if rule.entry_id not in matrix]
    assert {rule.customer for rule in derived} == {"蔚来"} and len(derived) == 12
    for rule in derived:
        (source,) = [other for other in presets if other.customer == "理想" and other.source_path == rule.source_path]
        assert rule.source_type == source.source_type and [clause.text for clause in rule.clauses] == [clause.text for clause in by_entry[source.entry_id].clauses]
    for rule in presets:
        source = ROOT / rule.source_path
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
    from audit.presets import preset_rule_body
    from corpus_contracts.core_round import customer_suite, run_suite_round
    from openpyxl import load_workbook

    source = ROOT / "assets/nio/templates/11 特殊特性清单.xlsx"
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

        target = RoundInput("target", "source", "frozen-source", "特殊特性清单.xlsx", "ready", rule_version_id="nio-t12-source", rule_body=body)
        result = run_suite_round(RoundWork("source-bound-round", "corpus", [target]), {"frozen-source": content.getvalue()}, suites={"frozen-source": customer_suite("蔚来", "N04")})
        by_id = {finding["item_key"].split(":rule:", 1)[1]: finding["status"] for finding in result["findings"]}
        assert [by_id[clause.id] for clause in next(rule for rule in customer_presets() if rule.entry_id == "N04").clauses] == expected, [
            (row["status"], row["suggestions"]) for row in result["findings"]
        ]
        assert not result["coverage"]["unchecked"]


def test_li_auto_equipment_template_metadata_cannot_replace_a_missing_device_supplier():
    import io

    from audit.audits import RoundInput, RoundWork
    from audit.presets import preset_rule_body
    from corpus_contracts.core_round import customer_suite, run_suite_round
    from openpyxl import load_workbook

    source = ROOT / "assets/li/templates/APQP-设备开发验收清单.xlsx"
    for missing_supplier, expected in [(False, "PASS"), (True, "NG")]:
        book = load_workbook(source)
        sheet = book["设备开发验收清单"]
        if missing_supplier:
            sheet["D6"] = None
        content = io.BytesIO()
        book.save(content)
        book.close()
        body = preset_rule_body("理想", "L13")
        target = RoundInput("target", "file", "version", "设备清单.xlsx", "ready", rule_version_id="li-l13", rule_body=body)
        result = run_suite_round(RoundWork("round", "tenant", [target]), {"version": content.getvalue()}, suites={"version": customer_suite("理想", "L13")})
        assert result["findings"][0]["status"] == expected, result["findings"][0]["suggestions"]
