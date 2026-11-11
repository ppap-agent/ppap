"""Frozen directory programs read existing originals without certifying an all-PASS Oracle."""

import hashlib
import json

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checker_lib.source import SourceAudit
from audit.standard_suites import predefined_directory_suites
from corpus_contracts.core_round import run_suite_round
from corpus_contracts.oracle import original


@pytest.mark.parametrize(("entry", "filename"), [("U08", "控制计划.xlsx"), ("U07", "PFMEA.xls"), ("U16", "过程流程图.xls"), ("U08", "控制计划-模板3 WY.pdf")])
def test_real_uaes_originals_execute_all_bound_clauses_without_technical_error(monkeypatch, entry, filename):
    path = original(filename)
    assert path.is_file(), filename
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    suite = next(item for item in predefined_directory_suites() if item.source_path == f"UAES/{entry}")
    rules = json.loads(suite.files["rules.json"])
    # Keep the original controlled capability boundary. These source smoke
    # cases do not buy model observations or replace independent human truth.
    monkeypatch.setattr(SourceAudit, "visual_pdf", lambda _owner, fields, **_options: dict.fromkeys(fields))
    monkeypatch.setattr(SourceAudit, "text_result", lambda *_args, **_kwargs: ("UNABLE", "本测试未提供文本语义事实。"))
    target = RoundInput("target", "file", "version", filename, "ready", rule_version_id="leaf-" + entry, rule_body=rules)
    result = run_suite_round(RoundWork("leaf-original-round", "tenant", [target]), {"version": path.read_bytes()}, suites={"version": suite})
    findings = result["findings"]
    assert findings
    assert {row["locator"]["rule_item_id"] for row in findings} == set(suite.entrypoints)
    assert all(item["coverage"] == "executed" for item in result["coverage"]["rule_items"]), result
    assert all(row["status"] != "ERR" for row in findings), {row["locator"]["rule_item_id"]: row["suggestions"] for row in findings if row["status"] == "ERR"}
    by_clause = {row["locator"]["rule_item_id"]: row for row in findings}
    # This round freezes only the named target. Missing independent inputs are
    # observable absences, not a claim about the contents of a missing file.
    missing = {
        "U08": {"U08:7": "图纸", "U08:8": "过程流程图", "U08:9": "过程流程图", "U08:10": "采购件质量计划", "U08:11": "采购件质量计划", "U08:12": "图纸", "U08:13": "图纸"},
        "U07": {"U07:3": "特殊特性", "U07:4": "采购件质量计划"},
    }
    for clause, source_name in missing.get(entry, {}).items():
        assert by_clause[clause]["status"] == "NG", by_clause[clause]
        assert "缺少" in by_clause[clause]["suggestions"] and source_name in by_clause[clause]["suggestions"]
    if entry == "U08":
        assert by_clause["U08:4"]["status"] == "PASS", by_clause["U08:4"]
        assert by_clause["U08:4"]["locator"]["semantic"]["sources"]
        assert by_clause["U08:2"]["status"] == "UNABLE"  # No semantic fact was supplied.
    elif entry == "U07":
        import xlrd

        sheet = xlrd.open_workbook(file_contents=path.read_bytes()).sheet_by_name("F01Z")
        # E7 and P7 are distinct S columns from different score groups. This
        # reader does not establish group ownership and must not choose one.
        assert sheet.cell_value(6, 4) == sheet.cell_value(6, 15) == "S"
        assert by_clause["U07:2"]["status"] == "UNABLE"
        assert "重复" in by_clause["U07:2"]["suggestions"]
        assert by_clause["U07:1"]["status"] == "UNABLE"
    else:
        # Real graphical truth was deliberately not bought by this native run.
        assert all(row["status"] == "UNABLE" for row in findings)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
