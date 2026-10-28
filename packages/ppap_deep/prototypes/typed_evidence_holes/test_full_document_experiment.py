"""A repair must not replay an unknown or already repaired model operation."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def previous_attempt(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    runner = importlib.import_module("full_document_experiment")
    source_and_binding = importlib.import_module("test_bound_document").source_and_binding
    document, rules = tmp_path / "document.pdf", tmp_path / "rules.docx"
    document.write_bytes(b"original document")
    rules.write_bytes(b"original rules")
    case = tmp_path / "prior" / "case"
    case.mkdir(parents=True)
    _, binding = source_and_binding()
    summary = {
        "case_id": "case",
        "returncode": 0,
        "run_completed": False,
        "source_unchanged": True,
        "document_sha256": runner.sha(document),
        "rules_sha256": runner.sha(rules),
        "usage": {"uncertain": False, "in_flight": False},
        "terminal": {"is_error": False, "structured_output": binding.model_dump()},
        "validation": {"errors": ["invalid_source_binding"], "detail": "body/header overlap"},
    }
    return runner, tmp_path / "prior", document, rules, case / "summary.json", summary


def test_one_explicit_repair_keeps_prior_candidate_and_rejection(previous_attempt):
    runner, prior, document, rules, path, summary = previous_attempt
    path.write_text(json.dumps(summary))
    reference, prompt = runner.repair_context(prior, "case", document, rules)
    assert reference["sha256"] == runner.sha(path)
    assert "body/header overlap" in prompt
    assert "header_rows=[]" in prompt
    assert "body/header overlap" in path.read_text()


@pytest.mark.parametrize("mode", ["unknown_usage", "in_flight", "completed", "already_repaired", "core_error", "changed_source", "different_rules"])
def test_repair_rejects_unknown_completed_or_different_original_operations(previous_attempt, mode):
    runner, prior, document, rules, path, summary = previous_attempt
    if mode == "unknown_usage":
        summary["usage"]["uncertain"] = True
    elif mode == "in_flight":
        summary["usage"]["in_flight"] = True
    elif mode == "completed":
        summary["run_completed"] = True
    elif mode == "already_repaired":
        summary["repair_of"] = {"attempt": 1}
    elif mode == "core_error":
        summary["terminal"]["is_error"] = True
    elif mode == "changed_source":
        document.write_bytes(b"different original")
    elif mode == "different_rules":
        rules.write_bytes(b"different rules")
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="settled first-attempt"):
        runner.repair_context(prior, "case", document, rules)
