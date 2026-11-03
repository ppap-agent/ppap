"""The local checker lab cannot hide duplicate results or skip replay validation."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("deep_lab", Path(__file__).resolve().parents[2] / "scripts/deep_lab.py")
deep_lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deep_lab)


def test_duplicate_clause_cannot_be_scored_as_a_match(tmp_path):
    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "checks": [
                    {"id": "1", "status": "NG"},
                    {"id": "1", "status": "PASS"},
                ]
            }
        )
    )
    assert deep_lab.score(tmp_path, {"1": "PASS"})["match"] is False


def test_missing_extra_and_empty_checks_are_not_success(tmp_path):
    for checks, expected in (([], {"1": "PASS"}), ([{"id": "2", "status": "PASS"}], {"1": "PASS"}), ([], {})):
        (tmp_path / "result.json").write_text(json.dumps({"checks": checks}))
        assert deep_lab.score(tmp_path, expected)["match"] is False


def test_a_new_source_is_explicitly_unscored(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"checks": [{"id": "1", "status": "PASS"}]}))
    report = deep_lab.score(tmp_path, None)
    assert report["match"] is None
    assert report["scored"] is False


def test_model_files_and_partial_receipt_use_the_product_reader(tmp_path, monkeypatch):
    from agent.execution.checker_guest import CheckerDraft

    source = "def create_audit(audit_file, rule, group_context=None): pass"

    def model(work, effort, **kwargs):
        assert kwargs["max_turns"] == 128
        (work / "checker.py").write_text(source)
        (work / "stdout.jsonl").write_text(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                    "result": json.dumps({"completed": True, "artifact": "checker.py", "clauses": ["A"]}),
                }
            )
        )
        return 0

    monkeypatch.setattr(deep_lab, "run_grok", model)
    runner = deep_lab._CheckerRunner("high")
    draft = runner.run(tmp_path, "task", CheckerDraft, 10**12, reasoning_effort="high")
    assert draft.files == {"checker.py": source}
    assert draft.clauses == ["A"]
    assert runner.input_tokens == 4
    assert runner.output_tokens == 2


def test_layout_variant_moves_table_and_merges_without_changing_source_values(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from openpyxl import Workbook, load_workbook

    path = tmp_path / "source.xlsx"
    book = Workbook()
    book.active["A1"] = "title"
    book.active["A2"] = "header"
    book.active.merge_cells("A2:B2")
    book.active["A3"] = "value"
    book.save(path)
    book.close()
    original = path.read_bytes()
    case = SimpleNamespace(
        file_name=path.name,
        file_path=path,
        file_seq="11",
        bundle_id="bundle",
        expected={1: SimpleNamespace(status="PASS")},
    )
    monkeypatch.setattr(deep_lab, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(deep_lab, "load_poc_cases", lambda: [case])
    monkeypatch.setattr(deep_lab, "build_bundle_map", lambda cases: {"bundle": cases})
    monkeypatch.setattr(deep_lab, "build_group_context_for_bundle", lambda cases: SimpleNamespace(by_doc_type={"target": [], "reference": []}))
    work, expected = deep_lab.prepare("checker", "bundle", path.name, "high", variant="shifted-table")
    assert expected == {"1": "PASS"}
    altered = load_workbook(work / "files" / path.name)
    try:
        assert altered.active["A1"].value == "title"
        assert altered.active["A22"].value == "header"
        assert altered.active["A23"].value == "value"
        assert "A22:B22" in altered.active.merged_cells
    finally:
        altered.close()
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "checks,matched",
    [([{"id": "R2", "status": "NG"}, {"id": "R1", "status": "PASS"}], True), ([{"id": "R1", "status": "PASS"}], False), ([{"id": "R1", "status": "PASS"}, {"id": "R1", "status": "PASS"}], False)],
)
def test_rule_change_scoring_requires_exact_clause_coverage(tmp_path, checks, matched):
    (tmp_path / "result.json").write_text(json.dumps({"checks": checks}))
    report = deep_lab.score(tmp_path, {"R1": "PASS", "R2": "NG"})
    assert report["match"] is matched


def test_changed_rules_have_no_implicit_corpus_oracle(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"checks": [{"id": "R1", "status": "PASS"}]}))
    report = deep_lab.score(tmp_path, None)
    assert report["match"] is None
    assert report["scored"] is False


def test_rule_override_is_used_after_relocation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from audit.contracts import AuditInput, GroupContextInput, RuleInput

    original = RuleInput(rule_id="stable", rules=[{"id": "old"}])
    changed = original.model_copy(update={"rules": [{"id": "new", "rule_to_check": "全部偏差不超过0.1"}]})
    context = GroupContextInput(task_group_id="test", by_audit_id={"target": AuditInput(audit_id="target", file_url="old.xlsx")})
    monkeypatch.setattr(deep_lab, "load_poc_cases", lambda: [])
    monkeypatch.setattr(deep_lab, "build_bundle_map", lambda _: {"bundle": [SimpleNamespace(file_name="target")]})
    monkeypatch.setattr(deep_lab, "build_group_context_for_bundle", lambda _: context)
    monkeypatch.setattr(deep_lab, "_rule_input", lambda _: original)
    digests = []
    for name in ["initial", "relocated"]:
        work = tmp_path / name
        work.mkdir()
        (work / "fixed.xlsx").write_bytes(b"fixed file bytes")
        (work / "rules.json").write_text(json.dumps({"target": "target", "rule": changed.model_dump()}))
        actual = deep_lab._checker_context("bundle", work, {"target": "fixed.xlsx"})
        assert actual.rules_by_id["target"] == changed
        digests.append(actual.by_audit_id["target"].file_metadata["sha256"])
    assert digests[0] == digests[1]
    assert original.rules == [{"id": "old"}]
