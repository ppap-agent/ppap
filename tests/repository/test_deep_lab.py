"""The local checker lab cannot hide duplicate results or skip replay validation."""

import json
from pathlib import Path

import pytest

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
    runner = deep_lab._CheckerRunner("high", backend="grok")
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


def test_real_rule_change_oracle_matches_original_sample():
    """Fail if source changes invalidate the independently chosen live expectations."""
    from audit.contracts import RuleInput
    from openpyxl import load_workbook

    cases = deep_lab.build_bundle_map(deep_lab.load_poc_cases())["Blanc Spoiler-有特殊特性清单"]
    target = next(case for case in cases if case.file_seq == "11")
    book = load_workbook(target.file_path, data_only=True)
    try:
        sheet = book["特殊特性清单"]
        records = [(row[0].row, row[3].value, row[11].value, row[14].value) for row in sheet if isinstance(row[0].value, int) and not isinstance(row[0].value, bool) and row[0].value > 0]
        assert records == [(4, "产品", "预防性防错", "NA"), (5, "产品", "年度试验", "每年1次")]
    finally:
        book.close()
    assert {case.file_name for case in cases} == {
        "19-BLANC尾-生产控制计划.pdf",
        "P0363244_扰流板总成_特殊特性清单.xlsx",
        "过程流程图-BLANC.xlsx",
        "BLANC扰流板总成PFMEA_V3.xlsx",
    }
    fixtures = Path(__file__).resolve().parents[1] / "fixtures/deep_lab/real_rule_changes"
    expected = {"baseline": "NG", "scope": "PASS", "exception": "PASS", "empty_unknown": "UNABLE", "empty_required": "NG", "missing_unknown": "UNABLE", "missing_required": "NG"}
    for name, status in expected.items():
        rule = RuleInput.model_validate_json((fixtures / f"{name}.json").read_bytes())
        assert rule.rule_id == "real-excel-rule-change"
        assert [item["id"] for item in rule.rules] == ["R1"]
        assert json.loads((fixtures / f"{name}-expected.json").read_text()) == {"R1": status}


@pytest.mark.parametrize("candidate", [False, True])
@pytest.mark.parametrize("status", [402, 429, 503])
def test_provider_http_failure_stops_before_candidate_confirmation(tmp_path, monkeypatch, candidate, status):
    from agent.client import DeepError
    from agent.execution.checker_guest import CheckerDraft

    source = "def create_audit(audit_file, rule, group_context=None): pass"

    def model(work, effort, **kwargs):
        if candidate:
            (work / "checker.py").write_text(source)
        terminal = {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "errors": [f'Internal error: {{"http_status": {status}, "message": "private provider detail"}}'],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
        (work / "stdout.jsonl").write_text(json.dumps(terminal))
        return 1

    monkeypatch.setattr(deep_lab, "run_grok", model)
    runner = deep_lab._CheckerRunner("high", backend="grok")
    with pytest.raises(DeepError, match=f"^deep_model_http_{status}$"):
        runner.run(tmp_path, "task", CheckerDraft, 10**12, reasoning_effort="high")
    assert runner.input_tokens == 4
    assert runner.output_tokens == 2
    if candidate:
        assert (tmp_path / "checker.py").read_text() == source


def test_cli_summary_keeps_provider_failure_instead_of_missing_result(tmp_path, monkeypatch):
    from types import SimpleNamespace

    case = SimpleNamespace(bundle_id="bundle", file_name="source.xlsx")
    work = tmp_path / "case"
    work.mkdir()
    monkeypatch.setattr(deep_lab.sys, "argv", ["deep_lab.py", "--recipe", "checker", "--effort", "high"])
    monkeypatch.setattr(deep_lab, "OUTPUT", tmp_path)
    monkeypatch.setattr(deep_lab, "load_dotenv", lambda *args: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Default Pi selection must not configure or invoke Grok")

    monkeypatch.setattr(deep_lab, "ensure_home", forbidden)
    monkeypatch.setattr(deep_lab, "run_grok", forbidden)
    monkeypatch.setattr(deep_lab, "_selected", lambda *args: [case])
    monkeypatch.setattr(deep_lab, "prepare", lambda *args, **kwargs: (work, {"R1": "NG"}))

    def failed_model(*args, **kwargs):
        assert kwargs["backend"] == "pi"
        (work / "checker-error.txt").write_text("DeepError: deep_model_http_402\ninternal detail")
        return 1

    monkeypatch.setattr(deep_lab, "run_checker", failed_model)
    with pytest.raises(SystemExit) as stopped:
        deep_lab.main()
    assert stopped.value.code == 1
    summary = json.loads((tmp_path / "checker/high/summary.json").read_text())
    assert summary["runner"] == "pi"
    assert summary["results"][0]["error"] == "DeepError: deep_model_http_402"
    assert summary["matched"] == summary["executed"] == 0


def test_checker_lab_defaults_to_pi_and_stops_on_provider_failure(tmp_path, monkeypatch):
    from agent.client import DeepError
    from agent.execution.checker_guest import CheckerDraft

    calls = []

    def model(work, effort, **kwargs):
        calls.append((effort, kwargs["max_turns"]))
        (work / "checker.py").write_text("def create_audit(audit_file, rule, group_context=None): pass")
        (work / "stdout.jsonl").write_text(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error",
                    "is_error": True,
                    "usage": {"input_tokens": 13, "output_tokens": 5},
                }
            )
        )
        return 1

    def forbidden(*args, **kwargs):
        raise AssertionError("Pi default must not invoke Grok")

    monkeypatch.setattr(deep_lab, "run_pi", model)
    monkeypatch.setattr(deep_lab, "run_grok", forbidden)
    runner = deep_lab._CheckerRunner("high", max_turns=3)
    with pytest.raises(DeepError, match="^deep_model_failed$"):
        runner.run(tmp_path, "task", CheckerDraft, 10**12, reasoning_effort="low")
    assert calls == [("low", 3)]
    assert (runner.input_tokens, runner.output_tokens) == (13, 5)


@pytest.mark.parametrize("backend", ["", "unknown"])
def test_invalid_checker_backend_does_not_fall_back_to_grok(backend):
    with pytest.raises(ValueError, match="Unsupported model runner"):
        deep_lab._CheckerRunner("low", backend=backend)
