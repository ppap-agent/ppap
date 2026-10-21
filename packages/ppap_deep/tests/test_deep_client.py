"""Explicit recovery of requests not delivered to the Deep Controller.

These failures precede DeepExecutionReplay's MarkAttempt/StartModel effects.
They do not authorize replay of an execution with an unknown paid outcome.
Only external storage, authorization and Runtime behavior are controlled;
the UDS connection, framing, protocol and Controller validation are real.
"""

from __future__ import annotations

import hashlib
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_deep.client import DeepClient, DeepUnavailable
from ppap_deep.controller import Controller, ScriptedRuntime, UnixControllerServer
from ppap_deep.protocol import DeepCoverage, DeepFinding, ExecuteRequest, ExecuteResponse, FrozenFile


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

    def source(version_id: str) -> bytes:
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
