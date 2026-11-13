"""Live deep activity: short steps shown to viewers while a deep review runs.

Activity is a broadcast, never a record: the guest turns Pi events into bounded steps,
the Controller forwards them, the Worker sends them as notices, and nothing stores them.
A step names an action, at most one supplier document file name, and the reviewed file.
Commands, scripts, program source, paths and tool results never leave the guest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ACTIVITY_FILE = "activity.jsonl"
MAX_SUBJECT = 80
# Enough for a long review; a runaway loop stops adding steps instead of filling the disk.
MAX_STEPS = 2000
# Pi lines larger than this are tool results or long scripts; they never become steps.
MAX_EVENT_LINE = 64 * 1024
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".emf", ".wmf"}
DOCUMENTS = {".xlsx", ".xlsm", ".xls", ".csv", ".pdf", ".docx", ".doc", ".pptx", ".ppt"}
# A supplier document named anywhere in a command; staged copies carry an "NN-" order prefix.
_DOCUMENT = re.compile(r"([^\s'\"/\\(),]+\.(?:" + "|".join(sorted(ext[1:] for ext in IMAGES | DOCUMENTS)) + r"))(?![\w.])", re.IGNORECASE)
_ORDER = re.compile(r"^\d{2,3}-")
_SEARCH = re.compile(r"(^|[\s|;&(])(rg|grep|egrep)\s")
_PROGRAM = re.compile(r"\.py\b|checker_lib|site-packages|__file__|\baudit\.", re.IGNORECASE)
_SHEET = re.compile(r"load_excel_workbook|openpyxl|zipfile|pandas|read_excel|xlrd|\.xlsx?\b", re.IGNORECASE)
_CONVERT = re.compile(r"\b(soffice|libreoffice)\b", re.IGNORECASE)
# Commentary that looks like code, paths or markup is not shown.
_CODE = re.compile(r"[`{}\[\]=;<>/\\$]|\b(def|import|print|return|lambda)\b|\w\(|_\w")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

Action = Literal["read_file", "parse_sheet", "view_image", "convert_image", "search", "read_program", "read_guide", "run_check", "record", "note"]


class Activity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    # A supplier document file name, or for a note its short sentence.
    subject: str | None = Field(default=None, min_length=1, max_length=MAX_SUBJECT)
    # The reviewed file (its version id), so each file shows its own steps.
    target: str | None = Field(default=None, pattern=r"^[0-9A-Za-z-]{1,64}$")


def _document(name: str) -> str | None:
    """A shown file name: only supplier documents and images, never program or work files."""
    base = PurePosixPath(name).name
    if PurePosixPath(base).suffix.lower() not in IMAGES | DOCUMENTS:
        return None
    base = " ".join(_CONTROL.sub(" ", _ORDER.sub("", base)).split())
    return base[:MAX_SUBJECT] or None


def _first_document(text: str) -> str | None:
    for match in _DOCUMENT.finditer(text):
        if name := _document(match.group(1)):
            return name
    return None


def _shell(command: str) -> Activity | None:
    if "stdout.jsonl" in command or "stderr.log" in command:
        return None
    if _CONVERT.search(command):
        return Activity(action="convert_image", subject=_first_document(command))
    if _PROGRAM.search(command) and not _SHEET.search(command):
        return Activity(action="read_program")
    if _SEARCH.search(command):
        return Activity(action="read_program" if _PROGRAM.search(command) else "search", subject=_first_document(command))
    if _SHEET.search(command):
        return Activity(action="parse_sheet", subject=_first_document(command))
    return Activity(action="run_check", subject=_first_document(command))


def _note(text: str) -> Activity | None:
    sentence = re.split(r"(?<=[。！？!?])\s*|\n", text.strip(), maxsplit=1)[0].strip()
    sentence = " ".join(_CONTROL.sub(" ", sentence).split())
    if not sentence or _CODE.search(sentence):
        return None
    return Activity(action="note", subject=sentence if len(sentence) <= MAX_SUBJECT else sentence[: MAX_SUBJECT - 1] + "…")


def activity_from_event(event: Any) -> Activity | None:
    """One Pi event as a step, or None when it says nothing a viewer needs."""
    if not isinstance(event, dict):
        return None
    try:
        if event.get("type") == "tool_execution_start":
            tool, raw = event.get("toolName"), event.get("args")
            args: dict[str, Any] = raw if isinstance(raw, dict) else {}
            path = args.get("path") if isinstance(args.get("path"), str) else ""
            if tool == "read" and path:
                suffix = PurePosixPath(path).suffix.lower()
                if suffix in IMAGES:
                    return Activity(action="view_image", subject=_document(path))
                if suffix in DOCUMENTS:
                    return Activity(action="read_file", subject=_document(path))
                if suffix == ".md":
                    return Activity(action="read_guide")
                if suffix == ".py" or _PROGRAM.search(path):
                    return Activity(action="read_program")
                return None if PurePosixPath(path).name in {"stdout.jsonl", "stderr.log"} else Activity(action="read_file")
            if tool == "bash" and isinstance(args.get("command"), str):
                return _shell(args["command"])
            if tool in {"edit", "write"}:
                return Activity(action="record")
            return None
        if event.get("type") == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                return None
            text = " ".join(part["text"] for part in message["content"] if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
            return _note(text) if text.strip() else None
    except ValidationError:
        return None
    return None


class ActivityRelay:
    """Guest side: follows Pi's event stream and appends steps to the run's activity file."""

    def __init__(self, events: Path, target: Path, reviewed: str | None = None) -> None:
        self.events, self.target, self.reviewed = events, target, reviewed
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
                steps.append(step.model_copy(update={"target": self.reviewed}).model_dump_json(exclude_none=True) + "\n")
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
