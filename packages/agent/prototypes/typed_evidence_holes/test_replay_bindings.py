"""Replay preserves source identity and does not relabel failed model runs as completed."""

import importlib
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook


@pytest.fixture
def recorded_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    replay = importlib.import_module("replay_bindings")
    snapshot, binding = importlib.import_module("test_bound_document").source_and_binding()
    case = tmp_path / "original"
    (case / "inputs").mkdir(parents=True)
    (case / "work").mkdir()
    book = Workbook()
    sheet = book.active
    sheet.title = "Report"
    for address, cell in snapshot["tables"][0]["cells"].items():
        sheet[address] = cell["value"]
    document = case / "inputs/document.xlsx"
    book.save(document)
    rules = case / "inputs/rules.docx"
    shutil.copy2(Path(__file__).resolve().parents[4] / "assets/V2.0测试/规则V2.0/01 全功能检验报告.docx", rules)
    observed = replay.inspect_original(document, case / "work/document_read")
    artifact = replay.evaluate(binding, observed, rules_sha256=replay.RULES_SHA256)
    (case / "work/execution.json").write_text(json.dumps(artifact))
    summary = {
        "run_completed": True,
        "returncode": 0,
        "source_unchanged": True,
        "terminal": {"is_error": False},
        "usage": {"uncertain": False, "in_flight": False},
        "validation": {"errors": []},
        "document_sha256": replay.sha(document),
        "rules_sha256": replay.sha(rules),
    }
    (case / "summary.json").write_text(json.dumps(summary))
    return replay, case


def test_replay_rereads_originals_and_preserves_original_candidate(recorded_case, tmp_path):
    replay, case = recorded_case
    original_hash = replay.sha(case / "work/execution.json")
    result = replay.replay(case, tmp_path / "derived")
    assert replay.sha(case / "work/execution.json") == original_hash
    assert result["replay"]["source_execution_sha256"] == original_hash
    assert result["replay"]["additional_model_requests"] == 0
    assert result["execution_kind"] == "deterministic_replay"
    assert result["training_eligible"] is False
    with pytest.raises(FileExistsError):
        replay.replay(case, tmp_path / "derived")


@pytest.mark.parametrize("mode", ["failed", "unknown_usage", "original_changed", "snapshot_changed"])
def test_replay_rejects_failed_runs_and_changed_source_observations(recorded_case, tmp_path, mode):
    replay, case = recorded_case
    if mode in {"failed", "unknown_usage"}:
        summary = replay.read(case / "summary.json")
        if mode == "failed":
            summary["run_completed"] = False
        else:
            summary["usage"]["uncertain"] = True
        (case / "summary.json").write_text(json.dumps(summary))
    elif mode == "original_changed":
        (case / "inputs/document.xlsx").write_bytes(b"different original")
    else:
        path = case / "work/document_read/snapshot.json"
        source = replay.read(path)
        source["tables"][0]["cells"]["A1"]["value"] = "wrong title"
        path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="completed|fingerprint|observations differ"):
        replay.replay(case, tmp_path / "derived")
