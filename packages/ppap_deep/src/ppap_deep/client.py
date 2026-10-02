"""Worker-side UDS client. Does not open a Docker socket or accept host commands."""

from __future__ import annotations

import json
import select
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ppap_deep.limits import DEEP_TOTAL_SECONDS
from ppap_deep.protocol import ErrorEnvelope, ExecuteRequest, ExecuteResponse, ProgressEnvelope, RequestEnvelope, ResultEnvelope

MAX_FRAME = 32 * 1024 * 1024
DEEP_REQUEST_TIMEOUT = DEEP_TOTAL_SECONDS + 30.0


class DeepError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = str(code or "deep_execution_failed")


class DeepUnavailable(DeepError):
    def __init__(self, code: str = "deep_runtime_unavailable"):
        super().__init__(code)


def write_frame(sock: socket.socket, payload: dict) -> None:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_FRAME:
        raise DeepError("deep_payload_too_large")
    sock.sendall(len(data).to_bytes(4, "big") + data)


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = sock.recv(size - len(chunks))
        if not piece:
            raise DeepUnavailable("deep_runtime_unavailable")
        chunks.extend(piece)
    return bytes(chunks)


def read_frame(sock: socket.socket) -> dict:
    header = read_exact(sock, 4)
    length = int.from_bytes(header, "big")
    if length <= 0 or length > MAX_FRAME:
        raise DeepError("deep_payload_too_large")
    return json.loads(read_exact(sock, length))


class DeepClient:
    def __init__(self, socket_path: str, timeout: float = DEEP_REQUEST_TIMEOUT):
        self.socket_path = str(socket_path or "").strip()
        self.timeout = timeout

    def execute(self, spec: ExecuteRequest, *, cancelled: threading.Event | None = None, on_progress: Callable[[dict], None] | None = None) -> ExecuteResponse:
        if not self.socket_path:
            raise DeepUnavailable()
        path = Path(self.socket_path)
        if not path.is_socket():
            raise DeepUnavailable()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                write_frame(sock, RequestEnvelope(op="execute", spec=spec).model_dump())
                deadline = time.monotonic() + self.timeout
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise DeepError("deep_lease_lost")
                    if time.monotonic() >= deadline:
                        raise DeepError("deep_timeout")
                    readable, _, _ = select.select([sock], [], [], min(0.25, max(0, deadline - time.monotonic())))
                    if not readable:
                        continue
                    sock.settimeout(min(10, max(0.01, deadline - time.monotonic())))
                    raw = read_frame(sock)
                    if raw.get("op") != "progress":
                        break
                    progress = ProgressEnvelope.model_validate(raw).progress
                    if on_progress:
                        on_progress(progress.model_dump(exclude_none=True))
        except DeepError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise DeepUnavailable() from exc
        if raw.get("op") == "error":
            raise DeepError(ErrorEnvelope.model_validate(raw).code)
        return ResultEnvelope.model_validate(raw).result
