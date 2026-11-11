"""U08:1 executes the v5 leaf and Core over located, frozen native cells."""

import hashlib
import json
from pathlib import Path
from runpy import run_path

import pytest
from openpyxl import load_workbook

_helpers = run_path(str(Path(__file__).with_name("test_v5_uaes_rules.py")))
_run, _xlsx = _helpers["_run"], _helpers["_xlsx"]


@pytest.mark.parametrize(
    ("resource", "expected", "located_cell"),
    [("压机", "PASS", "B3"), (None, "NG", "A3"), ("=1+1", "UNABLE", "B3")],
    ids=["resource-present", "resource-empty", "resource-formula-uncached"],
)
def test_u08_resource_clause_has_native_positive_negative_and_unknown_evidence(tmp_path, monkeypatch, resource, expected, located_cell):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Native U08:1 must not call a live text or OCR provider")

    monkeypatch.setattr("documents.ai.run_json", forbidden)
    monkeypatch.setattr("documents.ai.AsyncOpenAI", forbidden)
    sheets = {"控制计划": [["控制计划"], ["工序名称/操作描述", "机器/装置/夹具/工装"], ["装配", resource]]}
    raw = _xlsx(sheets)
    digest = hashlib.sha256(raw).hexdigest()
    source = tmp_path / "frozen-input" / "U08.xlsx"
    source.parent.mkdir()
    source.write_bytes(raw)
    frozen_inputs = []
    validate = _run.__globals__["validate_results"]

    def checked(results, *, target, inputs):
        frozen_inputs.extend(inputs)
        return validate(results, target=target, inputs=inputs)

    monkeypatch.setitem(_run.__globals__, "validate_results", checked)
    result, requests = _run(tmp_path, monkeypatch, "U08", "U08:1", original=source)
    assert result["clause_id"] == "U08:1"
    assert result["status"] == expected, result
    assert not requests
    assert result["evidence"]["source_format"] == "xlsx"
    assert result["evidence"]["used"] == "structure"
    path = tmp_path / "U08.xlsx"
    assert path.read_bytes() == source.read_bytes() == raw
    assert len(frozen_inputs) == 1
    assert frozen_inputs[0].sha256 == digest
    assert frozen_inputs[0].file_version_id == "original"
    sources = result["evidence"]["sources"]
    assert sources
    locators = [json.loads(source["locator"]) for source in sources]
    assert any(locator.get("cell") == located_cell for locator in locators)
    assert all(locator["kind"] == "xlsx_cell" and locator["sheet"] == "控制计划" and locator["file_version_id"] == frozen_inputs[0].file_version_id for locator in locators)
    original = load_workbook(path, data_only=False)
    cached = load_workbook(path, data_only=True)
    try:
        assert original["控制计划"]["A3"].value == "装配"
        assert original["控制计划"]["B3"].value == resource
        assert all(original["控制计划"][locator["cell"]].coordinate == locator["cell"] for locator in locators)
        if expected == "UNABLE":
            assert cached["控制计划"]["B3"].value is None
            assert any(item["code"] == "formula_cache_missing" for item in result["evidence"]["diagnostics"])
    finally:
        original.close()
        cached.close()
