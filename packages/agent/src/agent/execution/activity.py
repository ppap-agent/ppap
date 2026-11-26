"""Live deep activity: short steps shown to viewers while a deep review runs.

Activity is a broadcast, never a record: the guest turns Pi events into bounded steps,
the Controller forwards them, the Worker sends them as notices, and nothing stores them.
A step names an action (loading the rules, reading a document or a sheet, viewing an image), at most
one supplier document file name, and the reviewed file. Notes, commands, scripts, program source,
paths and tool results never leave the guest.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
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
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

# Only what the reviewer does with the user's own material: loading the round's rules (the user's
# own rules) and reading the documents and their images. Notes, our guides, programs and scripts are not shown.
Action = Literal["load_rules", "read_file", "read_sheet", "view_image"]
RULES_FILE = "rules.json"


class Activity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    # A supplier document file name.
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


def _reading(subject: str | None, *, sheet: bool = False) -> Activity | None:
    """Reading one of the user's documents; nothing without one."""
    if subject is None:
        return None
    if PurePosixPath(subject).suffix.lower() in IMAGES:
        return Activity(action="view_image", subject=subject)
    return Activity(action="read_sheet" if sheet else "read_file", subject=subject)


def _shell(command: str) -> Activity | None:
    if "stdout.jsonl" in command or "stderr.log" in command:
        return None
    if _CONVERT.search(command):
        return Activity(action="view_image", subject=_first_document(command))
    if _PROGRAM.search(command) and not _SHEET.search(command) or _SEARCH.search(command):
        return None
    if RULES_FILE in command:
        return Activity(action="load_rules")
    return _reading(_first_document(command), sheet=bool(_SHEET.search(command)))


def activity_from_event(event: Any) -> Activity | None:
    """One Pi event as a step, or None when it says nothing a viewer needs."""
    if not isinstance(event, dict) or event.get("type") != "tool_execution_start":
        return None
    try:
        tool, raw = event.get("toolName"), event.get("args")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        path = args.get("path") if isinstance(args.get("path"), str) else ""
        if tool == "read" and path:
            if PurePosixPath(path).name == RULES_FILE:
                return Activity(action="load_rules")
            return _reading(_document(path)) if not (path.endswith(".py") or _PROGRAM.search(path)) else None
        if tool == "bash" and isinstance(args.get("command"), str):
            return _shell(args["command"])
    except ValidationError:
        return None
    return None


def shown_names(filenames: Iterable[str]) -> frozenset[str]:
    """The round's documents as steps name them."""
    return frozenset(name for name in map(_document, filenames) if name)


_APPEND = threading.Lock()


class ActivityRelay:
    """Guest side: follows Pi's event stream and appends steps to the run's activity file.

    With ``originals`` (the round's documents as steps name them), a step names only the user's own documents:
    reading anything else is not shown, and an image taken out of a document is named by the last document read.
    """

    def __init__(self, events: Path, target: Path, reviewed: str | None = None, originals: frozenset[str] | None = None) -> None:
        self.events, self.target, self.reviewed, self.originals = events, target, reviewed, originals
        self.offset, self.pending, self.steps = 0, b"", 0
        self.document: str | None = None

    def _attribute(self, step: Activity) -> Activity | None:
        if self.originals is None or step.action == "load_rules":
            return step
        if step.subject in self.originals:
            self.document = step.subject
            return step
        if step.action == "view_image":
            return step.model_copy(update={"subject": self.document})
        return None

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
            step = self._attribute(step) if step is not None else None
            if step is not None:
                steps.append(step.model_copy(update={"target": self.reviewed}).model_dump_json(exclude_none=True) + "\n")
        steps = steps[: MAX_STEPS - self.steps]
        if steps:
            self.steps += len(steps)
            try:
                # Files reviewed side by side append to one file; each batch of whole lines goes in at once.
                with _APPEND, self.target.open("a", encoding="utf-8") as out:
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
