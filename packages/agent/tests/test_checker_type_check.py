from pathlib import Path

import pytest
from agent.execution.checker_guest import CheckerTrialError, type_check

ROOT = Path(__file__).resolve().parents[3]
PYRIGHT = ROOT / "deploy" / "deep" / "node_modules" / ".bin" / "pyright"
pytestmark = pytest.mark.skipif(not PYRIGHT.is_file(), reason="pyright ships with the deep image; npm ci --prefix deploy/deep")


def test_every_verified_checker_leaf_passes_the_type_check(tmp_path):
    leaves = sorted({path.parent for path in (ROOT / "packages/audit/src/audit/checkers").glob("**/checker.py")})
    assert leaves
    for index, leaf in enumerate(leaves):
        files = {path.name: path.read_text(encoding="utf-8") for path in leaf.glob("*.py")}
        type_check(files, tmp_path / str(index), pyright=PYRIGHT)


def test_a_type_error_goes_back_to_the_next_trial_with_its_location(tmp_path):
    checker = "def create_audit(audit_file, rule, group_context=None):\n    return undefined_helper(audit_file)\n"
    with pytest.raises(CheckerTrialError) as raised:
        type_check({"checker.py": checker}, tmp_path, pyright=PYRIGHT)
    assert "checker.py:2" in str(raised.value) and "reportUndefinedVariable" in str(raised.value)
