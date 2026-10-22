"""Controller lifecycle through its public request and UDS boundaries."""

from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_deep.client import DeepClient, DeepError
from ppap_deep.controller import Controller, ExecutionContext, UnconfiguredRuntime, UnixControllerServer
from ppap_deep.protocol import ExecuteRequest, FrozenFile


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


@pytest.mark.parametrize("cancel_during", ["target-version", "reference-version"])
def test_cancellation_during_input_read_stops_further_reads_and_execution(cancel_during: str) -> None:
    spec, blobs = controller_request()
    original = spec.model_dump_json()
    progress, authorized, reads = [], [], []
    context = ExecutionContext(on_progress=progress.append)

    def source(version: str) -> bytes:
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

    def source(version: str) -> bytes:
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
