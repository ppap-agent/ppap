"""Actual frozen UAES inputs; unknown visual facts are not passing goldens."""

import hashlib

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checkers.v4.uaes.native import NativeFacts, UaesSourceAgent
from audit.engine.checkers.rule_declarations import freeze_target_rule_execution
from audit.presets import preset_rule_body
from corpus_contracts.oracle import original
from product import run_round_from_bytes

ENTRIES = {"PFMEA.xls": "U07", "控制计划.xlsx": "U08", "控制计划-模板3 WY.pdf": "U08", "过程流程图.xls": "U16"}


@pytest.mark.parametrize(
    "names",
    [
        ("控制计划.xlsx", "PFMEA.xls", "过程流程图.xls"),
        ("控制计划.xlsx", "控制计划-模板3 WY.pdf"),
    ],
    ids=["CP-PFMEA-PFD", "two-independent-CP-originals"],
)
def test_actual_uaes_bundle_keeps_each_file_and_its_frozen_clauses(names, monkeypatch):
    # This is native/preparation coverage. No ambient credentials or model
    # output can turn these original files into an all-PASS oracle.
    monkeypatch.setattr(UaesSourceAgent, "visual_pdf", lambda self, fields: dict.fromkeys(fields))
    inputs, blobs = [], {}
    expected = {}
    for name in names:
        body = preset_rule_body("UAES", ENTRIES[name])
        assert body["rule_profile"] == "v4" and body["customers"] == ["UAES"]
        version = "original:" + name
        expected[version] = {clause["id"] for clause in body["rules"]}
        blobs[version] = original(name).read_bytes()
        inputs.append(RoundInput("target", version, version, name, "ready", rule_version_id=ENTRIES[name], rule_body=body, rule_execution=freeze_target_rule_execution(body, name)))
    result = run_round_from_bytes(RoundWork("uaes-production-bundle", "tenant", inputs), blobs)
    findings = result["findings"]
    assert len(findings) == sum(map(len, expected.values()))
    assert not result["coverage"]["unchecked"]
    assert all(row["status"] != "ERR" for row in findings), [(row["item_key"], row["suggestions"]) for row in findings if row["status"] == "ERR"]
    for version, clauses in expected.items():
        rows = [row for row in findings if row["locator"]["file_version_id"] == version]
        assert {row["locator"]["rule_item_id"] for row in rows} == clauses
        # Neither combination supplies PGQP1, a drawing or a special-characteristic
        # list. These requirements cannot pass by borrowing another target file.
        missing_peer_clauses = {"U07:3", "U07:4", "U08:8", "U08:9", "U08:10", "U08:11", "U08:12", "U08:13"}
        assert all(row["status"] != "PASS" for row in rows if ":".join(row["locator"]["rule_item_id"].split(":")[:2]) in missing_peer_clauses)


def test_actual_native_pdf_reaches_one_xlsx_table_with_four_original_page_sources():
    path = original("控制计划-模板3 WY.pdf")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    facts = NativeFacts(str(path), "frozen-real-pdf", lambda *args: None)
    table = facts.table(("控制计划", "control plan"))
    assert facts.data["prepared_format"] == "xlsx"
    assert len(facts.data["tables"]) == 1
    assert (table["rows"], table["cols"]) == (135, 16)
    assert facts.data["source"]["sha256"] == before
    assert {cell["source"]["page"] for cell in table["cells"].values() if cell.get("source", {}).get("page")} == {1, 2, 3, 4}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
