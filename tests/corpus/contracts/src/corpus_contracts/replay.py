"""Answer model requests from readable recordings; a new request is asked once and recorded when its case passes.

Each test scope has one JSON file listing what the model was shown and what it answered.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPLAY_DIR = Path(__file__).resolve().parents[5] / "assets/corpus/replay"
REPLAY = "replay"
LIVE = "live"
_BINARY = re.compile(r"^[A-Za-z0-9+/=\s]{4096,}$")


def mode() -> str:
    value = os.environ.get("PPAP_CORPUS_MODEL", REPLAY)
    if value not in {REPLAY, LIVE}:
        raise ValueError(f"PPAP_CORPUS_MODEL must be {REPLAY} or {LIVE}")
    return value


def _image_summary(value: str) -> str:
    """PNG bytes differ across platforms; its size says whether the crop changed."""
    _, _, encoded = value.partition(",")
    head = base64.b64decode(encoded[:64] + "=" * (-len(encoded[:64]) % 4))
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", head[16:24])
        return f"<png {width}x{height}>"
    return "<image>"


def shown_to_model(value: Any) -> Any:
    """What the model is shown, readable and without image or document bytes."""
    if isinstance(value, dict):
        return {key: shown_to_model(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [shown_to_model(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return _image_summary(value)
    if isinstance(value, str) and _BINARY.fullmatch(value):
        return "<binary>"
    return value


class Recorder:
    """New answers wait for the case verdict, so a failed case asks again next run."""

    def __init__(self, scope: str, recordings: Path = REPLAY_DIR, output: Path | None = None) -> None:
        relative = f"{scope}.json"
        self.path = recordings / relative
        self.output = (output or Path(os.environ.get("PPAP_CORPUS_REPLAY_OUT") or recordings)) / relative
        self.entries: list[dict[str, Any]] = []
        for path in (self.path, self.output):
            if path.is_file():
                self.entries.extend(json.loads(path.read_text(encoding="utf-8")))
        self.pending: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def lookup(self, kind: str, request: Any) -> dict[str, Any] | None:
        shown = json.loads(json.dumps(shown_to_model(request), ensure_ascii=False, default=str))
        with self.lock:
            return next((entry for entry in (*self.entries, *self.pending) if entry["kind"] == kind and entry["request"] == shown), None)

    def add(self, kind: str, request: Any, answer: Any, model: Any = None) -> None:
        shown = json.loads(json.dumps(shown_to_model(request), ensure_ascii=False, default=str))
        with self.lock:
            self.pending.append({"kind": kind, "model": model, "request": shown, "answer": answer})

    def finish(self, *, keep: bool) -> None:
        with self.lock:
            pending, self.pending = self.pending, []
        if not keep or not pending:
            return
        self.entries.extend(pending)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        temporary.replace(self.output)


def install(monkeypatch: Any, recorder: Recorder) -> None:
    """Replace the three model transports below every product decision."""
    from audit.engine.checkers.client import AIClient
    from documents.ocr import client as ocr_client
    from documents.ocr.paddle import client as paddle_client

    real_ocr = ocr_client._request_response
    real_text = AIClient.response_text
    real_paddle = paddle_client._post_page_sync

    async def ocr_request(kwargs: dict[str, Any]) -> Any:
        request = {name: kwargs.get(name) for name in ("input", "text", "max_output_tokens")}
        entry = recorder.lookup("ocr", request)
        if entry is None:
            response = await real_ocr(kwargs)
            if getattr(response, "status", None) == "completed":
                recorder.add("ocr", request, response.output_text, kwargs.get("model"))
            return response
        return SimpleNamespace(status="completed", output_text=entry["answer"], usage=None, model=entry.get("model"), incomplete_details=None)

    def text_request(self: Any, messages: Any, *args: Any, **kwargs: Any) -> str:
        names = ("max_tokens", "temperature", "text_format")
        request = {"messages": messages, **dict(zip(names, args)), **{name: kwargs[name] for name in names if name in kwargs}}
        entry = recorder.lookup("text", request)
        if entry is None:
            answer = real_text(self, messages, *args, **kwargs)
            recorder.add("text", request, answer, getattr(self.settings, "MODEL_NAME", None))
            return answer
        return entry["answer"]

    def paddle_request(endpoint: str, payload: dict, headers: dict, timeout: float) -> object:
        entry = recorder.lookup("paddle", payload)
        if entry is None:
            answer = real_paddle(endpoint, payload, headers, timeout)
            recorder.add("paddle", payload, answer)
            return answer
        return entry["answer"]

    monkeypatch.setattr(ocr_client, "_request_response", ocr_request)
    monkeypatch.setattr(AIClient, "response_text", text_request)
    monkeypatch.setattr(paddle_client, "_post_page_sync", paddle_request)


__all__ = ["LIVE", "REPLAY", "REPLAY_DIR", "Recorder", "install", "mode", "shown_to_model"]
