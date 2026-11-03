"""The local checker lab cannot hide duplicate results or skip replay validation."""

import json

from scripts import deep_lab


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
