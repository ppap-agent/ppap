"""Controller lifecycle through its public request and UDS boundaries."""

from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from agent.client import DeepClient, DeepError, read_frame
from agent.execution.budget import MAX_INPUT_FILES
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import Controller, ExecutionContext, UnconfiguredRuntime, UnixControllerServer
from agent.source import object_file_source
from audit.audits import DEEP_PROTOCOL_REVISION
from audit.storage_errors import ObjectTooLarge


def controller_request() -> tuple[ExecuteRequest, dict[str, bytes]]:
    blobs = {"target-version": b"frozen target", "reference-version": b"frozen reference"}
    spec = ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        executor="ppap-deep",
        executor_revision="a" * 64,
        tenant_id="tenant-one",
        round_id="round-one",
        job_id="job-one",
        files=[
            FrozenFile(
                file_version_id=version,
                filename=version + ".xlsx",
                role="target" if version == "target-version" else "reference",
                sha256=hashlib.sha256(data).hexdigest(),
                **({"rule_version_id": "rule-version", "rule_body": {"rules": [{"id": "R1", "rule_to_check": "Identify the process."}]}} if version == "target-version" else {}),
            )
            for version, data in blobs.items()
        ],
    )
    return spec, blobs


def test_excessive_input_count_is_rejected_before_reading_objects():
    spec, _blobs = controller_request()
    spec.files = [spec.files[0].model_copy(update={"file_version_id": str(index)}) for index in range(MAX_INPUT_FILES + 1)]
    reads = []
    controller = Controller(UnconfiguredRuntime(), lambda version, limit: reads.append((version, limit)) or b"", executor_revision="a" * 64)
    response = controller.handle_raw({"op": "execute", "spec": spec.model_dump()})
    assert response["code"] == "deep_budget_exhausted"
    assert reads == []


def test_frozen_object_source_maps_size_rejection_to_execution_budget():
    body = BytesIO(b"oversize")

    class Connection:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return ("frozen-object", "ready")

    class Database:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    class Objects:
        def get(self, key, *, max_bytes=None):
            try:
                raise ObjectTooLarge()
            finally:
                body.close()

    with pytest.raises(DeepError) as failure:
        object_file_source(object(), connect=lambda *_a, **_k: Database(), objects=Objects())("00000000-0000-4000-8000-000000000001", 3)
    assert failure.value.code == "deep_budget_exhausted"
    assert body.closed


@pytest.mark.parametrize("sizes,expected_reads,expected_code", [([6, 1, 1], [5], "deep_budget_exhausted"), ([3, 3, 1], [5, 2], "deep_budget_exhausted"), ([3, 2], [5, 2], "deep_runtime_unavailable")])
def test_input_bytes_use_remaining_budget_and_stop_before_runtime(sizes, expected_reads, expected_code):
    spec, _blobs = controller_request()
    blobs = {str(index): b"x" * size for index, size in enumerate(sizes)}
    spec.files = [spec.files[0].model_copy(update={"file_version_id": version, "sha256": hashlib.sha256(data).hexdigest()}) for version, data in blobs.items()]
    budgets = []

    def source(version, limit):
        budgets.append(limit)
        return blobs[version]

    controller = Controller(UnconfiguredRuntime(), source, executor_revision="a" * 64)
    with patch("agent.run.MAX_INPUT_TOTAL", 5):
        response = controller.handle_raw({"op": "execute", "spec": spec.model_dump()})
    assert response["code"] == expected_code
    assert budgets == expected_reads


@pytest.mark.parametrize("cancel_during", ["target-version", "reference-version"])
def test_cancellation_during_input_read_stops_further_reads_and_execution(cancel_during: str) -> None:
    spec, blobs = controller_request()
    original = spec.model_dump_json()
    progress, authorized, reads = [], [], []
    context = ExecutionContext(on_progress=progress.append)

    def source(version: str, _max_bytes: int) -> bytes:
        assert authorized == [original]
        reads.append(version)
        if version == cancel_during:
            context.cancelled.set()
        return blobs[version]

    controller = Controller(UnconfiguredRuntime(), source, executor_revision="a" * 64, authorize=lambda request: authorized.append(request.model_dump_json()))
    with pytest.raises(DeepError) as failure:
        controller.handle(spec, context)

    assert failure.value.code == "deep_cancelled"
    assert reads == (["target-version"] if cancel_during == "target-version" else ["target-version", "reference-version"])
    assert progress == [{"phase": "reading"}]
    assert spec.model_dump_json() == original


@pytest.mark.parametrize("fragment", ["header", "body"])
def test_request_frame_deadline_closes_slow_peers_before_dispatch(fragment: str) -> None:
    spec, blobs = controller_request()
    original = spec.model_dump_json()
    authorized, reads = [], []

    def source(version: str, _max_bytes: int) -> bytes:
        reads.append(version)
        return blobs[version]

    controller = Controller(UnconfiguredRuntime(), source, executor_revision="a" * 64, authorize=lambda request: authorized.append(request.model_dump_json()))
    body = json.dumps({"op": "execute", "spec": spec.model_dump(mode="json")}, separators=(",", ":")).encode()
    header = len(body).to_bytes(4, "big")
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="pd-in-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "c.sock")
        server = UnixControllerServer(path, controller, request_timeout=0.08)
        server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(1)
                peer.connect(path)
                peer.sendall(header[:1] if fragment == "header" else header + body[:1])

                def drip():
                    try:
                        if fragment == "header":
                            for byte in header[1:]:
                                if stop.wait(0.06):
                                    return
                                peer.sendall(bytes([byte]))
                            peer.sendall(body)
                        else:
                            width = (len(body) + 9) // 10
                            for offset in range(1, len(body), width):
                                if stop.wait(0.04):
                                    return
                                peer.sendall(body[offset : offset + width])
                    except OSError:
                        pass  # The deadline closes the peer while it is still sending.

                sender = threading.Thread(target=drip, daemon=True)
                sender.start()
                try:
                    started = time.monotonic()
                    try:
                        received = peer.recv(1)
                    except ConnectionResetError:
                        received = b""
                    assert received == b"", "an expired request reached dispatch"
                    assert time.monotonic() - started < 0.75
                    assert authorized == reads == []
                    assert spec.model_dump_json() == original
                finally:
                    stop.set()
                    sender.join(timeout=1)
                    assert not sender.is_alive()
            # Expiring an incomplete peer must not disable normal dispatch.
            with pytest.raises(DeepError) as unavailable:
                DeepClient(path, timeout=1).execute(spec)
            assert unavailable.value.code == "deep_runtime_unavailable"
            assert authorized == [original]
            assert reads == ["target-version", "reference-version"]
        finally:
            server.close()


@pytest.mark.parametrize("fragment", ["header", "body"])
def test_total_frame_deadline_preserves_the_socket_idle_timeout(fragment: str) -> None:
    body = b'{"op":"execute"}'
    header = len(body).to_bytes(4, "big")
    reader, writer = socket.socketpair()
    with reader, writer:
        reader.settimeout(0.04)
        writer.sendall(header[:1] if fragment == "header" else header + body[:1])
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            read_frame(reader, deadline=started + 0.35)
        assert time.monotonic() - started < 0.25


def test_close_cancels_a_connection_accepted_before_context_registration() -> None:
    spec, blobs = controller_request()
    authorized, reads = [], []

    def source(version: str, _max_bytes: int) -> bytes:
        reads.append(version)
        return blobs[version]

    controller = Controller(UnconfiguredRuntime(), source, executor_revision="a" * 64, authorize=lambda request: authorized.append(request.model_dump_json()))
    accepted, close_poked, release_accept = threading.Event(), threading.Event(), threading.Event()
    errors = []
    with tempfile.TemporaryDirectory(prefix="pd-close-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "c.sock")
        server = UnixControllerServer(path, controller, request_timeout=2)
        closer = threading.Thread(target=server.close, daemon=True)
        original_accept, original_connect = socket.socket.accept, socket.socket.connect

        def delayed_accept(listener):
            connection = original_accept(listener)
            accepted.set()
            if not release_accept.wait(2):
                errors.append("accepted peer was not released")
            return connection

        def observed_connect(peer, address):
            result = original_connect(peer, address)
            if address == path and threading.current_thread() is closer:
                close_poked.set()  # close has set its stop signal before this I/O.
            return result

        with patch.object(socket.socket, "accept", delayed_accept), patch.object(socket.socket, "connect", observed_connect):
            server.start()
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(1)
                    peer.connect(path)
                    assert accepted.wait(1)
                    body = json.dumps({"op": "execute", "spec": spec.model_dump(mode="json")}).encode()
                    peer.sendall(len(body).to_bytes(4, "big") + body)
                    closer.start()
                    assert close_poked.wait(1)
                    release_accept.set()
                    closer.join(timeout=1)
                    assert not closer.is_alive()
                    try:
                        received = peer.recv(1)
                    except ConnectionResetError:
                        received = b""
                    assert received == b"", "a connection registered after close reached dispatch"
                    assert authorized == reads == []
            finally:
                release_accept.set()
                if closer.ident is not None:
                    closer.join(timeout=2)
                server.close()
    assert not errors, errors
