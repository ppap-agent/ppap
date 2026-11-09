"""A selected Suite cannot produce a candidate that delegates to another version."""

import hashlib
import json

import pytest
from agent.core_program import run_audit_manifest, write_audit_manifest
from agent.execution import checker_guest
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest
from audit.contracts import AuditInput, RuleInput

FACTORY = "def create_audit(audit_file, rule, group_context=None):\n    return None\n"
OLD_IMPORTS = (
    "from audit.checkers.v4.uaes.control_plan import UaesControlPlanAgent\n",
    "from audit.standard_library import check_clause\n",
    "import importlib\nOLD = importlib.import_module('audit.checkers.v4.uaes.control_plan')\n",
    "OLD = __import__('audit.checkers.v4.uaes.control_plan')\n",
    "def load(name):\n    return __import__(name)\n",
    "from audit.engine import resolve_engine_agent_class\n",
    "import audit.standard_library as old\n",
    "from audit import engine as old\n",
)


@pytest.mark.parametrize("old_import", OLD_IMPORTS, ids=["old-class", "old-helper", "literal-import", "builtin-import", "dynamic-import", "engine-root", "library-alias", "engine-alias"])
@pytest.mark.parametrize("file_name", ["checker.py", "resources.py"])
def test_selected_candidate_rejects_old_business_imports_before_trial(tmp_path, monkeypatch, old_import, file_name):
    document = tmp_path / "original.txt"
    document.write_text("5\n")
    rule = RuleInput(rule_id="rule", type="自定义", rules=[{"id": "R1", "category": "数值", "rule_to_check": "数值至少10"}])
    files = {"checker.py": FACTORY, file_name: old_import + (FACTORY if file_name == "checker.py" else "VALUE = 5\n")}
    calls = []

    class Runner:
        input_tokens = output_tokens = 0

        def run(self, work, prompt, schema, deadline, **kwargs):
            calls.append(prompt)
            assert (work / "selected" / "checker.py").read_text() == FACTORY
            return schema(entrypoint="checker:create_audit", files=files)

    def forbidden_trial(*args, **kwargs):
        pytest.fail("A forbidden candidate must be rejected before executing its imports")

    monkeypatch.setattr(checker_guest, "MAX_TRIALS", 1)
    monkeypatch.setattr(checker_guest, "run_program", forbidden_trial)
    with pytest.raises(checker_guest.CheckerProgramError, match="checker_cross_version_import"):
        checker_guest.develop_checker(
            audit_file=AuditInput(audit_id="original", file_url=str(document)),
            rule=rule,
            group_context=None,
            digest="a" * 64,
            executor_revision="test-runtime",
            runner=Runner(),
            work=tmp_path / "development",
            deadline=10**12,
            source_files={"checker.py": FACTORY, "rules.json": json.dumps(rule.model_dump(mode="json"))},
        )
    assert len(calls) == 1


def test_core_deep_rejects_a_new_legacy_delegate_before_trial(tmp_path, monkeypatch):
    raw = b"5\n"
    program = {"checker.py": FACTORY.encode()}
    digest = checker_digest(program)
    rules = {"type": "自定义", "rules": [{"id": "R1", "category": "数值", "rule_to_check": "数值至少10"}]}
    target = FrozenTarget("original", "selected-revision", "UAES:U08", "a" * 64, rules, RuleBinding("UAES:U08", digest, {"R1": "checker:create_audit"}))
    snapshot = ExecutionSnapshot(
        "tenant",
        "user",
        (FrozenInput("original", hashlib.sha256(raw).hexdigest(), "target", "original.txt"),),
        (target,),
        "runtime",
        "library",
        {"provider": "responses-compatible", "model": "test-model"},
        round_id="round",
    )
    inputs, work, credentials = tmp_path / "inputs", tmp_path / "work", tmp_path / "credentials"
    work.mkdir()
    credentials.mkdir()
    (credentials / "model.json").write_text(
        json.dumps({"base_url": "http://127.0.0.1:1/v1", "token": "local-test-capability-never-sent-1234567890", "model": "test-model", "wall_seconds": 100, "max_turns": 2})
    )
    write_audit_manifest(inputs, snapshot, {"original": raw}, {digest: program}, analysis_mode="deep")

    class Runner:
        input_tokens = output_tokens = calls = 0
        wall_seconds = 100

        def run(self, work, prompt, schema, deadline, **kwargs):
            self.calls += 1
            return schema(entrypoint="checker:create_audit", files={"checker.py": "from audit.engine import resolve_engine_agent_class\n" + FACTORY})

    runner = Runner()
    monkeypatch.setattr(checker_guest, "MAX_TRIALS", 1)
    monkeypatch.setattr("agent.execution.guest_model.PiRunner", lambda grant: runner)
    with pytest.raises(checker_guest.CheckerProgramError, match="checker_cross_version_import"):
        run_audit_manifest(inputs, work, credentials)
    assert runner.calls == 1
