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


def test_pi_events_become_short_steps_without_paths_results_or_reasoning():
    assert activity_from_event(tool("read", path="/work/deep-review/t1/sources/控制计划.xlsx")) == Activity(kind="read", text="控制计划.xlsx")
    assert activity_from_event(tool("read", path="embedded/image3.EMF")) == Activity(kind="view", text="image3.EMF")
    script = "cd /work/deep-review\npython - <<'EOF'\nimport openpyxl\nwb = openpyxl.load_workbook('sources/cp.xlsx')\nEOF"
    assert activity_from_event(tool("bash", command=script)) == Activity(kind="run", text="wb = openpyxl.load_workbook('sources/cp.xlsx')")
    assert activity_from_event(tool("write", path="/work/out/review.json")) == Activity(kind="edit", text="review.json")
    note = {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "secret plan"}, {"type": "text", "text": "第 3 条：\n控制项编号齐全，维持 PASS。"}]}}
    assert activity_from_event(note) == Activity(kind="note", text="第 3 条： 控制项编号齐全，维持 PASS。")
    # Reasoning, tool results and other roles are not steps.
    assert activity_from_event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}}) is None
    assert activity_from_event({"type": "tool_execution_end", "toolName": "read", "result": {"content": "file body"}}) is None
    assert activity_from_event({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "prompt"}]}}) is None
    long = activity_from_event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "很长" * 200}]}})
    assert long is not None and len(long.text) == live.MAX_TEXT and long.text.endswith("…")


def test_relay_follows_a_growing_event_stream_and_skips_huge_lines(tmp_path: Path):
    events, target = tmp_path / "stdout.jsonl", tmp_path / ACTIVITY_FILE
    relay = ActivityRelay(events, target)
    relay.pump()  # Pi has not written yet.
    first = json.dumps(tool("read", path="a.xlsx"))
    events.write_text(first + "\n" + first[:10], encoding="utf-8")
    relay.pump()
    huge = json.dumps({"type": "tool_execution_end", "result": "x" * (live.MAX_EVENT_LINE + 10)})
    with events.open("a", encoding="utf-8") as stream:
        stream.write(first[10:] + "\n" + huge + "\n" + json.dumps(tool("bash", command="ls")) + "\n")
    relay.pump()
    steps = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert steps == [{"kind": "read", "text": "a.xlsx"}, {"kind": "read", "text": "a.xlsx"}, {"kind": "run", "text": "ls"}]


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
    path.write_bytes(b'{"kind":"read","text":"a.xlsx"}\n{"kind":"shell","text":"rm"}\n{"kind":"note","text":"' + b"x" * 300 + b'"}\n{"kind":"run"')
    steps, used = parse_steps(read_appended(tmp_path, ACTIVITY_FILE, 0, 64 * 1024))
    # Unknown kinds and oversized text are dropped; the partial last line waits for its end.
    assert steps == [Activity(kind="read", text="a.xlsx")]
    assert used == path.read_bytes().rfind(b"\n") + 1
    with path.open("ab") as stream:
        stream.write(b',"text":"ls"}\n')
    assert parse_steps(read_appended(tmp_path, ACTIVITY_FILE, used, 64 * 1024))[0] == [Activity(kind="run", text="ls")]
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
                write_frame(conn, {"op": "activity", "activity": {"kind": "read", "text": "cp.xlsx"}})
                write_frame(conn, {"op": "progress", "progress": {"phase": "checking"}})
                write_frame(conn, {"op": "activity", "activity": {"kind": "note", "text": "维持 NG"}})
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
    assert steps == [{"kind": "read", "text": "cp.xlsx"}, {"kind": "note", "text": "维持 NG"}]
    assert progress == [{"phase": "checking"}]
