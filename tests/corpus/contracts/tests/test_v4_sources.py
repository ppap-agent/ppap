"""V4 shipped rule text is traceable to the original DOCX and 00-A matrix.

This checks provenance, not customer Checker correctness or runtime coverage.
"""

from pathlib import Path

from audit.presets import customer_presets

ROOT = Path(__file__).resolve().parents[4]


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
