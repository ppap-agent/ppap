"""Controller process: owns runtime, never accepts host path/command/mount/image/env."""

from __future__ import annotations

import hashlib
import os
import select
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from audit.audits import DEEP_PROTOCOL_REVISION
from audit.storage_errors import ObjectMissing
from pydantic import ValidationError

from agent.client import DeepError, read_frame, write_frame
from agent.execution.activity import Activity
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.protocol import (
    ActivityEnvelope,
    DeepProgress,
    ErrorEnvelope,
    ExecuteRequest,
    ExecuteResponse,
    ProgressEnvelope,
    RequestEnvelope,
    ResultEnvelope,
)

FileSource = Callable[[str, int], bytes]


@dataclass
class ExecutionContext:
    cancelled: threading.Event = field(default_factory=threading.Event)
    on_progress: Callable[[dict], None] = lambda _state: None
    on_activity: Callable[[dict], None] = lambda _step: None
    unavailable_sources: frozenset[str] = frozenset()

    def raise_if_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise DeepError("deep_cancelled")

    def progress(self, state: dict) -> None:
        self.raise_if_cancelled()
        self.on_progress(DeepProgress.model_validate(state).model_dump(exclude_none=True))

    def activity(self, step: Activity) -> None:
        """A live step for viewers. It never cancels or fails the execution."""
        self.on_activity(step.model_dump(exclude_none=True))


class Runtime(Protocol):
    def execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse: ...


class Controller:
    def __init__(
        self,
        runtime: Runtime,
        file_source: FileSource,
        *,
        executor_revision: str = DEEP_PROTOCOL_REVISION,
        executor_revision_for_request: Callable[[ExecuteRequest], str] | None = None,
        protocol_revision: str = DEEP_PROTOCOL_REVISION,
        authorize: Callable[[ExecuteRequest], None] | None = None,
        readiness: Callable[[], bool] | None = None,
        configuration: dict[str, str] | None = None,
    ):
        self.runtime = runtime
        self.file_source = file_source
        self.executor_revision = str(executor_revision or "").strip() or DEEP_PROTOCOL_REVISION
        self.executor_revision_for_request = executor_revision_for_request
        self.protocol_revision = str(protocol_revision or "").strip() or DEEP_PROTOCOL_REVISION
        self.authorize = authorize
        self.readiness = readiness
        self.configuration = dict(configuration) if configuration is not None else None

    def handle(self, spec: ExecuteRequest, context: ExecutionContext | None = None) -> ExecuteResponse:
        context = context or ExecutionContext()
        if spec.protocol_revision != self.protocol_revision:
            raise DeepError("deep_protocol_mismatch")
        try:
            expected_revision = self.executor_revision_for_request(spec) if self.executor_revision_for_request is not None else self.executor_revision
        except (ValueError, KeyError, TypeError):
            raise DeepError("executor_revision_mismatch") from None
        if spec.executor_revision != expected_revision:
            raise DeepError("executor_revision_mismatch")
        if self.authorize:
            self.authorize(spec)
        if len(spec.files) > MAX_INPUT_FILES:
            raise DeepError("deep_budget_exhausted")
        context.progress({"phase": "reading"})
        blobs: dict[str, bytes] = {}
        unavailable: set[str] = set()
        context.unavailable_sources = frozenset()
        remaining = MAX_INPUT_TOTAL
        try:
            for item in spec.files:
                context.raise_if_cancelled()
                if not item.sha256:
                    raise DeepError("deep_integrity_mismatch")
                try:
                    data = self.file_source(item.file_version_id, remaining)
                except DeepError as exc:
                    if exc.code == "deep_input_missing":
                        unavailable.add(item.file_version_id)
                        continue
                    raise
                except (ObjectMissing, FileNotFoundError):
                    unavailable.add(item.file_version_id)
                    continue
                except Exception as exc:
                    raise DeepError("deep_input_missing") from exc
                context.raise_if_cancelled()
                if len(data) > remaining:
                    raise DeepError("deep_budget_exhausted")
                remaining -= len(data)
                digest = hashlib.sha256(data).hexdigest()
                if digest != item.sha256:
                    raise DeepError("deep_integrity_mismatch")
                blobs[item.file_version_id] = data
            context.raise_if_cancelled()
            context.unavailable_sources = frozenset(unavailable)
            result = self.runtime.execute(spec, blobs, context=context)
            context.raise_if_cancelled()
            return self._validate(spec, ExecuteResponse.model_validate(result.model_dump()))
        finally:
            blobs.clear()
            context.unavailable_sources = frozenset()

    def _validate(self, spec: ExecuteRequest, result: ExecuteResponse) -> ExecuteResponse:
        if result.round_id != spec.round_id:
            raise DeepError("deep_output_identity_mismatch")
        if result.protocol_revision != spec.protocol_revision or result.files != spec.files:
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
        expected = {
            f"{item.file_version_id}:rule:{clause.get('id') or index}" for item in spec.files if item.role == "target" for index, clause in enumerate((item.rule_body or {}).get("rules") or [], 1)
        }
        # Unsupported clauses remain explicit coverage, not fabricated ERR
        # findings. Require an exact, unique disposition for every clause.
        coverage = result.coverage.rule_items
        covered = {f"{row.get('file_version_id')}:rule:{row.get('rule_item_id')}" for row in coverage}
        if len(covered) != len(coverage) or covered != expected:
            raise DeepError("deep_output_invalid")
        unsupported = {f"{row.get('file_version_id')}:rule:{row.get('rule_item_id')}" for row in coverage if row.get("coverage") == "unsupported"}
        if seen != expected - unsupported:
            raise DeepError("deep_output_invalid")
        identity = result.coverage.checker_execution or {}
        if identity.get("input_digest") != spec.checker_snapshot_digest or identity.get("runtime_revision") != spec.executor_revision:
            raise DeepError("deep_output_identity_mismatch")
        return result

    def handle_raw(self, payload: dict, context: ExecutionContext | None = None) -> dict:
        if payload == {"op": "configuration"} and self.configuration is not None:
            return {"op": "configuration", "configuration": dict(self.configuration)}
        if payload == {"op": "ready"}:
            # Local observation only: never authorize, read files, or execute work.
            try:
                ready = self.readiness is not None and self.readiness() is True
            except Exception:
                ready = False
            return {"op": "ready", "ready": ready}
        try:
            envelope = RequestEnvelope.model_validate(payload)
            result = self.handle(envelope.spec, context)
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
        self._slots = threading.BoundedSemaphore(4)
        self._contexts: list[ExecutionContext] = []
        self._contexts_lock = threading.Lock()

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
        with self._contexts_lock:
            for context in self._contexts:
                context.cancelled.set()
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
                read_deadline = time.monotonic() + self.request_timeout
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            if not self._slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, read_deadline), daemon=True).start()

    def _serve(self, conn: socket.socket, read_deadline: float) -> None:
        context = ExecutionContext()
        finished = threading.Event()
        with self._contexts_lock:
            if self._stop.is_set():
                context.cancelled.set()
            self._contexts.append(context)
        try:
            with conn:
                conn.settimeout(10)
                payload = read_frame(conn, deadline=read_deadline, cancelled=context.cancelled)

                def watch_disconnect():
                    while not finished.wait(0.25):
                        try:
                            readable, _, _ = select.select([conn], [], [], 0)
                            if readable:
                                # The request is complete. EOF or further input cancels it.
                                conn.recv(1)
                                context.cancelled.set()
                                return
                        except OSError:
                            context.cancelled.set()
                            return

                def progress(state):
                    write_frame(conn, ProgressEnvelope(op="progress", progress=DeepProgress.model_validate(state)).model_dump())

                def activity(step):
                    write_frame(conn, ActivityEnvelope(op="activity", activity=Activity.model_validate(step)).model_dump())

                context.on_progress = progress
                context.on_activity = activity
                watcher = threading.Thread(target=watch_disconnect, daemon=True)
                watcher.start()
                try:
                    write_frame(conn, self.controller.handle_raw(payload, context))
                except Exception:
                    write_frame(conn, ErrorEnvelope(op="error", code="deep_execution_failed").model_dump())
                finally:
                    finished.set()
                    watcher.join(timeout=1)
        except (OSError, DeepError, ValueError):
            context.cancelled.set()
        finally:
            finished.set()
            with self._contexts_lock:
                self._contexts.remove(context)
            self._slots.release()
