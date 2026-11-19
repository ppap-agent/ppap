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


def test_only_the_rules_and_the_users_documents_become_steps():
    step = activity_from_event
    # The user's own rules, loaded for the round.
    assert step(tool("read", path="/work/deep-review/review-0-0/rules.json")) == Activity(action="load_rules")
    assert step(tool("bash", command="python -c 'import json; print(json.load(open(\"rules.json\")))'")) == Activity(action="load_rules")
    # The user's documents and their images, named by file name only.
    assert step(tool("read", path="/work/deep-review/review-0-0/files/00-控制计划-776.xlsx")) == Activity(action="read_file", subject="控制计划-776.xlsx")
    assert step(tool("read", path="embedded/image3.EMF")) == Activity(action="view_image", subject="image3.EMF")
    script = "cd /work\npython - <<'EOF'\nimport openpyxl\nwb = load_excel_workbook('files/00-控制计划-776.xlsx')\nprint(wb['Sheet1']['A1'].value)\nEOF"
    assert step(tool("bash", command=script)) == Activity(action="read_sheet", subject="控制计划-776.xlsx")
    assert step(tool("bash", command="python -c 'import fitz; fitz.open(\"files/01-flow.pdf\")'")) == Activity(action="read_file", subject="flow.pdf")
    assert step(tool("bash", command="soffice --headless --convert-to png media/image3.emf")) == Activity(action="view_image", subject="image3.emf")
    # Our guides, programs, searches, scripts, records and Pi's own logs are not shown.
    hidden = [
        tool("read", path="skills/ppap-semantic-audit/references/review.md"),
        tool("read", path="/app/.venv/lib/python3.12/site-packages/audit/checker_lib/uaes/fmea.py"),
        tool("read", path="/work/deep-review/review-0-0/program-results.json"),
        tool("bash", command='rg -n "编制人" files/00-cp.xlsx.txt'),
        tool("bash", command='rg -n "跑道|rule_5" /app/.venv/lib/python3.12/site-packages/audit/checker_lib/uaes/control_plan.py | head -80'),
        tool("bash", command="python -c 'import audit; print(audit.__file__)'"),
        tool("bash", command="cands=[]\nfor row in rows: cands.append(row)"),
        tool("write", path="/work/out/review.json", content="{secret}"),
        tool("bash", command='python - <<EOF\nwith open("stdout.jsonl") as f: print(f.read())\nEOF'),
        tool("read", path="stdout.jsonl"),
        # Notes are the reviewer's own words about how it works: never shown.
        note("第 3 条：控制项编号齐全，维持 PASS。下一步核对第 4 条。"),
        {"type": "tool_execution_end", "toolName": "read", "result": {"content": "file body"}},
    ]
    assert [step(event) for event in hidden] == [None] * len(hidden)
    # Converting pages to images is viewing images, even before the document is known.
    assert step(tool("bash", command="soffice --headless --convert-to png")) == Activity(action="view_image")


def test_with_the_rounds_documents_steps_name_only_them(tmp_path: Path):
    events, target = tmp_path / "stdout.jsonl", tmp_path / ACTIVITY_FILE
    relay = ActivityRelay(events, target, "version-1", live.shown_names(["控制计划.xlsx", "PFMEA.xls"]))
    lines = [
        tool("read", path="/work/deep-review/review-0-0/rules.json"),
        tool("read", path="media/image1.png"),
        tool("read", path="/work/deep-review/review-0-0/files/00-控制计划.xlsx"),
        tool("read", path="media/image1.png"),
        tool("read", path="/tmp/converted/控制计划.pdf"),
        tool("bash", command="python -c 'load_excel_workbook(\"files/01-PFMEA.xls\")'"),
    ]
    events.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    relay.pump()
    steps = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    # An image taken out of a document is named by the last of the user's documents read, or not at all;
    # a converted copy is not the user's document and is not shown.
    assert [(row["action"], row.get("subject")) for row in steps] == [
        ("load_rules", None),
        ("view_image", None),
        ("read_file", "控制计划.xlsx"),
        ("view_image", "控制计划.xlsx"),
        ("read_sheet", "PFMEA.xls"),
    ]


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
    assert steps == [{"action": "read_file", "subject": "a.xlsx", "target": "version-1"}] * 2


def test_relay_stops_adding_steps_at_the_cap(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(live, "MAX_STEPS", 2)
    events, target = tmp_path / "stdout.jsonl", tmp_path / ACTIVITY_FILE
    events.write_text("".join(json.dumps(tool("read", path=f"{index}.xlsx")) + "\n" for index in range(5)), encoding="utf-8")
    relay = ActivityRelay(events, target)
    relay.pump()
    relay.pump()
    assert len(target.read_text(encoding="utf-8").splitlines()) == 2


def test_controller_reads_only_complete_valid_steps_from_a_regular_file(tmp_path: Path):
    path = tmp_path / ACTIVITY_FILE
    assert read_appended(tmp_path, ACTIVITY_FILE, 0, 1024) == b""
    path.write_bytes(
        b'{"action":"read_file","subject":"a.xlsx"}\n{"action":"shell","subject":"rm"}\n'
        + b'{"action":"read_file","subject":"'
        + b"x" * 300
        + b'"}\n{"action":"read_sheet","target":"../x"}\n{"action":"load_rules"'
    )
    steps, used = parse_steps(read_appended(tmp_path, ACTIVITY_FILE, 0, 64 * 1024))
    # Unknown actions, oversized text and bad targets are dropped; the partial last line waits for its end.
    assert steps == [Activity(action="read_file", subject="a.xlsx")]
    assert used == path.read_bytes().rfind(b"\n") + 1
    with path.open("ab") as stream:
        stream.write(b',"target":"version-1"}\n')
    assert parse_steps(read_appended(tmp_path, ACTIVITY_FILE, used, 64 * 1024))[0] == [Activity(action="load_rules", target="version-1")]
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
                write_frame(conn, {"op": "activity", "activity": {"action": "view_image", "subject": "a.png"}})
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
    assert steps == [{"action": "read_file", "subject": "cp.xlsx"}, {"action": "view_image", "subject": "a.png"}]
    assert progress == [{"phase": "checking"}]
