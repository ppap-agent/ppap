"""Host persistence validates generated factories without revalidating old programs."""

import json
from types import SimpleNamespace

import pytest
from agent.checker_spec import CheckerArtifact, SuiteCheckerProgram
from audit.checker_core import RuleBinding, checker_digest
from product.store.checker_development import _candidate_files, _standalone_candidate_files

FACTORY = b"def create_audit(audit_file, rule, group_context=None):\n    return None\n"


def base(files=None):
    rules = {"rules": [{"id": "R1", "rule_to_check": "Required"}], "checker_binding": {"R1": "checker:create_audit"}}
    return SimpleNamespace(revision=SimpleNamespace(suite_key="UAES:U08"), files={"checker.py": FACTORY, "rules.json": json.dumps(rules).encode(), **(files or {})})


@pytest.mark.parametrize("name", ["checker.py", "resources.py"])
@pytest.mark.parametrize("candidate_files", [_standalone_candidate_files, _candidate_files], ids=["optimization", "round"])
@pytest.mark.parametrize(
    "old_import",
    [
        b"from audit.checkers.v4.uaes.control_plan import UaesControlPlanAgent\n",
        b"from audit.standard_library import check_clause\n",
        b"import importlib\nOLD = importlib.import_module('audit.checkers.v4.uaes.control_plan')\n",
    ],
    ids=["old-class", "old-helper", "literal-import"],
)
def test_generated_factory_cannot_bypass_guest_import_guard(candidate_files, name, old_import):
    files = {"checker.py": FACTORY, name: old_import + (FACTORY if name == "checker.py" else b"VALUE = 5\n")}
    artifact = CheckerArtifact(input_digest="a" * 64, executor_revision="runtime", entrypoint="checker:create_audit", files=files)
    with pytest.raises(ValueError, match="checker_cross_version_import"):
        candidate_files(base(), artifact)


def test_generated_factory_may_use_shared_checker_library():
    files = {"checker.py": b"from audit.checker_lib.source import SourceAudit\n" + FACTORY}
    artifact = CheckerArtifact(input_digest="a" * 64, executor_revision="runtime", entrypoint="checker:create_audit", files=files)
    candidate = _standalone_candidate_files(base(), artifact)
    assert candidate["checker.py"] == files["checker.py"]


def test_frozen_native_program_keeps_its_original_imports_and_binding():
    files = {"checker.py": b"from audit.standard_library import check_clause\ndef check(context):\n    return check_clause(context, {})\n"}
    rules = {"rules": [{"id": "R1", "rule_to_check": "Required"}], "checker_binding": {"R1": "checker:check"}}
    selected = base({**files, "rules.json": json.dumps(rules).encode()})
    program = SuiteCheckerProgram(input_digest="a" * 64, executor_revision="runtime", files=files, binding=RuleBinding("UAES:U08", checker_digest(files), rules["checker_binding"]))
    candidate = _standalone_candidate_files(selected, program)
    assert candidate == selected.files
