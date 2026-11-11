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
from typing import Any

from documents.model_access import ModelAccess, ModelPurpose, effective_vision_extra_body
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
    return json.dumps(effective_vision_extra_body(raw), sort_keys=True) if raw else ""


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
            return ModelAccess(SecretStr(key), provider="corpus-test", base_url=endpoint, model=model, extra_body_json=options)
        if values[0]:
            raise ValueError("Incomplete corpus test model configuration: " + ", ".join(names))
    return None


def replay_model_access() -> ModelAccess:
    # This inert grant lets recorded observations pass the same scope boundary.
    # Offline Recorder transports below reject every cache miss before sending.
    return ModelAccess(SecretStr("corpus-replay-only"), provider="corpus-replay", model="recorded-model", base_url="http://127.0.0.1:1/v1", extra_body_json=_ocr_test_options())


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


def ocr_request(images: list[bytes], prompt: str, response_schema: dict, max_output_tokens: int) -> dict[str, Any]:
    """The recorded identity of one vision call: what the model is shown and asked to return.

    It keeps the shape recorded before the direct model layer, so existing answers stay valid.
    """
    from documents.model_access import current_model_access
    from documents.ocr.observation import current_ocr_observation

    shown = [{"type": "input_text", "text": prompt}, *({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"} for image in images)]
    request: dict[str, Any] = {"input": [{"role": "user", "content": shown}], "text": {"format": {"type": "json_schema", **response_schema}}, "max_output_tokens": max_output_tokens}
    observation = current_ocr_observation()
    if observation is not None:
        request["observation"] = observation
    # An explicit policy cannot consume an answer recorded under different request options.
    extra = current_model_access("vision").extra_body()
    if extra:
        request["extra_body"] = extra
    return request


def install(monkeypatch: Any, recorder: Recorder) -> None:
    """Replace the model and Paddle calls below every product decision."""
    import documents.ai
    from audit.engine.checkers import ocr_visual_facts
    from documents.ocr.paddle import client as paddle_client

    real_vision = ocr_visual_facts.read_visual_facts
    real_direct = documents.ai.run_json_async
    real_paddle = paddle_client._post_page_sync

    async def vision_request(images: list[bytes], prompt: str, *, response_schema: dict, max_output_tokens: int = 4096) -> dict:
        request = ocr_request(images, prompt, response_schema, max_output_tokens)
        entry = recorder.lookup("ocr", request)
        if entry is None:
            recorder.require_live("ocr")
            answer = await real_vision(images, prompt, response_schema=response_schema, max_output_tokens=max_output_tokens)
            recorder.add("ocr", request, json.dumps(answer, ensure_ascii=False), current_model_access_name("vision"))
            return answer
        return json.loads(entry["answer"])

    async def direct_request(task: str, **kwargs: Any) -> Any:
        if kwargs.get("purpose", "text") != "text":
            return await real_direct(task, **kwargs)
        request = {"task": task, **{name: kwargs.get(name) for name in ("instructions", "prompt", "schema", "max_tokens")}}
        entry = recorder.lookup("text", request)
        if entry is None:
            recorder.require_live("text")
            answer = await real_direct(task, **kwargs)
            recorder.add("text", request, json.dumps(answer.output, ensure_ascii=False), answer.model)
            return answer
        return documents.ai.AIResult(json.loads(entry["answer"]), model=entry.get("model") or "")

    def paddle_request(endpoint: str, payload: dict, headers: dict, timeout: float) -> object:
        entry = recorder.lookup("paddle", payload)
        if entry is None:
            recorder.require_live("paddle")
            answer = real_paddle(endpoint, payload, headers, timeout)
            recorder.add("paddle", payload, answer)
            return answer
        return entry["answer"]

    monkeypatch.setattr(ocr_visual_facts, "read_visual_facts", vision_request)
    monkeypatch.setattr(documents.ai, "run_json_async", direct_request)
    monkeypatch.setattr(paddle_client, "_post_page_sync", paddle_request)


def current_model_access_name(purpose: ModelPurpose) -> str | None:
    from documents.model_access import ModelUnavailable, current_model_access

    try:
        return current_model_access(purpose).model
    except ModelUnavailable:
        return None


__all__ = ["LIVE", "REPLAY", "REPLAY_DIR", "OfflineReplayMiss", "Recorder", "configured_model_access", "install", "mode", "ocr_request", "offline", "replay_model_access", "shown_to_model"]
