"""Answer model requests from recordings; a new request is asked once and recorded when its case passes."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPLAY_DIR = Path(__file__).resolve().parents[5] / "assets/corpus/replay"
REPLAY = "replay"
LIVE = "live"


def mode() -> str:
    value = os.environ.get("PPAP_CORPUS_MODEL", REPLAY)
    if value not in {REPLAY, LIVE}:
        raise ValueError(f"PPAP_CORPUS_MODEL must be {REPLAY} or {LIVE}")
    return value


def request_key(kind: str, request: Any) -> str:
    """Identify what the model is shown, not which deployment answers it."""
    return hashlib.sha256(json.dumps([kind, request], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class Recorder:
    """New answers wait for the case verdict, so a failed case asks again next run."""

    def __init__(self, recordings: Path = REPLAY_DIR, output: Path | None = None) -> None:
        self.recordings = recordings
        self.output = output or Path(os.environ.get("PPAP_CORPUS_REPLAY_OUT") or recordings)
        self.pending: dict[str, dict[str, Any]] = {}
        self.used: set[str] = set()
        self.lock = threading.Lock()

    def lookup(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            self.used.add(key)
            if key in self.pending:
                return self.pending[key]
        for root in (self.recordings, self.output):
            path = root / f"{key}.json"
            if path.is_file():
                return json.loads(path.read_text())
        return None

    def add(self, key: str, entry: dict[str, Any]) -> None:
        with self.lock:
            self.pending.setdefault(key, entry)

    def finish(self, *, keep: bool) -> None:
        with self.lock:
            pending, self.pending = self.pending, {}
            self.used = set()
        if not keep:
            return
        self.output.mkdir(parents=True, exist_ok=True)
        for key, entry in pending.items():
            target = self.output / f"{key}.json"
            temporary = target.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(entry, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
            temporary.replace(target)

    def discard(self) -> None:
        with self.lock:
            self.pending = {}


def install(monkeypatch: Any, recorder: Recorder) -> None:
    """Replace the three model transports below every product decision."""
    from audit.engine.checkers.client import AIClient
    from documents.ocr import client as ocr_client
    from documents.ocr.paddle import client as paddle_client

    real_ocr = ocr_client._request_response
    real_text = AIClient.response_text
    real_paddle = paddle_client._post_page_sync

    async def ocr_request(kwargs: dict[str, Any]) -> Any:
        key = request_key("ocr", {name: kwargs.get(name) for name in ("input", "text", "max_output_tokens")})
        entry = recorder.lookup(key)
        if entry is None:
            response = await real_ocr(kwargs)
            if getattr(response, "status", None) != "completed":
                return response
            entry = {"kind": "ocr", "model": kwargs.get("model"), "answer": response.output_text}
            recorder.add(key, entry)
            return response
        return SimpleNamespace(status="completed", output_text=entry["answer"], usage=None, model=entry.get("model"), incomplete_details=None)

    def text_request(self: Any, messages: Any, *args: Any, **kwargs: Any) -> str:
        names = ("max_tokens", "temperature", "text_format")
        bound = {**dict(zip(names, args)), **{name: kwargs[name] for name in names if name in kwargs}}
        key = request_key("text", {"messages": messages, **bound})
        entry = recorder.lookup(key)
        if entry is None:
            answer = real_text(self, messages, *args, **kwargs)
            recorder.add(key, {"kind": "text", "model": getattr(self.settings, "MODEL_NAME", None), "answer": answer})
            return answer
        return entry["answer"]

    def paddle_request(endpoint: str, payload: dict, headers: dict, timeout: float) -> object:
        key = request_key("paddle", payload)
        entry = recorder.lookup(key)
        if entry is None:
            answer = real_paddle(endpoint, payload, headers, timeout)
            recorder.add(key, {"kind": "paddle", "answer": answer})
            return answer
        return entry["answer"]

    monkeypatch.setattr(ocr_client, "_request_response", ocr_request)
    monkeypatch.setattr(AIClient, "response_text", text_request)
    monkeypatch.setattr(paddle_client, "_post_page_sync", paddle_request)


__all__ = ["LIVE", "REPLAY", "REPLAY_DIR", "Recorder", "install", "mode", "request_key"]
