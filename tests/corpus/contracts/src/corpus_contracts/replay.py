"""Answer model requests from readable recordings; a new request is asked once and recorded when its case passes.

Each test scope has one JSON file listing what the model was shown and what it answered.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from audit.engine.checkers.client import TextCompletion
from documents.model_access import ModelAccess
from documents.ocr.env import effective_ocr_extra_body
from pydantic import SecretStr

REPLAY_DIR = Path(__file__).resolve().parents[5] / "assets/corpus/replay"
REPLAY = "replay"
LIVE = "live"
_BINARY = re.compile(r"^[A-Za-z0-9+/=\s]{4096,}$")


def mode() -> str:
    value = os.environ.get("PPAP_CORPUS_MODEL", REPLAY)
    if value not in {REPLAY, LIVE}:
        raise ValueError(f"PPAP_CORPUS_MODEL must be {REPLAY} or {LIVE}")
    return value


def offline() -> bool:
    value = os.environ.get("PPAP_CORPUS_OFFLINE", "0").strip().lower()
    if value not in {"0", "1", "false", "true", ""}:
        raise ValueError("PPAP_CORPUS_OFFLINE must be 0 or 1")
    return value in {"1", "true"}


def _ocr_test_options() -> str:
    raw = os.environ.get("OCR_OPENAI_EXTRA_BODY_JSON", "").strip()
    return json.dumps(effective_ocr_extra_body(raw), sort_keys=True) if raw else ""


def configured_model_access() -> ModelAccess | None:
    """One explicit test grant, never a production ambient-credential fallback.

    Corpus visual regressions prefer their dedicated OCR provider. All model
    tools in that test use this same grant. The paired key/endpoint/model must
    come from one complete test configuration, not unrelated settings defaults.
    """
    for names in (
        ("OCR_OPENAI_API_KEY", "OCR_OPENAI_BASE_URL", "OCR_MODEL_NAME"),
        ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"),
        ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"),
    ):
        values = tuple(os.environ.get(name, "").strip() for name in names)
        if all(values):
            key, endpoint, model = values
            options = _ocr_test_options() if names[0] == "OCR_OPENAI_API_KEY" else ""
            return ModelAccess(SecretStr(key), provider="corpus-test", base_url=endpoint, model=model, ocr_extra_body_json=options)
        if values[0]:
            raise ValueError("Incomplete corpus test model configuration: " + ", ".join(names))
    return None


def replay_model_access() -> ModelAccess:
    # This inert grant lets recorded observations pass the same scope boundary.
    # Offline Recorder transports below reject every cache miss before sending.
    return ModelAccess(SecretStr("corpus-replay-only"), provider="corpus-replay", model="recorded-model", base_url="http://127.0.0.1:1/v1", ocr_extra_body_json=_ocr_test_options())


class OfflineReplayMiss(AssertionError):
    """A request without a recording cannot authorize an external model call."""


def _image_summary(value: str) -> str:
    """Readable dimensions only; they do not prove image content identity."""
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


def _request_sha256(request: Any) -> str:
    """Readable summaries are lossy; match the complete request instead."""
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


class Recorder:
    """New answers wait for the case verdict, so a failed case asks again next run."""

    def __init__(self, scope: str, recordings: Path = REPLAY_DIR, output: Path | None = None, *, offline: bool = False) -> None:
        self.offline = offline
        self.misses: list[str] = []
        relative = f"{scope}.json"
        self.path = recordings / relative
        self.output = (output or Path(os.environ.get("PPAP_CORPUS_REPLAY_OUT") or recordings)) / relative
        self.entries: list[dict[str, Any]] = []
        for path in (self.path, self.output):
            if path.is_file():
                self.entries.extend(json.loads(path.read_text(encoding="utf-8")))
        self.pending: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def require_live(self, kind: str) -> None:
        if self.offline:
            self.misses.append(kind)
            raise OfflineReplayMiss(f"No {kind} recording for {self.path.name}; external model calls are disabled")

    def lookup(self, kind: str, request: Any) -> dict[str, Any] | None:
        digest = _request_sha256(request)
        with self.lock:
            # An old complete text request still proves its own identity.
            # A size-only image summary cannot match current image bytes.
            return next((entry for entry in (*self.entries, *self.pending) if entry["kind"] == kind and entry.get("request_sha256", _request_sha256(entry["request"])) == digest), None)

    def add(self, kind: str, request: Any, answer: Any, model: Any = None) -> None:
        shown = json.loads(json.dumps(shown_to_model(request), ensure_ascii=False, default=str))
        with self.lock:
            self.pending.append({"kind": kind, "model": model, "request": shown, "request_sha256": _request_sha256(request), "answer": answer})

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
        observation = ocr_client.current_ocr_observation()
        if observation is not None:
            request["observation"] = observation
        # Existing empty-policy recordings remain readable. An explicit policy
        # cannot consume an answer recorded under different request parameters.
        if kwargs.get("extra_body"):
            request["extra_body"] = kwargs["extra_body"]
        entry = recorder.lookup("ocr", request)
        if entry is None:
            recorder.require_live("ocr")
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
            recorder.require_live("text")
            answer = real_text(self, messages, *args, **kwargs)
            text = answer.text if isinstance(answer, TextCompletion) else answer
            recorder.add("text", request, text, getattr(self.settings, "MODEL_NAME", None))
            return text
        return entry["answer"]

    def paddle_request(endpoint: str, payload: dict, headers: dict, timeout: float) -> object:
        entry = recorder.lookup("paddle", payload)
        if entry is None:
            recorder.require_live("paddle")
            answer = real_paddle(endpoint, payload, headers, timeout)
            recorder.add("paddle", payload, answer)
            return answer
        return entry["answer"]

    monkeypatch.setattr(ocr_client, "_request_response", ocr_request)
    monkeypatch.setattr(AIClient, "response_text", text_request)
    monkeypatch.setattr(paddle_client, "_post_page_sync", paddle_request)


__all__ = ["LIVE", "REPLAY", "REPLAY_DIR", "OfflineReplayMiss", "Recorder", "configured_model_access", "install", "mode", "offline", "replay_model_access", "shown_to_model"]
