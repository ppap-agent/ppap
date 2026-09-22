"""Worker-side UDS client. Does not open a Docker socket or accept host commands."""
from __future__ import annotations

import json
import socket
from pathlib import Path

from ppap_deep.protocol import ErrorEnvelope, ExecuteRequest, ExecuteResponse, RequestEnvelope, ResultEnvelope

MAX_FRAME = 32 * 1024 * 1024
DEEP_REQUEST_TIMEOUT = 1000.0


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

    def execute(self, spec: ExecuteRequest) -> ExecuteResponse:
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
                raw = read_frame(sock)
        except DeepError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise DeepUnavailable() from exc
        if raw.get("op") == "error":
            raise DeepError(ErrorEnvelope.model_validate(raw).code)
        return ResultEnvelope.model_validate(raw).result
