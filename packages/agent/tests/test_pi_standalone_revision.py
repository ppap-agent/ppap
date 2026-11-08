"""Pi edits the selected checker bytes and does not look up another version."""

import hashlib
import json
from pathlib import Path

import pytest
from agent.execution.checker_guest import CheckerProgramError, develop_checker
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.contracts import AuditInput, RuleInput
from audit.standard_suites import predefined_directory_suites


def _rule() -> RuleInput:
    return RuleInput(rule_id="selected", type="自定义", rule_profile="v4", rules=[{"id": "R1", "category": "阈值", "rule_to_check": "A1"}])


def _target(path: Path) -> AuditInput:
    return AuditInput(audit_id="file", file_url=str(path), file_metadata={"file_version_id": "file", "original_filename": path.name})


class _Stop(Exception):
    pass


class _Runner:
    input_tokens = output_tokens = 0

    def __init__(self, checks):
        self.checks = checks

    def run(self, work, prompt, schema, deadline, *, reasoning_effort):
        self.checks(work, prompt)
        raise _Stop()


def _develop(tmp_path, monkeypatch, source, *, prior=True):
    called = []

    def forbidden(*args, **kwargs):
        called.append(args)
        raise AssertionError("task_reference")

    monkeypatch.setattr("agent.execution.checker_guest.task_reference", forbidden)
    path = tmp_path / "sample.xlsx"
    path.write_bytes(b"frozen-bytes")
    prior_text = "def create_audit(audit_file, rule, group_context=None):\n    return []\n"

    def checks(work, prompt):
        assert "预置来源只用于选择参考实现" not in prompt
        assert "不要改用其他版本" in prompt
        assert not (work / "reference-checker.py").exists()
        assert not (work / "template-checker.py").exists()
        for name, body in source.items():
            assert (work / "selected" / name).read_text(encoding="utf-8") == body
        if "resources.py" not in source:
            assert not (work / "selected" / "resources.py").exists()

    with pytest.raises(_Stop):
        develop_checker(
            audit_file=_target(path),
            rule=_rule(),
            group_context=None,
            digest="a" * 64,
            executor_revision="current",
            runner=_Runner(checks),
            work=tmp_path / "work",
            deadline=10**12,
            prior_files={"checker.py": prior_text} if prior else None,
            source_files=source,
        )
    assert called == []


def test_selected_revision_is_the_working_copy_when_resources_exist(tmp_path, monkeypatch):
    _develop(
        tmp_path,
        monkeypatch,
        {"checker.py": "def create_audit(audit_file, rule, group_context=None):\n    return []\n", "rules.json": '{"rules":[]}', "resources.py": "VALUE = 1\n"},
    )


def test_selected_revision_without_resources_does_not_invent_one(tmp_path, monkeypatch):
    _develop(tmp_path, monkeypatch, {"checker.py": "def create_audit(audit_file, rule, group_context=None):\n    return []\n", "rules.json": '{"rules":[]}'})


def test_unsupported_source_name_fails_before_a_model_call(tmp_path):
    calls = []

    class Runner:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, *, reasoning_effort):
            calls.append(work)
            raise AssertionError("model")

    with pytest.raises(CheckerProgramError, match="源文件不完整"):
        develop_checker(
            audit_file=_target(tmp_path / "sample.xlsx"),
            rule=_rule(),
            group_context=None,
            digest="b" * 64,
            executor_revision="current",
            runner=Runner(),
            work=tmp_path / "work",
            deadline=10**12,
            prior_files={"checker.py": "def create_audit(audit_file, rule, group_context=None):\n    return []\n"},
            source_files={"checker.py": "def create_audit(audit_file, rule, group_context=None):\n    return []\n", "notes.txt": "x"},
        )
    assert calls == []


def test_v5_u08_bytes_do_not_pass_unbound_clauses_or_unsupported_formats(tmp_path):
    from agent.execution.audit_runtime import run_audit_process
    from openpyxl import Workbook

    suite = next(item for item in predefined_directory_suites() if item.source_path == "v5/UAES/U08")
    rules = json.loads(suite.files["rules.json"])
    program = {name: suite.files[name] for name in ("checker.py", "resources.py")}
    digest = checker_digest(program)
    binding = RuleBinding(suite.suite_key, digest, rules["checker_binding"])
    book = Workbook()
    sheet = book.active
    sheet.title = "控制计划"
    path = tmp_path / "control-plan.xlsx"
    book.save(path)
    book.close()
    original = path.read_bytes()
    pdf = tmp_path / "control-plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def run(raw: bytes, filename: str):
        source = FrozenInput("target", hashlib.sha256(raw).hexdigest(), "target", filename)
        target = FrozenTarget("target", "revision", suite.suite_key, "c" * 64, rules, binding)
        snapshot = ExecutionSnapshot("tenant", "initiator", (source,), (target,), "runtime", "library", {})
        return run_audit_process(snapshot, {"target": raw}, {digest: program})

    excel = run(original, "control-plan.xlsx")
    coverage = {row["clause_id"]: row["coverage"] for row in excel["coverage"]}
    unbound = {clause for clause, state in coverage.items() if state == "unsupported"}
    assert unbound == {row["id"] for row in rules["rules"]} - set(rules["checker_binding"])
    assert unbound
    assert not any(row["clause_id"] in unbound and row["status"] == "PASS" for row in excel["findings"])
    unsupported = run(pdf.read_bytes(), "control-plan.pdf")
    assert unsupported["findings"]
    assert {row["status"] for row in unsupported["findings"]} == {"UNABLE"}
    assert not any(row["coverage"] == "unsupported" and row["clause_id"] in {item["clause_id"] for item in unsupported["findings"]} for row in unsupported["coverage"])
