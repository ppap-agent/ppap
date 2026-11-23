"""Model call records (#360): what one call was asked and answered, in the recording format offline replay reads.

A record is written only where the owner of an execution installs a recorder; the audit guest hands its records to
the local model gateway, which stores them by round. A record never holds a key, a credential header or a signed
address: its request is the call's own parameters, and images are kept apart by content digest. Writing a record
never changes the call; a failed write is reported to the recorder's owner, who keeps it as a diagnostic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

FORMAT = "ppap-model-call/1"
# Names a direct call to the local gateway, so it is recorded once, as the call, and not again as a wire exchange.
CALL_HEADER = "X-PPAP-Model-Call"
_BINARY = re.compile(r"^[A-Za-z0-9+/=\s]{4096,}$")


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


def request_sha256(request: Any) -> str:
    """Readable summaries are lossy; match the complete request instead."""
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def recording(kind: str, request: Any, answer: Any, model: Any = None) -> dict[str, Any]:
    """One recorded answer: what the model was shown (readable), the complete request identity and the answer."""
    shown = json.loads(json.dumps(shown_to_model(request), ensure_ascii=False, default=str))
    return {"kind": kind, "model": model, "request": shown, "request_sha256": request_sha256(request), "answer": answer}


def text_request(task: str, *, instructions: Any = None, prompt: Any = None, schema: Any = None, max_tokens: Any = None) -> dict[str, Any]:
    """The recorded identity of one direct text call: its task and the parameters it was given (None when omitted)."""
    return {"task": task, "instructions": instructions, "prompt": prompt, "schema": schema, "max_tokens": max_tokens}


def ocr_request(images: Sequence[bytes], prompt: str, response_schema: dict, max_output_tokens: int) -> dict[str, Any]:
    """The recorded identity of one vision call: what the model is shown, asked to return and sent with."""
    from documents.model_access import current_model_access
    from documents.ocr.observation import current_ocr_observation

    shown = [{"type": "input_text", "text": prompt}, *({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"} for image in images)]
    request: dict[str, Any] = {"input": [{"role": "user", "content": shown}], "text": {"format": {"type": "json_schema", **response_schema}}, "max_output_tokens": max_output_tokens}
    observation = current_ocr_observation()
    if observation is not None:
        request["observation"] = observation
    # An answer recorded under another reasoning effort cannot answer this request. The key keeps
    # the name existing recordings were made with.
    effort = current_model_access("vision").reasoning_effort
    if effort:
        request["extra_body"] = {"reasoning": {"effort": effort}}
    return request


def image_sha256(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()


class CallRecorder(Protocol):
    """Where an execution keeps its records. ``write`` may fail; ``failed`` is then told why, as a diagnostic."""

    def write(self, record: dict[str, Any], images: Mapping[str, bytes]) -> None: ...

    def failed(self, record: dict[str, Any], code: str) -> None: ...


_RECORDER: ContextVar[CallRecorder | None] = ContextVar("model_call_recorder", default=None)
_SCOPE: ContextVar[Mapping[str, str]] = ContextVar("model_call_scope", default={})


def current_recorder() -> CallRecorder | None:
    return _RECORDER.get()


def current_scope() -> dict[str, str]:
    return dict(_SCOPE.get())


@contextmanager
def model_call_recorder(recorder: CallRecorder | None) -> Iterator[None]:
    """Record every direct model call made in this scope (and in threads that copy its context)."""
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


@contextmanager
def model_call_scope(**labels: str) -> Iterator[None]:
    """Name what the calls in this scope serve, for example the target file and the clause under check."""
    token = _SCOPE.set({**_SCOPE.get(), **labels})
    try:
        yield
    finally:
        _SCOPE.reset(token)
