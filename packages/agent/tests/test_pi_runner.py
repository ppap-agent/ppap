"""Pi must prove terminal success and keep file receipts; usage is observed, never a budget."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agent.execution.checker_guest import CheckerDraft
from agent.execution.guest_model import ModelProtocolError, PiRunner

GRANT = {"base_url": "http://127.0.0.1:12345/v1", "token": "x" * 48, "model": "test-model", "wall_seconds": 10}
SOURCE = "def create_audit(audit_file, rule, group_context=None): return None\n"


def terminal(**changes):
    return {
        "type": "result",
        "subtype": "success",
        "stop_reason": "end_turn",
        "is_error": False,
        "result": '{"completed":true,"artifact":"checker.py"}',
        "num_turns": 2,
        "usage": {"input_tokens": 13, "output_tokens": 5},
        "tool_calls": 2,
        "completed_tool_calls": 1,
        "compactions": 1,
        **changes,
    }


@pytest.fixture
def guest():
    with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off", "UNRELATED_SECRET": "private", "PYTHONPATH": "/trusted/packages"}, clear=True):
        with patch("agent.execution.guest_model.os.getuid", return_value=10001):
            yield


def launch(events, *, returncode=0, write_checker=False):
    def start(command, **kwargs):
        if write_checker:
            (kwargs["cwd"] / "checker.py").write_text(SOURCE)
        for event in events:
            kwargs["stdout"].write((json.dumps(event) + "\n").encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=returncode, poll=lambda: returncode, wait=lambda: returncode)

    return start


def test_pi_uses_scoped_grant_and_accounts_for_completed_work(tmp_path, guest):
    runner = PiRunner(GRANT)
    with patch("agent.execution.guest_model.subprocess.Popen", side_effect=launch([terminal()], write_checker=True)) as process:
        draft = runner.run(tmp_path, "develop", CheckerDraft, 10**12, reasoning_effort="medium")
    assert draft.submitted and draft.files == {"checker.py": SOURCE}
    assert (runner.input_tokens, runner.output_tokens, runner.usage_known) == (13, 5, True)
    assert (runner.tool_calls, runner.completed_tool_calls, runner.compactions) == (2, 1, 1)
    command = process.call_args.args[0]
    config = json.loads((tmp_path / "pi-home/request.json").read_text())
    assert command == ["/usr/local/bin/ppap-pi", str(tmp_path / "pi-home/request.json")]
    assert GRANT["token"] not in command
    assert config["token"] == GRANT["token"]
    assert not {"max_turns", "max_tokens"} & set(config)
    assert (tmp_path / "pi-home/request.json").stat().st_mode & 0o777 == 0o600
    env = process.call_args.kwargs["env"]
    assert "UNRELATED_SECRET" not in env and env["PYTHONPATH"] == "/trusted/packages"
    assert env["PI_OFFLINE"] == "1" and env["PI_TELEMETRY"] == "0"
    # Pi inspects originals with the checker's own Python; the image has no system python3.
    assert env["PATH"].split(":")[0] == str(Path(sys.executable).parent)
    assert process.call_args.kwargs["start_new_session"] is False


@pytest.mark.parametrize(
    "events,returncode",
    [
        ([], 0),
        ([terminal(), terminal()], 0),
        ([terminal()], 1),
        ([terminal(is_error=True)], 0),
        ([terminal(stop_reason="length")], 0),
    ],
)
def test_pi_rejects_missing_failed_or_invalid_completion(tmp_path, guest, events, returncode):
    with patch("agent.execution.guest_model.subprocess.Popen", side_effect=launch(events, returncode=returncode)):
        with pytest.raises(ModelProtocolError):
            PiRunner(GRANT)._run(tmp_path, "develop", 10**12, reasoning_effort="low")


def test_failed_pi_can_recover_candidate_but_cannot_submit_it(tmp_path, guest):
    with patch("agent.execution.guest_model.subprocess.Popen", side_effect=launch([terminal(is_error=True)], write_checker=True)):
        draft = PiRunner(GRANT).run(tmp_path, "develop", CheckerDraft, 10**12, reasoning_effort="low")
    assert draft.files == {"checker.py": SOURCE} and draft.submitted is False


@pytest.mark.parametrize("usage", [None, {"input_tokens": True, "output_tokens": 2}, {"input_tokens": 3}])
def test_missing_or_invalid_usage_does_not_reject_a_completed_stage(tmp_path, guest, usage):
    runner = PiRunner(GRANT)
    with patch("agent.execution.guest_model.subprocess.Popen", side_effect=launch([terminal(usage=usage, tool_calls=-1)], write_checker=True)):
        draft = runner.run(tmp_path, "develop", CheckerDraft, 10**12, reasoning_effort="low")
    assert draft.submitted and draft.files == {"checker.py": SOURCE}
    assert (runner.input_tokens, runner.output_tokens, runner.usage_known) == (0, 0, False)


def test_each_run_gets_the_shipped_agents_file_and_skills_read_only(tmp_path):
    from agent.execution.guest_model import DEEP_AGENT, install_deep_agent

    home = tmp_path / "pi-home"
    home.mkdir()
    install_deep_agent(home)
    assert (home / "AGENTS.md").read_bytes() == (DEEP_AGENT / "AGENTS.md").read_bytes()
    shipped = sorted(path.relative_to(DEEP_AGENT / "skills") for path in (DEEP_AGENT / "skills").rglob("*.md"))
    assert sorted(path.relative_to(home / "skills") for path in (home / "skills").rglob("*.md")) == shipped
    assert {"ppap-semantic-audit/SKILL.md", "ppap-semantic-audit/references/cross-file.md"} <= {str(path) for path in shipped}
    assert all(not path.stat().st_mode & 0o222 for path in [home / "AGENTS.md", *(home / "skills").rglob("*.md")])
