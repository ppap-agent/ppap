"""Live deep activity: short steps shown to viewers while a deep review runs.

Activity is a broadcast, never a record: the guest turns Pi events into bounded steps,
the Controller forwards them, the Worker sends them as notices, and nothing stores them.
Steps carry file names and short command or commentary text, never tool results.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ACTIVITY_FILE = "activity.jsonl"
MAX_TEXT = 160
# Enough for a long review; a runaway loop stops adding steps instead of filling the disk.
MAX_STEPS = 2000
# Pi lines larger than this are tool results or long scripts; they never become steps.
MAX_EVENT_LINE = 64 * 1024
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".emf", ".wmf"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class Activity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["read", "view", "run", "edit", "note"]
    text: str = Field(min_length=1, max_length=MAX_TEXT)


def _clean(text: str) -> str:
    text = " ".join(_CONTROL.sub(" ", text).split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _name(path: Any) -> str | None:
    return PurePosixPath(path).name or None if isinstance(path, str) and path.strip() else None


def _command(command: Any) -> str | None:
    """The line that says what a shell step does: past `cd`, heredoc openers and imports."""
    if not isinstance(command, str):
        return None
    lines = [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]
    body = [line for line in lines if not line.startswith(("cd ", "set -", "import ", "from ")) and "<<" not in line]
    return (body or lines)[0] if body or lines else None


def activity_from_event(event: Any) -> Activity | None:
    """One Pi event as a step, or None when it says nothing a viewer needs."""
    if not isinstance(event, dict):
        return None
    try:
        if event.get("type") == "tool_execution_start":
            tool, raw = event.get("toolName"), event.get("args")
            args: dict[str, Any] = raw if isinstance(raw, dict) else {}
            if tool == "read" and (name := _name(args.get("path"))):
                return Activity(kind="view" if PurePosixPath(name).suffix.lower() in IMAGES else "read", text=_clean(name))
            if tool == "bash" and (line := _command(args.get("command"))):
                return Activity(kind="run", text=_clean(line))
            if tool in {"edit", "write"} and (name := _name(args.get("path"))):
                return Activity(kind="edit", text=_clean(name))
            return None
        if event.get("type") == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                return None
            text = " ".join(part["text"] for part in message["content"] if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
            return Activity(kind="note", text=_clean(text)) if text.strip() else None
    except ValidationError:
        return None
    return None


class ActivityRelay:
    """Guest side: follows Pi's event stream and appends steps to the run's activity file."""

    def __init__(self, events: Path, target: Path) -> None:
        self.events, self.target = events, target
        self.offset, self.pending, self.steps = 0, b"", 0

    def pump(self) -> None:
        if self.steps >= MAX_STEPS:
            return
        try:
            with self.events.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(1024 * 1024)
        except OSError:
            return
        self.offset += len(chunk)
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > MAX_EVENT_LINE:
            # A huge line (a tool result) is skipped; keep reading after it.
            self.pending = b"\xff"
        steps = []
        for line in lines:
            if not line or len(line) > MAX_EVENT_LINE or line.startswith(b"\xff"):
                continue
            try:
                step = activity_from_event(json.loads(line))
            except ValueError:
                continue
            if step is not None:
                steps.append(step.model_dump_json() + "\n")
        steps = steps[: MAX_STEPS - self.steps]
        if steps:
            self.steps += len(steps)
            try:
                with self.target.open("a", encoding="utf-8") as out:
                    out.writelines(steps)
            except OSError:
                self.steps = MAX_STEPS


def parse_steps(data: bytes) -> tuple[list[Activity], int]:
    """Complete, valid steps in `data` and how many bytes they used. Invalid lines are dropped."""
    end = data.rfind(b"\n") + 1
    steps = []
    for line in data[:end].split(b"\n"):
        if not line:
            continue
        try:
            steps.append(Activity.model_validate_json(line))
        except ValidationError:
            continue
    return steps, end
