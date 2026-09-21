"""Controller process: owns runtime, never accepts host path/command/mount/image/env."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_deep.client import DeepError, DeepUnavailable, MAX_FRAME, read_frame, write_frame
from ppap_deep.protocol import (
    ErrorEnvelope,
    ExecuteRequest,
    ExecuteResponse,
    RequestEnvelope,
    ResultEnvelope,
)

FileSource = Callable[[str], bytes]


class Runtime(Protocol):
    def execute(self, spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        ...


class UnconfiguredRuntime:
    def execute(self, spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        raise DeepUnavailable()


class ScriptedRuntime:
    """Test-only injectable runtime. Not selected by Settings or RPC."""

    def __init__(self, handler: Callable[[ExecuteRequest, dict[str, bytes]], ExecuteResponse]):
        self._handler = handler

    def execute(self, spec: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        return self._handler(spec, files)


class Controller:
    def __init__(
        self,
        runtime: Runtime,
        file_source: FileSource,
        *,
        executor_revision: str = DEEP_PROTOCOL_REVISION,
        protocol_revision: str = DEEP_PROTOCOL_REVISION,
    ):
        self.runtime = runtime
        self.file_source = file_source
        self.executor_revision = str(executor_revision or "").strip() or DEEP_PROTOCOL_REVISION
        self.protocol_revision = str(protocol_revision or "").strip() or DEEP_PROTOCOL_REVISION

    def handle(self, spec: ExecuteRequest) -> ExecuteResponse:
        if spec.protocol_revision != self.protocol_revision:
            raise DeepError("deep_protocol_mismatch")
        if spec.executor != "ppap-deep":
            raise DeepError("deep_executor_mismatch")
        if spec.executor_revision != self.executor_revision:
            raise DeepError("executor_revision_mismatch")
        blobs: dict[str, bytes] = {}
        try:
            for item in spec.files:
                if not item.sha256:
                    raise DeepError("deep_integrity_mismatch")
                try:
                    data = self.file_source(item.file_version_id)
                except DeepError:
                    raise
                except Exception as exc:
                    raise DeepError("deep_input_missing") from exc
                digest = hashlib.sha256(data).hexdigest()
                if digest != item.sha256:
                    raise DeepError("deep_integrity_mismatch")
                blobs[item.file_version_id] = data
            result = self.runtime.execute(spec, blobs)
            return self._validate(spec, ExecuteResponse.model_validate(result.model_dump()))
        finally:
            blobs.clear()

    def _validate(self, spec: ExecuteRequest, result: ExecuteResponse) -> ExecuteResponse:
        if result.round_id != spec.round_id:
            raise DeepError("deep_output_identity_mismatch")
        if [row.file_version_id for row in result.files] != [row.file_version_id for row in spec.files]:
            raise DeepError("deep_output_identity_mismatch")
        known = {row.file_version_id for row in spec.files}
        seen: set[str] = set()
        for finding in result.findings:
            if finding.item_key in seen:
                raise DeepError("deep_output_invalid")
            seen.add(finding.item_key)
            locator = finding.locator or {}
            version_id = locator.get("file_version_id")
            if version_id and version_id not in known:
                raise DeepError("deep_output_identity_mismatch")
        return result

    def handle_raw(self, payload: dict) -> dict:
        try:
            envelope = RequestEnvelope.model_validate(payload)
            result = self.handle(envelope.spec)
            return ResultEnvelope(op="result", result=result).model_dump()
        except ValidationError:
            return ErrorEnvelope(op="error", code="deep_protocol_invalid").model_dump()
        except DeepError as exc:
            return ErrorEnvelope(op="error", code=exc.code).model_dump()


class UnixControllerServer:
    def __init__(self, path: str, controller: Controller, request_timeout: float = 180.0):
        self.path = path
        self.controller = controller
        self.request_timeout = request_timeout
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if Path(self.path).exists():
            os.unlink(self.path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        os.chmod(self.path, 0o660)
        sock.listen(8)
        sock.settimeout(0.2)
        self._sock = sock
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError("deep_controller_not_ready")

    def close(self) -> None:
        self._stop.set()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as poke:
                poke.settimeout(0.2)
                poke.connect(self.path)
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _run(self) -> None:
        self._ready.set()
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _unused = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            with conn:
                try:
                    conn.settimeout(self.request_timeout)
                    payload = read_frame(conn)
                    if len(json.dumps(payload).encode()) > MAX_FRAME:
                        write_frame(conn, ErrorEnvelope(op="error", code="deep_payload_too_large").model_dump())
                        continue
                    write_frame(conn, self.controller.handle_raw(payload))
                except Exception:
                    try:
                        write_frame(conn, ErrorEnvelope(op="error", code="deep_execution_failed").model_dump())
                    except Exception:
                        return
