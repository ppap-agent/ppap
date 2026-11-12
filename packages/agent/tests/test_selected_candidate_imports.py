"""A selected Suite cannot produce a candidate that delegates to another version."""

import json

import pytest
from agent.execution import checker_guest
from audit.contracts import AuditInput, RuleInput

FACTORY = "def create_audit(audit_file, rule, group_context=None):\n    return None\n"
OLD_IMPORTS = (
    "from audit.checker_lib.uaes.control_plan import UaesControlPlanAgent\n",
    "from audit.standard_library import check_clause\n",
    "import importlib\nOLD = importlib.import_module('audit.checker_lib.uaes.control_plan')\n",
    "OLD = __import__('audit.checker_lib.uaes.control_plan')\n",
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
