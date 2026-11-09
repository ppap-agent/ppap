"""Frozen v5 directory programs read existing originals without certifying an all-PASS Oracle."""

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
    suite = next(item for item in predefined_directory_suites() if item.source_path == f"v5/UAES/{entry}")
    rules = json.loads(suite.files["rules.json"])
    # Keep the original controlled capability boundary. These source smoke
    # cases do not buy model observations or replace independent human truth.
    monkeypatch.setattr(SourceAudit, "visual_pdf", lambda _owner, fields, **_options: dict.fromkeys(fields))
    monkeypatch.setattr(SourceAudit, "text_result", lambda *_args, **_kwargs: ("UNABLE", "本测试未提供文本语义事实。"))
    target = RoundInput("target", "file", "version", filename, "ready", rule_version_id="v5-" + entry, rule_body=rules)
    result = run_suite_round(RoundWork("v5-original-round", "tenant", [target]), {"version": path.read_bytes()}, suites={"version": suite})
    findings = result["findings"]
    assert findings
    assert {row["locator"]["rule_item_id"] for row in findings} == set(suite.entrypoints)
    assert all(item["coverage"] == "executed" for item in result["coverage"]["rule_items"]), result
    assert all(row["status"] != "ERR" for row in findings), {row["locator"]["rule_item_id"]: row["suggestions"] for row in findings if row["status"] == "ERR"}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
