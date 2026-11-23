"""Answer model requests from readable recordings; a new request is asked once and recorded when its case passes.

Each test scope has one JSON file listing what the model was shown and what it answered. A request no recording of
its own scope answers takes the answer another scope recorded for the identical request, so a sample read by several
tests is asked once. The format belongs to ``documents.model_calls``, so a production call record (#360) replays here.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from documents.model_access import ModelAccess, ModelPurpose, ModelResolver, platform_model_access
from documents.model_calls import ocr_request, recording, request_sha256, shown_to_model, text_request
from pydantic import SecretStr

REPLAY_DIR = Path(__file__).resolve().parents[5] / "assets/corpus/replay"
REPLAY = "replay"
LIVE = "live"


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


def configured_model_access() -> ModelResolver | None:
    """Live corpus calls use the platform models exactly as production does, one per use.

    Text reads PPAP_MODEL_TEXT_*, vision PPAP_MODEL_VISION_*,
    coding PPAP_MODEL_CODING_* or the text model. Without a text or vision key there is no
    live model and recordings answer every call.
    """
    if not (os.environ.get("PPAP_MODEL_TEXT_API_KEY", "").strip() or os.environ.get("PPAP_MODEL_VISION_API_KEY", "").strip()):
        return None
    return platform_model_access


def replay_model_access() -> ModelAccess:
    # This inert grant lets recorded observations pass the same scope boundary.
    # Offline Recorder transports below reject every cache miss before sending.
    effort = os.environ.get("PPAP_MODEL_VISION_REASONING_EFFORT", "").strip()
    return ModelAccess(SecretStr("corpus-replay-only"), provider="corpus-replay", model="recorded-model", base_url="http://127.0.0.1:1/v1", reasoning_effort=effort)


class OfflineReplayMiss(AssertionError):
    """A request without a recording cannot authorize an external model call."""


_SHARED: dict[Path, dict[tuple[str, str], dict[str, Any]]] = {}
_SHARED_LOCK = threading.Lock()


def _shared(recordings: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every recording under ``recordings`` by (kind, request digest); the first file in path order wins."""
    with _SHARED_LOCK:
        if recordings not in _SHARED:
            index: dict[tuple[str, str], dict[str, Any]] = {}
            for path in sorted(recordings.rglob("*.json")):
                try:
                    entries = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for entry in entries if isinstance(entries, list) else ():
                    if isinstance(entry, dict) and entry.get("request_sha256"):
                        index.setdefault((str(entry.get("kind")), str(entry["request_sha256"])), entry)
            _SHARED[recordings] = index
        return _SHARED[recordings]


class Recorder:
    """New answers wait for the case verdict, so a failed case asks again next run."""

    def __init__(self, scope: str, recordings: Path = REPLAY_DIR, output: Path | None = None, *, offline: bool = False) -> None:
        self.offline = offline
        self.misses: list[str] = []
        relative = f"{scope}.json"
        self.path = recordings / relative
        self.output = (output or Path(os.environ.get("PPAP_CORPUS_REPLAY_OUT") or recordings)) / relative
        self.recordings = recordings
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
        digest = request_sha256(request)
        with self.lock:
            # Only the complete request identity matches; a readable summary never does.
            own = next((entry for entry in (*self.entries, *self.pending) if entry["kind"] == kind and entry.get("request_sha256") == digest), None)
        return own if own is not None else _shared(self.recordings).get((kind, digest))

    def add(self, kind: str, request: Any, answer: Any, model: Any = None) -> None:
        entry = recording(kind, request, answer, model)
        with self.lock:
            self.pending.append(entry)

    def finish(self, *, keep: bool) -> None:
        with self.lock:
            pending, self.pending = self.pending, []
        if not keep or not pending:
            return
        self.entries.extend(pending)
        shared = _shared(self.recordings)
        with _SHARED_LOCK:
            for entry in pending:
                shared.setdefault((entry["kind"], entry["request_sha256"]), entry)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        temporary.replace(self.output)


def install(monkeypatch: Any, recorder: Recorder) -> None:
    """Replace the model calls below every product decision."""
    import documents.ai
    from audit.engine.checkers import ocr_visual_facts

    real_vision = ocr_visual_facts.read_visual_facts
    real_direct = documents.ai.run_json_async

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
        request = text_request(task, **{name: kwargs.get(name) for name in ("instructions", "prompt", "schema", "max_tokens")})
        entry = recorder.lookup("text", request)
        if entry is None:
            recorder.require_live("text")
            answer = await real_direct(task, **kwargs)
            recorder.add("text", request, json.dumps(answer.output, ensure_ascii=False), answer.model)
            return answer
        return documents.ai.AIResult(json.loads(entry["answer"]), model=entry.get("model") or "")

    monkeypatch.setattr(ocr_visual_facts, "read_visual_facts", vision_request)
    monkeypatch.setattr(documents.ai, "run_json_async", direct_request)


def current_model_access_name(purpose: ModelPurpose) -> str | None:
    from documents.model_access import ModelUnavailable, current_model_access

    try:
        return current_model_access(purpose).model
    except ModelUnavailable:
        return None


__all__ = [
    "LIVE",
    "REPLAY",
    "REPLAY_DIR",
    "OfflineReplayMiss",
    "Recorder",
    "configured_model_access",
    "install",
    "mode",
    "ocr_request",
    "offline",
    "recording",
    "replay_model_access",
    "shown_to_model",
]
