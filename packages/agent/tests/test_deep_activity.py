"""Live deep steps: Pi events become short steps, the Controller forwards only valid ones."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest
from agent.client import DeepClient, DeepError, read_frame, write_frame
from agent.execution import activity as live
from agent.execution.activity import ACTIVITY_FILE, Activity, ActivityRelay, activity_from_event, parse_steps
from agent.execution.secure_files import read_appended


def tool(name: str, **args) -> dict:
    return {"type": "tool_execution_start", "toolCallId": "c1", "toolName": name, "args": args}


def note(text: str) -> dict:
    return {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "secret plan"}, {"type": "text", "text": text}]}}


def test_pi_events_become_actions_with_only_document_names():
    step = activity_from_event
    assert step(tool("read", path="/work/deep-review/review-0-0/files/00-控制计划-776.xlsx")) == Activity(action="read_file", subject="控制计划-776.xlsx")
    assert step(tool("read", path="embedded/image3.EMF")) == Activity(action="view_image", subject="image3.EMF")
    script = "cd /work\npython - <<'EOF'\nimport openpyxl\nwb = load_excel_workbook('files/00-控制计划-776.xlsx')\nprint(wb['Sheet1']['A1'].value)\nEOF"
    assert step(tool("bash", command=script)) == Activity(action="parse_sheet", subject="控制计划-776.xlsx")
    assert step(tool("bash", command="soffice --headless --convert-to png media/image3.emf")) == Activity(action="convert_image", subject="image3.emf")
    assert step(tool("bash", command='rg -n "编制人" files/00-cp.xlsx.txt')) == Activity(action="search")
    # Program source is never named.
    grep = 'rg -n "跑道|rule_5" /app/.venv/lib/python3.12/site-packages/audit/checker_lib/uaes/control_plan.py | head -80'
    assert step(tool("bash", command=grep)) == Activity(action="read_program")
    assert step(tool("bash", command="python -c 'import audit; print(audit.__file__)'")) == Activity(action="read_program")
    assert step(tool("read", path="/app/.venv/lib/python3.12/site-packages/audit/checker_lib/uaes/fmea.py")) == Activity(action="read_program")
    assert step(tool("read", path="skills/ppap-semantic-audit/references/review.md")) == Activity(action="read_guide")
    assert step(tool("bash", command="cands=[]\nfor row in rows: cands.append(row)")) == Activity(action="run_check")
    assert step(tool("write", path="/work/out/review.json", content="{secret}")) == Activity(action="record")
    # Pi's own logs are not steps.
    assert step(tool("bash", command='python - <<EOF\nwith open("stdout.jsonl") as f: print(f.read())\nEOF')) is None
    assert step(tool("read", path="stdout.jsonl")) is None
    # Commentary: its first sentence, unless it looks like code or a path.
    assert step(note("第 3 条：控制项编号齐全，维持 PASS。下一步核对第 4 条。")) == Activity(action="note", subject="第 3 条：控制项编号齐全，维持 PASS。")
    assert step(note("我先运行 `load_excel_workbook()` 看看")) is None
    assert step(note("读取 /app/audit/x 下的资料")) is None
    long = step(note("很长" * 100))
    assert long is not None and long.subject is not None and len(long.subject) == live.MAX_SUBJECT and long.subject.endswith("…")
    # Reasoning, tool results and other roles are not steps.
    assert step({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}}) is None
    assert step({"type": "tool_execution_end", "toolName": "read", "result": {"content": "file body"}}) is None
    assert step({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "prompt"}]}}) is None


def test_relay_follows_a_growing_event_stream_and_skips_huge_lines(tmp_path: Path):
    events, target = tmp_path / "stdout.jsonl", tmp_path / ACTIVITY_FILE
    relay = ActivityRelay(events, target, "version-1")
    relay.pump()  # Pi has not written yet.
    first = json.dumps(tool("read", path="a.xlsx"))
    events.write_text(first + "\n" + first[:10], encoding="utf-8")
    relay.pump()
    huge = json.dumps({"type": "tool_execution_end", "result": "x" * (live.MAX_EVENT_LINE + 10)})
    with events.open("a", encoding="utf-8") as stream:
        stream.write(first[10:] + "\n" + huge + "\n" + json.dumps(tool("bash", command="ls")) + "\n")
    relay.pump()
    steps = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert steps == [{"action": "read_file", "subject": "a.xlsx", "target": "version-1"}] * 2 + [{"action": "run_check", "target": "version-1"}]


def test_relay_stops_adding_steps_at_the_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(live, "MAX_STEPS", 2)
    events, target = tmp_path / "stdout.jsonl", tmp_path / ACTIVITY_FILE
    events.write_text("".join(json.dumps(tool("bash", command=f"echo {index}")) + "\n" for index in range(5)), encoding="utf-8")
    relay = ActivityRelay(events, target)
    relay.pump()
    relay.pump()
    assert len(target.read_text(encoding="utf-8").splitlines()) == 2


def test_controller_reads_only_complete_valid_steps_from_a_regular_file(tmp_path: Path):
    path = tmp_path / ACTIVITY_FILE
    assert read_appended(tmp_path, ACTIVITY_FILE, 0, 1024) == b""
    path.write_bytes(
        b'{"action":"read_file","subject":"a.xlsx"}\n{"action":"shell","subject":"rm"}\n{"action":"note","subject":"' + b"x" * 300 + b'"}\n{"action":"run_check","target":"../x"}\n{"action":"search"'
    )
    steps, used = parse_steps(read_appended(tmp_path, ACTIVITY_FILE, 0, 64 * 1024))
    # Unknown actions, oversized text and bad targets are dropped; the partial last line waits for its end.
    assert steps == [Activity(action="read_file", subject="a.xlsx")]
    assert used == path.read_bytes().rfind(b"\n") + 1
    with path.open("ab") as stream:
        stream.write(b',"target":"version-1"}\n')
    assert parse_steps(read_appended(tmp_path, ACTIVITY_FILE, used, 64 * 1024))[0] == [Activity(action="search", target="version-1")]
    path.unlink()
    os.symlink("/etc/hosts", path)
    with pytest.raises(OSError):
        read_appended(tmp_path, ACTIVITY_FILE, 0, 1024)


def test_client_hands_activity_frames_to_the_worker_before_the_result():
    with tempfile.TemporaryDirectory(prefix="pa-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "c.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(1)

        def serve():
            conn, _ = listener.accept()
            with conn:
                read_frame(conn)
                write_frame(conn, {"op": "activity", "activity": {"action": "read_file", "subject": "cp.xlsx"}})
                write_frame(conn, {"op": "progress", "progress": {"phase": "checking"}})
                write_frame(conn, {"op": "activity", "activity": {"action": "note", "subject": "维持 NG"}})
                write_frame(conn, {"op": "error", "code": "deep_timeout"})

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        steps, progress = [], []
        try:
            with pytest.raises(DeepError) as failed:
                DeepClient(path, timeout=5)._request({"op": "execute"}, on_progress=progress.append, on_activity=steps.append)
        finally:
            server.join(timeout=5)
            listener.close()
    assert failed.value.code == "deep_timeout"
    assert steps == [{"action": "read_file", "subject": "cp.xlsx"}, {"action": "note", "subject": "维持 NG"}]
    assert progress == [{"phase": "checking"}]
