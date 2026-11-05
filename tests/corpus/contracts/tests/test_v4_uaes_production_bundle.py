"""Actual frozen UAES inputs; unknown visual facts are not passing goldens."""

import hashlib
import json
from pathlib import Path

import pytest
from audit.audits import RoundInput, RoundWork
from audit.checkers.v4.uaes.native import NativeFacts, UaesSourceAgent
from corpus_contracts.oracle import original
from product import run_round_from_bytes

RULES = Path(__file__).resolve().parents[4] / "assets/corpus/rules/v4-uaes-2026-11-04"


def frozen(name):
    rule_name = "控制计划.xlsx" if name.endswith(".pdf") else name
    return json.loads((RULES / (rule_name + ".json")).read_text())


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
    source_hashes = {
        "PFMEA.xls": "eb65cbeff2052f2329572b53a24ed045bc0aa4f0d74283cb5822a773ad45bd78",
        "控制计划.xlsx": "777dc22645a73dc11ce5ed5aa1b2883b32709469a4c3d9aea1df4f3ffadbf54f",
        "过程流程图.xls": "8534b20607334cd6274a6f280eebab4f701c1b7bb6f8087216f0addd90488b33",
        "控制计划-模板3 WY.pdf": "fc87fac089541368e46cd1c6b78a61b63c6bd9e4bacaebadcd3184e0831f2b42",
    }
    for name in names:
        assert hashlib.sha256(original(name).read_bytes()).hexdigest() == source_hashes[name]
    inputs, blobs = [], {}
    expected = {}
    for name in names:
        rule = frozen(name)
        body = rule["rule_body"]
        assert body["rule_profile"] == "v4" and body["customers"] == ["UAES"]
        version = "original:" + name
        expected[version] = {clause["id"] for clause in body["rules"]}
        blobs[version] = original(name).read_bytes()
        inputs.append(RoundInput("target", version, version, name, "ready", rule_version_id=rule["rule_version_id"], rule_body=body, rule_execution=rule["rule_execution"]))
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
