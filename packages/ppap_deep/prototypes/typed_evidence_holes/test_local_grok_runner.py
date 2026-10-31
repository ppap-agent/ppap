"""Local model execution preserves paid-operation and source boundaries."""

import hashlib
import http.client
import importlib
import io
import json
import os
import shutil
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field, ValidationError


class Answer(BaseModel):
    value: int = Field(strict=True)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    module = importlib.import_module("local_grok_runner")
    monkeypatch.setattr(module.subprocess, "check_output", lambda *args, **kwargs: "grok test-build\n")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    original = b'{"rule":"frozen original"}'
    (inputs / "rule-0.json").write_bytes(original)
    (inputs / "manifest.json").write_text(json.dumps({"rules": {"r": "rule-0.json"}, "documents": {}}))
    work = tmp_path / "work" / "compile-0"
    work.mkdir(parents=True)
    (work / "clauses.json").write_text('{"rule":"read original"}')
    (work / "rule-source.json").write_text(json.dumps({"source": {"sha256": hashlib.sha256(original).hexdigest()}}))
    settings = SimpleNamespace(llm_base_url="https://example.invalid/v1", llm_api_key="private-test-provider-credential")

    def make(**kwargs):
        return module.LocalGrokRunner(settings, tmp_path / "runner", "/usr/bin/true", input_root=inputs, **kwargs)

    return module, make, work, inputs, tmp_path


def fresh_work(work, name):
    destination = work.parent / name
    shutil.copytree(work, destination, ignore=shutil.ignore_patterns("execution.json", "artifact_schema.json"))
    return destination


def artifact_result(runner, value, *, notes=None):
    scratch = runner.scratch / f"stage-{runner.stage_number}"
    scratch.mkdir(exist_ok=True)
    (scratch / "execution.json").write_text(json.dumps(value))
    if notes is not None:
        (scratch / "working-state.json").write_text(json.dumps(notes))
    return {"result": '{"completed":true,"artifact":"execution.json"}'}


def settle(runner, prompt, *, unknown=False):
    body = {"model": "auxiliary-core-alias", "reasoning": {"effort": "low"}, "input": prompt}
    lease = runner.grants.authorize(runner.token, body, len(prompt.encode()) + 128)
    if unknown:
        runner.grants.finish_unknown(lease)
    else:
        runner.grants.record_usage(lease, {"input_tokens": 13, "output_tokens": 7})
    assert body["model"] == "grok-4.7"
    assert body["reasoning"] == {"effort": "medium"}


def test_compile_and_audit_share_budget_and_force_medium(setup, monkeypatch):
    module, make, work, _, root = setup
    runner = make(max_requests=2)

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt)
        return artifact_result(runner, {"value": 42}, notes={"synthetic_checks": [{"case": "known blank", "expected": "NG", "actual": "NG"}]})

    monkeypatch.setattr(runner, "_invoke", invoke)
    audit = fresh_work(work, "audit")
    (audit / "rule-source.json").unlink()
    with runner:
        first = runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
        second = runner.run(audit, "audit", Answer, time.monotonic() + 30, reasoning_effort="low")
        assert first == second == Answer(value=42)
        with pytest.raises(module.ModelBudgetError):
            runner.run(work, "cannot start", Answer, time.monotonic() + 30, reasoning_effort="low")
    assert runner.usage == {"completed_requests": 2, "input_tokens": 26, "output_tokens": 14, "uncertain": False, "in_flight": False, "budget_exhausted": False, "requests_started": 2}
    requests = [json.loads(line) for line in (root / "runner/model-inputs.jsonl").read_text().splitlines()]
    assert [item["stage"] for item in requests] == [1, 2]
    assert all(item["body"]["reasoning"] == {"effort": "medium"} for item in requests)
    assert runner.summary["context_completed"] is True
    assert all(item["reasoning_effort"] == "medium" for item in runner.summary["stages"])
    notes = json.loads((root / "runner/stage-002-Answer/working-state.json").read_text())
    assert notes == {"trust": "model_notes_unverified", "training_eligible": False, "notes": {"synthetic_checks": [{"case": "known blank", "expected": "NG", "actual": "NG"}]}}
    assert not any(path.name.startswith("home-") for path in (root / "runner").rglob("*"))


def test_settled_schema_rejection_remains_available_for_guest_single_repair(setup, monkeypatch):
    _, make, work, _, root = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt)
        return artifact_result(runner, {"value": "wrong" if runner.stage_number == 1 else 42})

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner:
        with pytest.raises(ValidationError):
            runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
        assert runner.run(fresh_work(work, "repair"), "explicit schema repair", Answer, time.monotonic() + 30, reasoning_effort="xhigh").value == 42
    rejected = json.loads((root / "runner/stage-001-Answer/summary.json").read_text())
    assert rejected["completed"] is False
    assert rejected["failure_type"] == "ValidationError"
    assert runner.usage["completed_requests"] == 2


def test_unknown_consumption_blocks_every_further_stage_without_retry(setup, monkeypatch):
    module, make, work, _, _ = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt, unknown=True)
        return artifact_result(runner, {"value": 42})

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner:
        with pytest.raises(module.ModelProtocolError, match="not settled"):
            runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
        with pytest.raises(module.ModelProtocolError, match="unknown consumption"):
            runner.run(work, "repair", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
    assert runner.stage_number == runner.requests_started == 1
    assert runner.usage["uncertain"] is True
    assert runner.summary["context_completed"] is False


def test_changed_stage_input_is_rejected_after_settled_response(setup, monkeypatch):
    module, make, work, _, _ = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt)
        (work / "clauses.json").write_text('{"rule":"changed"}')
        return artifact_result(runner, {"value": 42})

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner, pytest.raises(module.ModelProtocolError, match="source changed"):
        runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
    assert runner.usage["completed_requests"] == 1


def test_deadline_and_symlink_fail_before_model_submission(setup):
    _, make, work, _, root = setup
    runner = make()
    with runner:
        with pytest.raises(TimeoutError):
            runner.run(work, "compile", Answer, time.monotonic() - 1, reasoning_effort="xhigh")
        secret = root / "outside.json"
        secret.write_text('{"unrelated":"private"}')
        (work / "clauses.json").unlink()
        (work / "clauses.json").symlink_to(secret)
        with pytest.raises(OSError):
            runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
    assert runner.requests_started == 0


def test_saved_events_remove_credentials_and_private_reasoning(setup):
    _, make, _, _, root = setup
    runner = make()
    with runner:
        runner.record(
            runner.output / "events.jsonl",
            {
                "type": "message",
                "text": runner.settings.llm_api_key + " " + runner.token,
                "content": [{"type": "thinking_delta", "thinking": "private chain"}, {"type": "response.reasoning_text.delta", "delta": "private explanation"}],
                "reasoning": {"effort": "medium"},
                "encrypted_content": "private encrypted reasoning",
            },
        )
    saved = (root / "runner/events.jsonl").read_text()
    assert "private" not in saved
    assert runner.token not in saved
    assert '"effort": "medium"' in saved
    assert "reasoning_omitted" in saved


@pytest.mark.parametrize("bad_json", ['{"type":"result","is_error":true,"is_error":false}', '{"type":"result","is_error":false,"value":NaN}'])
def test_invalid_core_json_cannot_be_followed_by_a_valid_result(setup, monkeypatch, bad_json):
    module, make, work, _, _ = setup
    runner = make()

    class Process:
        pid = 123456
        returncode = 0
        stdout = io.StringIO(bad_json + '\n{"type":"result","is_error":false,"result":"{\\"completed\\":true,\\"artifact\\":\\"execution.json\\"}"}\n')
        stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            return self.returncode

    with runner:
        monkeypatch.setattr(runner, "_seatbelt", lambda *args: runner.output / "unused.sb")
        monkeypatch.setattr(module.os, "killpg", lambda *args: None)
        monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Process())
        with pytest.raises(module.ModelProtocolError, match="Invalid core event JSON"):
            runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
    assert runner.summary["stages"][0]["completed"] is False


def test_result_without_a_model_request_is_not_a_real_run(setup, monkeypatch):
    module, make, work, _, _ = setup
    runner = make()
    monkeypatch.setattr(runner, "_invoke", lambda *args: artifact_result(runner, {"value": 42}))
    with runner, pytest.raises(module.ModelProtocolError, match="without an authorized"):
        runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")


@pytest.mark.parametrize("mode", ["complete", "incomplete_stop", "errors", "nonzero_exit", "duplicate_result", "stdout_limit", "stderr_limit"])
def test_core_completion_and_log_limits_preserve_matched_tool_counts(setup, monkeypatch, mode):
    module, make, work, _, _ = setup
    runner = make()
    stopped, waited = [], []
    terminal = {"type": "result", "is_error": False, "stop_reason": "end_turn", "result": '{"completed":true,"artifact":"execution.json"}'}
    if mode == "incomplete_stop":
        terminal["stop_reason"] = "max_tokens"
    if mode == "errors":
        terminal["errors"] = ["incomplete tool operation"]
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a"}, {"type": "tool_use", "id": "b"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a"}, {"type": "tool_result", "tool_use_id": "a"}, {"type": "tool_result", "tool_use_id": "unrelated"}]}},
        terminal,
    ]
    if mode == "duplicate_result":
        events.append(terminal)
    if mode == "stdout_limit":
        events.insert(0, {"type": "system", "text": "x" * 2000})

    class Process:
        pid = 123456
        returncode = 1 if mode == "nonzero_exit" else 0

        def __init__(self):
            self.stdout = io.StringIO("\n".join(json.dumps(item) for item in events) + "\n")
            self.stderr = io.StringIO("x" * 2000 if mode == "stderr_limit" else "")

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            waited.append(self.pid)
            return self.returncode

    with runner:
        monkeypatch.setattr(runner, "_seatbelt", lambda *args: runner.output / "unused.sb")
        monkeypatch.setattr(module.os, "killpg", lambda pid, sig: stopped.append((pid, sig)))
        monkeypatch.setattr(module, "CORE_LOG_LIMITS", {"stdout": 1024, "stderr": 1024})

        def popen(*args, **kwargs):
            settle(runner, "one model request")
            (Path(kwargs["cwd"]) / "execution.json").write_text('{"value":42}')
            return Process()

        monkeypatch.setattr(module.subprocess, "Popen", popen)
        if mode == "complete":
            assert runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh").value == 42
            assert runner.tool_calls == 2
            assert runner.completed_tool_calls == 1
        else:
            with pytest.raises(module.ModelProtocolError):
                runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="xhigh")
        assert runner.usage["completed_requests"] == 1
        assert stopped and all(call == (123456, module.signal.SIGKILL) for call in stopped)
        assert waited and all(pid == 123456 for pid in waited)


@pytest.mark.skipif(sys.platform != "darwin", reason="The local experiment uses macOS Seatbelt")
@pytest.mark.parametrize("phase", ["compile", "audit"])
def test_native_boundary_exposes_only_stage_originals_and_denies_hidden_inputs(setup, phase):
    module, make, work, inputs, root = setup
    supplier = inputs / "supplier-sensitive.json"
    other_rule = inputs / "other-rule-sensitive.json"
    extra = inputs / "unmapped-secret.json"
    cached = inputs / "cached-plan-private.json"
    for path in (supplier, other_rule, extra, cached):
        path.write_text(json.dumps({"content": path.stem}))
    manifest = {
        "rules": {"r": "rule-0.json", "other": other_rule.name},
        "documents": {"supplier": supplier.name},
        "cached_plans": {"r": cached.name},
        "cached_snapshots": {"supplier": extra.name},
    }
    (inputs / "manifest.json").write_text(json.dumps(manifest))
    oracle = root / "report.json"
    oracle.write_text('{"expected":"PASS"}')
    history = root / "history.json"
    history.write_text('{"previous_status":"PASS"}')
    if phase == "audit":
        (work / "rule-source.json").unlink()
    runner = make()
    with runner:
        agent_work, home, stage = runner.scratch / "agent", runner.scratch / "home", runner.output / "probe"
        for path in (agent_work, home, stage):
            path.mkdir()
        originals = runner.scratch / f"sources-{runner.stage_number}"
        runner._stage_sources(work, originals)
        (agent_work / "sources").symlink_to(originals, target_is_directory=True)
        prompt, _ = runner._stage_prompt(work, "Use only the supplied stage files.", stage)
        policy = runner._seatbelt(work, agent_work, home, stage)
        probes = json.loads((stage / "boundary-probe.json").read_text())
        assert probes["denied_returncode"] != 0
        assert probes["original_returncode"] == 0
        assert str(inputs) not in prompt
        assert "(subpath " + json.dumps(str(inputs)) + ")" not in policy.read_text()
        names = {path.name for path in originals.iterdir()}
        if phase == "compile":
            assert names == {"rule-0.json"}
            # Hidden paths do not occur in the prompt or stage data. Knowing
            # such a path still cannot grant the compiler read access.
            readable = prompt + (stage / "inputs.json").read_text() + "".join(path.name + path.read_text() for path in originals.iterdir())
            assert all(path.name not in readable for path in (supplier, other_rule, extra, cached, oracle, history))
        else:
            assert names == {"rule-0.json", supplier.name, other_rule.name, "manifest.json"}
            visible = json.loads((originals / "manifest.json").read_text())
            assert visible["rules"] == manifest["rules"] and visible["documents"] == manifest["documents"]
            assert not visible.get("cached_plans") and not visible.get("cached_snapshots")
            assert cached.name not in json.dumps(visible) and extra.name not in json.dumps(visible)
        for path in originals.iterdir():
            outcome = module.subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy), "/bin/cat", str(agent_work / "sources" / path.name)], capture_output=True)
            assert outcome.returncode == 0 and outcome.stdout == path.read_bytes()
            forbidden_write = module.subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy), "/bin/sh", "-c", 'printf changed > "$1"', "probe", str(path)], capture_output=True)
            assert forbidden_write.returncode != 0
        for path in (*inputs.iterdir(), oracle, history):
            outcome = module.subprocess.run(
                ["/usr/bin/sandbox-exec", "-f", str(policy), sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).read_bytes()", str(path)],
                env=runner._environment(agent_work, home),
                cwd=agent_work,
                capture_output=True,
            )
            assert outcome.returncode != 0
            assert b"PermissionError" in outcome.stderr, (path, outcome.stderr)


@pytest.mark.skipif(sys.platform != "darwin", reason="The local experiment uses macOS Seatbelt")
@pytest.mark.parametrize("format", [".xlsx", ".pdf"])
def test_native_python_runs_frozen_checker_without_working_repo_access(setup, format):
    import fitz
    from openpyxl import Workbook

    module, make, work, inputs, root = setup
    runner = make()
    (work / "rule-source.json").unlink()
    original = inputs / ("source" + format)
    if format == ".xlsx":
        book = Workbook()
        book.active.title = "Records"
        book.active.append(["Frequency"])
        book.active.append(["daily"])
        book.save(original)
        book.close()
    else:
        with fitz.open() as document:
            page = document.new_page(width=280, height=120)
            for x in (20, 140, 260):
                page.draw_line((x, 20), (x, 100))
            for y in (20, 60, 100):
                page.draw_line((20, y), (260, y))
            page.insert_text((30, 45), "Frequency")
            page.insert_text((30, 85), "daily")
            document.save(original)
    manifest = json.loads((inputs / "manifest.json").read_text())
    manifest["documents"]["target"] = original.name
    (inputs / "manifest.json").write_text(json.dumps(manifest))
    source = module.document_tools.snapshot(original)
    request = {
        "binding": {
            "id": "body",
            "document_id": "target",
            "source_sha256": source["source"]["sha256"],
            "table": "Records" if format == ".xlsx" else "page-1",
            "header_rows": [1],
            "first_row": 2,
            "last_row": 2,
            "columns": {"frequency": "A"},
            "mapping_basis": "Header on row 1.",
            "boundary_basis": "Row 2 is the complete scope.",
        },
        "checks": [{"kind": "field_nonempty", "id": "frequency", "field": "frequency"}],
    }
    (work / "snapshot.json").write_text(json.dumps(source))
    (work / "request.json").write_text(json.dumps(request))
    oracle = root / "report.json"
    oracle.write_text('{"expected":"PASS"}')
    repo = Path(__file__).resolve().parents[4]
    denied = [repo / ".env", Path(module.document_tools.__file__).resolve(), Path(module.ppap_docops.__file__).resolve(), oracle]
    with runner:
        agent_work, home, stage = runner.scratch / "agent", runner.scratch / "home", runner.output / "probe"
        for path in (agent_work, home, stage):
            path.mkdir()
        originals = runner.scratch / f"sources-{runner.stage_number}"
        runner._stage_sources(work, originals)
        (agent_work / "sources").symlink_to(originals, target_is_directory=True)
        policy = runner._seatbelt(work, agent_work, home, stage)
        environment = runner._environment(agent_work, home)
        program = (
            "import json,sys,ppap_docops; from pathlib import Path; "
            "from ppap_deep.semantic import checker_tools,document_tools; "
            "assert document_tools.snapshot(Path(sys.argv[1]))==json.loads(Path(sys.argv[2]).read_text()); "
            "print(json.dumps({'checker':checker_tools.__file__,'reader':document_tools.__file__,'parser':ppap_docops.__file__})); "
            "sys.argv=['checker',sys.argv[2],'--request',sys.argv[3],'--out','checked.json']; "
            "raise SystemExit(checker_tools.main())"
        )
        result = module.subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(policy), "/usr/bin/env", "python", "-c", program, str(agent_work / "sources" / original.name), str(work / "snapshot.json"), str(work / "request.json")],
            cwd=agent_work,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        imported = json.loads(result.stdout.splitlines()[0])
        assert imported == {"checker": str(runner.helpers / "checker_tools.py"), "reader": str(runner.helpers / "document_tools.py"), "parser": str(runner.python_root / "ppap_docops/__init__.py")}
        assert json.loads((agent_work / "checked.json").read_text())["checks"][0]["local_condition_status"] == "PASS"
        for path in denied:
            code = "from pathlib import Path; import sys; Path(sys.argv[1]).read_bytes()"
            blocked = module.subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy), sys.executable, "-c", code, str(path)], env=environment, cwd=agent_work, capture_output=True)
            assert blocked.returncode != 0
            assert b"PermissionError" in blocked.stderr
        probes = json.loads((stage / "boundary-probe.json").read_text())
        assert all(code != 0 for code in probes["write_denied_returncodes"].values())


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("grok") is None, reason="Requires the installed macOS grok core; uses only a local scripted provider")
def test_real_core_reads_stage_files_before_artifact_output(setup, monkeypatch):
    module, _, work, inputs, root = setup
    requests = []
    (work / "rule-source.json").unlink()
    source_name = "documents.json"
    source = {"value": 42, "source_marker": "stage-source-content-not-in-prompt"}
    source_path = work / source_name
    source_path.write_text(json.dumps(source))
    (work / "context-index.json").write_text(json.dumps({"verified_facts": [{"file": source_name, "scope": "frozen test source"}]}))
    source_outputs = []
    command = (
        'printf \'BASH_VERSION=%s\\nBASH_PATH=%s\\n\' "$BASH_VERSION" "$BASH"; '
        "python3 -c 'import json; from pathlib import Path; from ppap_deep.semantic import checker_tools; import openpyxl; "
        'print(checker_tools.__file__); value=json.loads(Path("documents.json").read_text())["value"]; Path("execution.json").write_text(json.dumps({"value":value}))\''
    )

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            outputs = {item["call_id"]: item.get("output") for item in body.get("input", []) if isinstance(item, dict) and item.get("type") == "function_call_output"}
            if [tool.get("name") for tool in body.get("tools", [])] == ["session_title"]:
                item = {"type": "function_call", "id": "fc_title", "call_id": "call_title", "status": "completed", "name": "session_title", "arguments": '{"title":"Runtime probe"}'}
            elif "call_source" not in outputs:
                item = {
                    "type": "function_call",
                    "id": "fc_source",
                    "call_id": "call_source",
                    "status": "completed",
                    "name": "read_file",
                    "arguments": json.dumps({"target_file": source_name}),
                }
            elif "call_probe" in outputs:
                source_outputs.append(outputs["call_source"])
                item = {
                    "type": "message",
                    "id": "msg_probe",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"completed":true,"artifact":"execution.json"}', "annotations": []}],
                }
            else:
                item = {
                    "type": "function_call",
                    "id": "fc_probe",
                    "call_id": "call_probe",
                    "status": "completed",
                    "name": "run_terminal_command",
                    "arguments": json.dumps({"command": command, "description": "Verify system shell and write the test artifact"}),
                }
            usage = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
            response = {
                "id": "resp_probe",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "grok-4.7",
                "output": [item],
                "usage": usage,
                "error": None,
                "incomplete_details": None,
                "instructions": None,
                "metadata": {},
                "parallel_tool_calls": True,
                "tools": [],
                "tool_choice": "auto",
                "temperature": 1,
                "top_p": 1,
                "text": {"format": {"type": "text"}},
                "truncation": "disabled",
                "store": False,
            }
            events = [
                {"type": "response.created", "response": dict(response, status="in_progress", output=[])},
                {"type": "response.output_item.added", "output_index": 0, "item": dict(item, status="in_progress")},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            data = b"".join(("data: " + json.dumps(dict(event, sequence_number=i)) + "\n\n").encode() for i, event in enumerate(events))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    monkeypatch.setattr(http.client, "HTTPSConnection", http.client.HTTPConnection)
    settings = SimpleNamespace(llm_base_url=f"https://127.0.0.1:{provider.server_address[1]}/v1", llm_api_key="local-scripted-provider")
    runner = module.LocalGrokRunner(settings, root / "real-core", shutil.which("grok"), input_root=inputs, seconds=45, max_requests=6)
    try:
        with runner:
            result = runner.run(work, "Read the frozen stage source and return its required value.", Answer, time.monotonic() + 40, reasoning_effort="medium")
        assert result.value == 42
        stage = runner.output / "stage-001-Answer"
        prompt = (stage / "prompt.txt").read_text()
        assert source_name in prompt and "context-index.json" in prompt
        assert source["source_marker"] not in prompt
        assert source_outputs and all(source["source_marker"] in str(output) for output in source_outputs)
        for request in requests:
            if not any(item.get("type") == "function_call_output" for item in request.get("input", [])):
                assert source["source_marker"] not in json.dumps(request)
        assert json.loads((stage / "inputs.json").read_text())[source_name] == source
        assert json.loads((stage / "source-hashes.json").read_text())[source_name] == hashlib.sha256(source_path.read_bytes()).hexdigest()
        events = (stage / "events.jsonl").read_text()
        outputs = [block for event in map(json.loads, events.splitlines()) for block in event.get("message", {}).get("content", []) if block.get("type") == "tool_result"]
        assert source["source_marker"] in outputs[0]["content"]
        assert "BASH_VERSION=3.2" in events
        assert "BASH_PATH=/bin/bash" in events
        assert str(runner.helpers / "checker_tools.py") in events
        assert json.loads(outputs[1]["content"])["exit_code"] == 0
        assert json.loads((work / "execution.json").read_text()) == {"value": 42}
        names = {tool.get("name") for request in requests for tool in request.get("tools", [])}
        assert {"run_terminal_command", "read_file", "write", "get_command_or_subagent_output", "kill_command_or_subagent"} <= names
        assert runner.usage["completed_requests"] == len(requests)
        assert runner.usage["uncertain"] is False
    finally:
        provider.shutdown()
        provider.server_close()
        serving.join(timeout=5)


def test_original_root_cannot_include_run_outputs(setup):
    module, _, _, _, root = setup
    settings = SimpleNamespace(llm_base_url="https://example.invalid", llm_api_key="secret")
    with pytest.raises(ValueError, match="must not expose"):
        module.LocalGrokRunner(settings, root / "runner", "/usr/bin/true", input_root=root)


@pytest.mark.parametrize("mode", ["wrong_hash", "unmapped_original"])
def test_compiler_requires_the_matching_authorized_original_before_model_submission(setup, mode):
    module, make, work, inputs, _ = setup
    if mode == "wrong_hash":
        (work / "rule-source.json").write_text(json.dumps({"source": {"sha256": "0" * 64}}))
    else:
        (inputs / "other-rule.json").write_text('{"rule":"different original"}')
        (inputs / "manifest.json").write_text(json.dumps({"rules": {"other": "other-rule.json"}, "documents": {}}))
    runner = make()
    with runner, pytest.raises(module.ModelProtocolError, match="Current rule original is missing"):
        runner.run(work, "compile", Answer, time.monotonic() + 30, reasoning_effort="medium")
    assert runner.requests_started == 0
    assert not (work / "execution.json").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="The local experiment uses macOS Seatbelt")
def test_stage_notes_are_writable_without_mutating_sources_or_opening_tmp(setup, monkeypatch):
    module, make, work, inputs, root = setup
    runner = make()
    original_notes = '{"assumptions":[]}'
    (work / "working-state.json").write_text(original_notes)
    original_manifest = (inputs / "manifest.json").read_text()
    native_popen = module.subprocess.Popen
    outcomes = {}

    class Process:
        pid = 123456
        returncode = 0

        def __init__(self):
            terminal = {"type": "result", "is_error": False, "stop_reason": "end_turn"}
            terminal["result"] = '{"completed":true,"artifact":"execution.json"}'
            self.stdout = io.StringIO(json.dumps(terminal) + "\n")
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            return self.returncode

    def core(command, **kwargs):
        if "--agent" not in command:
            return native_popen(command, **kwargs)
        scratch = Path(kwargs["cwd"])
        assert kwargs["env"]["TMPDIR"] == str(scratch)
        assert command[command.index("--tools") + 1] == ",".join(module.GROK_TOOL_ALIASES)
        assert not (scratch / "working-state.json").is_symlink()
        prompt = (scratch / "prompt.txt").read_text()
        assert "TMPDIR" in prompt and "/tmp is outside the permitted paths" in prompt
        assert "--json-schema" not in command

        def shell(label, script, *paths):
            result = module.subprocess.run(
                command[:3] + ["/bin/sh", "-c", script, "probe", *(str(path) for path in paths)],
                cwd=scratch,
                env=kwargs["env"],
                capture_output=True,
                text=True,
            )
            outcomes[label] = result.returncode
            return result

        assert shell("notes_write", 'printf \'{"assumptions":["unverified"]}\' > working-state.json').returncode == 0
        assert shell("tmpdir_write", 'printf helper > "$TMPDIR/helper.txt"').returncode == 0
        for filename in ("clauses.json", "artifact_schema.json"):
            assert shell(filename + "_read", 'cat "$1"', scratch / filename).returncode == 0
            assert shell(filename + "_write", 'printf changed > "$1"', scratch / filename).returncode != 0
        assert shell("host_notes_write", 'printf changed > "$1"', work / "working-state.json").returncode != 0
        assert shell("original_write", 'printf changed > "$1"', inputs / "manifest.json").returncode != 0
        forbidden = Path("/tmp") / f"ppap-scratch-probe-{runner.execution_id}"
        assert not forbidden.exists()
        try:
            assert shell("global_tmp_write", 'printf forbidden > "$1"', forbidden).returncode != 0
            assert not forbidden.exists()
        finally:
            forbidden.unlink(missing_ok=True)
        assert json.loads((scratch / "working-state.json").read_text()) == {"assumptions": ["unverified"]}
        assert (work / "working-state.json").read_text() == original_notes
        assert shell("artifact_write", "printf '{\"value\":42}' > execution.json").returncode == 0
        settle(runner, "local path contract probe")
        return Process()

    with runner:
        monkeypatch.setattr(module.subprocess, "Popen", core)
        monkeypatch.setattr(module.os, "killpg", lambda *args: None)
        assert runner.run(work, "Keep stage notes in working-state.json.", Answer, time.monotonic() + 30, reasoning_effort="medium").value == 42
        assert (work / "working-state.json").read_text() == original_notes
        assert (work / "clauses.json").read_text() == '{"rule":"read original"}'
        assert json.loads((work / "artifact_schema.json").read_text()) == Answer.model_json_schema()
        assert (inputs / "manifest.json").read_text() == original_manifest
    module.dump(root / "scratch-path-probe.json", {"mode": "artifact", "model_invoked": False, "returncodes": outcomes, "host_inputs_unchanged": True})


@pytest.mark.skipif(sys.platform != "darwin", reason="The local experiment uses macOS Seatbelt")
def test_artifact_chunks_are_read_back_after_native_boundary_and_settled_usage(setup, monkeypatch):
    module, make, work, inputs, root = setup
    runner = make()
    original_manifest = (inputs / "manifest.json").read_text()
    native_popen = module.subprocess.Popen

    class LargeAnswer(BaseModel):
        observations: list[dict]

    class Process:
        pid = 123456
        returncode = 0

        def __init__(self):
            terminal = {"type": "result", "is_error": False, "stop_reason": "end_turn", "result": '{"completed":true,"artifact":"execution.json"}'}
            self.stdout = io.StringIO(json.dumps(terminal) + "\n")
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            return self.returncode

    def core(command, **kwargs):
        if "--agent" not in command:
            return native_popen(command, **kwargs)
        assert "--json-schema" not in command
        assert command[command.index("--tools") + 1] == ",".join(module.GROK_TOOL_ALIASES)
        assert command[command.index("--reasoning-effort") + 1] == "medium"
        config = tomllib.loads((Path(kwargs["env"]["GROK_HOME"]) / "config.toml").read_text())
        assert config["model"]["grok-4.7"]["max_completion_tokens"] == 262144
        scratch = Path(kwargs["cwd"])
        assert not (scratch / "execution.json").exists()
        assert json.loads((scratch / "artifact_schema.json").read_text()) == LargeAnswer.model_json_schema()
        # Actual Seatbelt and shell write three chunks; no provider is called.
        commands = [
            "printf '{\"observations\":[' > execution.json",
            'i=0; while [ "$i" -lt 1648 ]; do [ "$i" -eq 0 ] || printf ","; printf \'{"row":%s,"reason":"%s"}\' "$i" "' + "evidence" * 55 + '"; i=$((i+1)); done >> execution.json',
            'printf "]}" >> execution.json',
        ]
        for script in commands:
            outcome = module.subprocess.run(command[:3] + ["/bin/sh", "-c", script], cwd=scratch, capture_output=True)
            assert outcome.returncode == 0, outcome.stderr
        assert (scratch / "execution.json").stat().st_size > 622_000
        settle(runner, "file stage")
        return Process()

    with runner:
        monkeypatch.setattr(module.subprocess, "Popen", core)
        monkeypatch.setattr(module.os, "killpg", lambda *args: None)
        result = runner.run(work, "audit", LargeAnswer, time.monotonic() + 30, reasoning_effort="low")
        assert len(result.observations) == 1648
        assert json.loads((work / "execution.json").read_text()) == result.model_dump()
        assert (inputs / "manifest.json").read_text() == original_manifest
    stage = root / "runner/stage-001-LargeAnswer"
    probes = json.loads((stage / "boundary-probe.json").read_text())
    assert all(code != 0 for code in probes["write_denied_returncodes"].values())
    assert json.loads((stage / "execution.json").read_text()) == result.model_dump()
    assert runner.usage["completed_requests"] == 1
    assert runner.summary["stages"][0]["output_mode"] == "artifact"
    assert not runner.scratch.exists()


@pytest.mark.parametrize(
    "receipt",
    [
        '{"value":42}',
        "",
        '{"completed":1,"artifact":"execution.json"}',
        '{"completed":false,"completed":true,"artifact":"execution.json"}',
        '{"completed":true,"artifact":"execution.json","extra":NaN}',
        '{"completed":true,"artifact":"elsewhere.json"}',
        '{"completed":true,"artifact":"execution.json","extra":true}',
    ],
)
def test_artifact_receipt_cannot_substitute_for_strict_completion(setup, monkeypatch, receipt):
    module, make, work, _, _ = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt)
        return {"result": receipt}

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner, pytest.raises(module.ModelProtocolError, match="completion receipt"):
        runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
    assert not (work / "execution.json").exists()


@pytest.mark.parametrize("mode", ["missing", "symlink", "hardlink", "directory", "fifo", "duplicate", "nonfinite", "schema", "protected"])
def test_artifact_readback_rejects_unsafe_or_invalid_content(setup, monkeypatch, mode):
    module, make, work, _, root = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        scratch = runner.scratch / f"stage-{runner.stage_number}"
        scratch.mkdir()
        artifact = scratch / "execution.json"
        external = root / "external.json"
        external.write_text('{"value":42}')
        if mode == "symlink":
            artifact.symlink_to(external)
        elif mode == "hardlink":
            os.link(external, artifact)
        elif mode == "directory":
            artifact.mkdir()
        elif mode == "fifo":
            os.mkfifo(artifact)
        elif mode == "duplicate":
            artifact.write_text('{"value":1,"value":42}')
        elif mode == "nonfinite":
            artifact.write_text('{"value":NaN}')
        elif mode == "schema":
            artifact.write_text('{"value":"not an integer"}')
        elif mode == "protected":
            artifact.write_text(json.dumps({"value": 42, "text": runner.token, "thinking": "private reasoning"}))
        settle(runner, prompt)
        return {"result": '{"completed":true,"artifact":"execution.json"}'}

    monkeypatch.setattr(runner, "_invoke", invoke)
    error = ValidationError if mode == "schema" else module.ModelProtocolError
    with runner, pytest.raises(error):
        runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
    assert not (work / "execution.json").exists()
    assert runner.usage["completed_requests"] == 1
    for path in runner.output.rglob("*.json"):
        text = path.read_text()
        assert runner.token not in text
        assert "private reasoning" not in text


@pytest.mark.parametrize("symlink", [False, True])
def test_artifact_refuses_existing_output_before_paid_call(setup, symlink):
    module, make, work, _, root = setup
    runner = make()
    old = work / "execution.json"
    if symlink:
        old.symlink_to(root / "absent.json")
    else:
        old.write_text('{"value":42}')
    with runner, pytest.raises(module.ModelProtocolError, match="already exists"):
        runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
    assert runner.requests_started == runner.stage_number == 0


def test_artifact_unknown_consumption_never_publishes_or_retries(setup, monkeypatch):
    module, make, work, _, _ = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        scratch = runner.scratch / f"stage-{runner.stage_number}"
        scratch.mkdir()
        (scratch / "execution.json").write_text('{"value":42}')
        settle(runner, prompt, unknown=True)
        return {"result": '{"completed":true,"artifact":"execution.json"}'}

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner:
        with pytest.raises(module.ModelProtocolError, match="not settled"):
            runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
        with pytest.raises(module.ModelProtocolError, match="unknown consumption"):
            runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
    assert not (work / "execution.json").exists()
    assert runner.stage_number == runner.requests_started == 1


def test_legacy_cli_config_preserves_default_completion_limit(setup):
    config = importlib.import_module("grok_support").cli_config("http://localhost/v1", "test-token")
    assert tomllib.loads(config)["model"]["grok-4.7"]["max_completion_tokens"] == 16000


@pytest.mark.parametrize("protected", [False, True])
def test_failed_stage_preserves_only_clean_readable_candidate_after_scratch_removal(setup, monkeypatch, protected):
    module, make, work, _, root = setup
    runner = make()

    def invoke(work, prompt, stage, deadline):
        settle(runner, prompt)
        payload = {"value": 42}
        if protected:
            payload["thinking"] = "private-test-provider-credential hidden reasoning"
        artifact_result(runner, payload)
        raise module.ModelProtocolError("Injected failure after artifact write")

    monkeypatch.setattr(runner, "_invoke", invoke)
    with runner, pytest.raises(module.ModelProtocolError):
        runner.run(work, "audit", Answer, time.monotonic() + 30, reasoning_effort="medium")
    candidate = root / "runner/stage-001-Answer/candidate.json"
    assert candidate.exists() is (not protected)
    assert not (work / "execution.json").exists()
    assert not runner.scratch.exists()
    if not protected:
        assert json.loads(candidate.read_text()) == {"trust": "candidate_unverified", "training_eligible": False, "artifact": {"value": 42}}
        assert json.loads((work / "candidate-execution.json").read_text()) == {"value": 42}
    assert runner.requests_started == 1
    saved = "\n".join(path.read_text() for path in (root / "runner").rglob("*.json"))
    assert "private-test-provider-credential" not in saved
    assert "hidden reasoning" not in saved
