"""Worker-side UDS client. Does not open a Docker socket or accept host commands."""

from __future__ import annotations

import json
import select
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent.execution.budget import DEEP_TOTAL_SECONDS
from agent.protocol import ErrorEnvelope, ExecuteRequest, ExecuteResponse, ProgressEnvelope, RequestEnvelope, ResultEnvelope

MAX_FRAME = 32 * 1024 * 1024
DEEP_REQUEST_TIMEOUT = DEEP_TOTAL_SECONDS + 30.0


class DeepError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = str(code or "deep_execution_failed")


class DeepUnavailable(DeepError):
    def __init__(self, code: str = "deep_runtime_unavailable"):
        super().__init__(code)


def write_frame(sock: socket.socket, payload: dict, *, deadline: float | None = None, cancelled: threading.Event | None = None) -> None:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    _check_active(deadline, cancelled)
    if len(data) > MAX_FRAME:
        raise DeepError("deep_payload_too_large")
    frame = memoryview(len(data).to_bytes(4, "big") + data)
    if deadline is None and cancelled is None:
        sock.sendall(frame)
        return
    timeout = sock.gettimeout()
    try:
        sock.setblocking(False)
        offset = 0
        while offset < len(frame):
            _check_active(deadline, cancelled)
            wait = min(0.25, max(0, deadline - time.monotonic())) if deadline is not None else 0.25
            _, writable, _ = select.select([], [sock], [], wait)
            if not writable:
                continue
            try:
                sent = sock.send(frame[offset:])
            except BlockingIOError:
                continue
            _check_active(deadline, cancelled)
            if not sent:
                raise DeepUnavailable()
            offset += sent
    finally:
        sock.settimeout(timeout)


def _check_active(deadline: float | None, cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise DeepError("deep_lease_lost")
    if deadline is not None and time.monotonic() >= deadline:
        raise DeepError("deep_timeout")


def read_exact(sock: socket.socket, size: int, *, deadline: float | None = None, cancelled: threading.Event | None = None) -> bytes:
    chunks = bytearray()
    idle_timeout = sock.gettimeout()
    idle_deadline = time.monotonic() + idle_timeout if idle_timeout else None
    while len(chunks) < size:
        if deadline is not None or cancelled is not None:
            _check_active(deadline, cancelled)
            wait = min(0.25, max(0, deadline - time.monotonic())) if deadline is not None else 0.25
            if idle_deadline is not None:
                remaining_idle = idle_deadline - time.monotonic()
                if remaining_idle <= 0:
                    raise TimeoutError("timed out")
                wait = min(wait, remaining_idle)
            readable, _, _ = select.select([sock], [], [], wait)
            if not readable:
                continue
        piece = sock.recv(size - len(chunks))
        _check_active(deadline, cancelled)
        if not piece:
            raise DeepUnavailable("deep_runtime_unavailable")
        chunks.extend(piece)
        if idle_timeout:
            idle_deadline = time.monotonic() + idle_timeout
    return bytes(chunks)


def read_frame(sock: socket.socket, *, deadline: float | None = None, cancelled: threading.Event | None = None) -> dict:
    header = read_exact(sock, 4, deadline=deadline, cancelled=cancelled)
    length = int.from_bytes(header, "big")
    if length <= 0 or length > MAX_FRAME:
        raise DeepError("deep_payload_too_large")
    raw = json.loads(read_exact(sock, length, deadline=deadline, cancelled=cancelled))
    _check_active(deadline, cancelled)
    return raw


class DeepClient:
    def __init__(self, socket_path: str, timeout: float = DEEP_REQUEST_TIMEOUT):
        self.socket_path = str(socket_path or "").strip()
        self.timeout = timeout

    def execute(self, spec: ExecuteRequest, *, cancelled: threading.Event | None = None, on_progress: Callable[[dict], None] | None = None) -> ExecuteResponse:
        deadline = time.monotonic() + self.timeout
        _check_active(deadline, cancelled)
        if not self.socket_path:
            raise DeepUnavailable()
        path = Path(self.socket_path)
        if not path.is_socket():
            raise DeepUnavailable()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(max(0, deadline - time.monotonic()))
                sock.connect(self.socket_path)
                write_frame(sock, RequestEnvelope(op="execute", spec=spec).model_dump(), deadline=deadline, cancelled=cancelled)
                while True:
                    raw = read_frame(sock, deadline=deadline, cancelled=cancelled)
                    if raw.get("op") != "progress":
                        break
                    progress = ProgressEnvelope.model_validate(raw).progress
                    _check_active(deadline, cancelled)
                    if on_progress:
                        on_progress(progress.model_dump(exclude_none=True))
        except DeepError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            _check_active(deadline, cancelled)
            raise DeepUnavailable() from exc
        _check_active(deadline, cancelled)
        if raw.get("op") == "error":
            code = ErrorEnvelope.model_validate(raw).code
            _check_active(deadline, cancelled)
            raise DeepError(code)
        result = ResultEnvelope.model_validate(raw).result
        _check_active(deadline, cancelled)
        return result
