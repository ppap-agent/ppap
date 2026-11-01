"""Undelivered request recovery and bounded reads after request delivery.

Only undelivered endpoint cases precede DeepExecutionReplay's model effects.
A delivered timeout/cancellation does not prove the absence of a paid effect
and never authorizes replay of an execution with an unknown outcome.
Only external storage, authorization, Runtime and peer responses are controlled;
the UDS connection, framing and protocol are real. A sent request is not retried.
"""

from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from agent.client import DeepClient, DeepError, DeepUnavailable, read_frame
from agent.protocol import DeepCoverage, DeepFinding, ExecuteRequest, ExecuteResponse, FrozenFile
from agent.run import Controller, ScriptedRuntime, UnixControllerServer
from audit.audits import DEEP_PROTOCOL_REVISION


@pytest.mark.parametrize("endpoint", ["missing", "ordinary_file", "unlistened_socket"])
def test_undelivered_request_can_explicitly_recover_without_changing_frozen_identity(endpoint: str) -> None:
    blobs = {"target-version": b"frozen target", "reference-version": b"frozen reference"}
    spec = ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        executor="ppap-deep",
        executor_revision="a" * 64,
        tenant_id="tenant-one",
        round_id="round-one",
        job_id="job-one",
        attempt=2,
        files=[
            FrozenFile(
                file_version_id="target-version",
                file_id="target-file",
                role="target",
                filename="control-plan.xlsx",
                sha256=hashlib.sha256(blobs["target-version"]).hexdigest(),
                rule_version_id="published-rule-version",
                rule_source_file_version_id="rule-source-version",
                rule_body={"rules": [{"id": "CP-1", "rule_to_check": "The control plan must identify its process."}]},
            ),
            FrozenFile(
                file_version_id="reference-version",
                file_id="reference-file",
                role="reference",
                filename="process-flow.xlsx",
                sha256=hashlib.sha256(blobs["reference-version"]).hexdigest(),
            ),
        ],
    )
    original = spec.model_dump(mode="json")
    original_json = spec.model_dump_json()
    response = ExecuteResponse(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        round_id="round-one",
        files=[item.model_copy(deep=True) for item in spec.files],
        findings=[
            DeepFinding(
                item_key="target-version:rule:CP-1",
                classification="process",
                item="The control plan must identify its process.",
                priority="P1",
                status="NG",
                suggestions="Controlled external Runtime result, not model judgment.",
                locator={"file_version_id": "reference-version"},
            )
        ],
        coverage=DeepCoverage(checked=["control-plan.xlsx"], unchecked=["process-flow.xlsx (reference)"], locator_gap=False),
        usage={"input_tokens": 3, "output_tokens": 2},
    )
    authorized: list[dict] = []
    reads: list[str] = []
    executions: list[tuple[dict, dict[str, bytes]]] = []
    progress: list[dict] = []

    def authorize(request: ExecuteRequest) -> None:
        assert request.model_dump(mode="json") == original
        authorized.append(request.model_dump(mode="json"))

    def source(version_id: str, _max_bytes: int) -> bytes:
        assert authorized == [original]
        assert executions == []
        reads.append(version_id)
        return blobs[version_id]

    def execute(request: ExecuteRequest, files: dict[str, bytes]) -> ExecuteResponse:
        assert authorized == [original]
        assert reads == ["target-version", "reference-version"]
        executions.append((request.model_dump(mode="json"), dict(files)))
        return response.model_copy(deep=True)

    controller = Controller(ScriptedRuntime(execute), source, executor_revision="a" * 64, authorize=authorize)
    # A short /tmp path also fits macOS's sockaddr_un length limit.
    with tempfile.TemporaryDirectory(prefix="pd-", dir="/tmp") as temporary:
        path = Path(temporary) / "c.sock"
        if endpoint == "ordinary_file":
            path.write_bytes(b"not a socket")
            assert path.is_file()
        elif endpoint == "unlistened_socket":
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
                abandoned.bind(str(path))
            assert path.is_socket()
        else:
            assert not path.exists()

        client = DeepClient(str(path), timeout=2)
        with patch("subprocess.Popen", side_effect=AssertionError("UDS recovery must not start a local runtime")) as spawn:
            for _ in range(2):
                assert authorized == reads == executions == progress == []
                assert spec.model_dump_json() == original_json
                with pytest.raises(DeepUnavailable) as unavailable:
                    client.execute(spec, on_progress=progress.append)
                assert unavailable.value.code == "deep_runtime_unavailable"
                assert spec.model_dump_json() == original_json
                assert authorized == reads == executions == progress == []
                spawn.assert_not_called()

            server = UnixControllerServer(str(path), controller)
            try:
                server.start()
                result = client.execute(spec, on_progress=progress.append)
                assert result.model_dump(mode="json") == response.model_dump(mode="json")
                assert spec.model_dump_json() == original_json
                assert authorized == [original]
                assert reads == ["target-version", "reference-version"]
                assert executions == [(original, blobs)]
                assert progress == [{"phase": "reading"}]
                spawn.assert_not_called()
            finally:
                server.close()
            assert not path.exists()


def transport_request() -> ExecuteRequest:
    return ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        executor="ppap-deep",
        executor_revision="a" * 64,
        tenant_id="test-tenant",
        round_id="test-round",
        job_id="test-job",
        files=[
            FrozenFile(
                file_version_id="test-file-version",
                file_id="test-file",
                role="target",
                filename="test.xlsx",
                sha256=hashlib.sha256(b"frozen test file").hexdigest(),
                rule_version_id="test-rule-version",
                rule_body={"rules": [{"id": "R1", "rule_to_check": "A process must be identified."}]},
            )
        ],
    )


def transport_response(spec: ExecuteRequest) -> ExecuteResponse:
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=spec.files,
        findings=[DeepFinding(item_key="test-file-version:rule:R1", classification="test", item="A process must be identified.", priority="P1", status="NG")],
        coverage=DeepCoverage(checked=["test.xlsx"], unchecked=[], locator_gap=True),
    )


def response_frame(response: ExecuteResponse) -> tuple[bytes, bytes]:
    body = json.dumps({"op": "result", "result": response.model_dump(mode="json")}, separators=(",", ":")).encode()
    return len(body).to_bytes(4, "big"), body


@contextmanager
def socket_peer(respond, *, before_read=None):
    """A bounded external UDS peer, without a Controller or model execution."""
    received, errors, connections = [], [], []
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="pd-fr-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "c.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)
            listener.settimeout(2)

            def serve():
                try:
                    with listener.accept()[0] as connection:
                        connections.append(connection)
                        connection.settimeout(2)
                        if before_read is not None:
                            before_read(connection, stop)
                        received.append(read_frame(connection))
                        respond(connection, stop)
                except (OSError, DeepUnavailable):
                    # Deadline/cancellation closes the peer before it finishes sending.
                    pass
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                yield path, received
            finally:
                stop.set()
                for connection in connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                listener.close()
                thread.join(timeout=3)
                assert not thread.is_alive(), "bounded UDS test peer did not stop"
                assert not errors, errors


@pytest.mark.parametrize("fragment", ["header", "body"])
def test_one_deadline_applies_through_all_receives_of_a_fragmented_response(fragment: str) -> None:
    spec = transport_request()
    original = spec.model_dump(mode="json")
    header, body = response_frame(transport_response(spec))

    def respond(connection, stop):
        if fragment == "header":
            for byte in header:
                connection.sendall(bytes([byte]))
                if stop.wait(0.05):
                    return
            connection.sendall(body)
        else:
            connection.sendall(header + body[:1])
            width = (len(body) + 9) // 10
            for offset in range(1, len(body), width):
                if stop.wait(0.05):
                    return
                connection.sendall(body[offset : offset + width])

    with socket_peer(respond) as (path, received):
        started = time.monotonic()
        with pytest.raises(DeepError) as failure:
            DeepClient(path, timeout=0.12).execute(spec)
        assert failure.value.code == "deep_timeout"
        assert time.monotonic() - started < 0.8
        assert received == [{"op": "execute", "spec": original}]
        assert spec.model_dump(mode="json") == original


def test_cancellation_during_a_partial_frame_closes_the_request_without_a_late_result() -> None:
    spec = transport_request()
    original = spec.model_dump(mode="json")
    header, body = response_frame(transport_response(spec))
    partial = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def respond(connection, _stop):
        connection.sendall(header + body[:1])
        partial.set()
        assert release.wait(2), "partial response was not released"
        connection.sendall(body[1:])

    with socket_peer(respond) as (path, received), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(DeepClient(path, timeout=2).execute, spec, cancelled=cancelled)
        try:
            assert partial.wait(1)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.05)
            cancelled.set()
            with pytest.raises(DeepError) as failure:
                pending.result(timeout=0.75)
            assert failure.value.code == "deep_lease_lost"
            assert received == [{"op": "execute", "spec": original}]
            assert spec.model_dump(mode="json") == original
        finally:
            release.set()


def test_already_cancelled_execution_does_not_connect_or_send_a_request() -> None:
    cancelled = threading.Event()
    cancelled.set()
    with tempfile.TemporaryDirectory(prefix="pd-fr-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "c.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)
            with pytest.raises(DeepError) as failure:
                DeepClient(path, timeout=1).execute(transport_request(), cancelled=cancelled)
            assert failure.value.code == "deep_lease_lost"
            listener.setblocking(False)
            with pytest.raises(BlockingIOError):
                with listener.accept()[0]:
                    pytest.fail("cancelled execution connected to the Controller")


def test_deadline_includes_request_delivery_without_a_new_response_budget() -> None:
    spec = transport_request()
    # Exceed the UDS send buffer, not the protocol's existing frame limit. The
    # external peer controls when request delivery and response delivery finish.
    spec.files[0].rule_body = {"rules": [{"id": "R1", "rule_to_check": "x" * (1024 * 1024)}]}
    original = spec.model_dump(mode="json")
    header, body = response_frame(transport_response(spec))

    def before_read(_connection, stop):
        stop.wait(0.2)

    def respond(connection, stop):
        if not stop.wait(0.2):
            connection.sendall(header + body)

    with socket_peer(respond, before_read=before_read) as (path, received):
        started = time.monotonic()
        with pytest.raises(DeepError) as failure:
            DeepClient(path, timeout=0.3).execute(spec)
        assert failure.value.code == "deep_timeout"
        assert time.monotonic() - started < 1.2
        assert received == [{"op": "execute", "spec": original}]


def test_fragmented_response_within_deadline_keeps_frozen_identity_and_business_result() -> None:
    spec = transport_request()
    original = spec.model_dump(mode="json")
    response = transport_response(spec)
    header, body = response_frame(response)

    def respond(connection, stop):
        connection.sendall(header[:1])
        if stop.wait(0.01):
            return
        connection.sendall(header[1:] + body[:1])
        width = (len(body) + 3) // 4
        for offset in range(1, len(body), width):
            if stop.wait(0.01):
                return
            connection.sendall(body[offset : offset + width])

    with socket_peer(respond) as (path, received):
        result = DeepClient(path, timeout=1).execute(spec)
        assert result == response
        assert result.findings[0].status == "NG"
        assert received == [{"op": "execute", "spec": original}]
        assert spec.model_dump(mode="json") == original


def test_cancellation_while_request_delivery_is_backpressured_does_not_wait_for_the_peer() -> None:
    spec = transport_request()
    spec.files[0].rule_body = {"rules": [{"id": "R1", "rule_to_check": "x" * (1024 * 1024)}]}
    sending = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    header, body = response_frame(transport_response(spec))

    def before_read(connection, _stop):
        assert connection.recv(1, socket.MSG_PEEK), "no request prefix reached the peer"
        sending.set()
        assert release.wait(2), "backpressured request was not released"

    def respond(connection, _stop):
        connection.sendall(header + body)

    with socket_peer(respond, before_read=before_read) as (path, received), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(DeepClient(path, timeout=2).execute, spec, cancelled=cancelled)
        try:
            assert sending.wait(1)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.05)
            cancelled.set()
            with pytest.raises(DeepError) as failure:
                pending.result(timeout=0.75)
            assert failure.value.code == "deep_lease_lost"
            # Bytes were already sent. This is not an undelivered request and
            # no automatic replay is authorized by the failure.
            assert received == []
        finally:
            release.set()
