"""Model egress through the real loopback gateway: a request limit, observed usage, no budgets."""

from __future__ import annotations

import http.client
import json
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.execution.gateway import grant_request, local_gateway
from agent.execution.guest_model import ModelGrant


def _post(port: int, token: str, body: str) -> tuple[int, bytes, bool]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("POST", "/v1/responses", body=body, headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        try:
            return response.status, response.read(), True
        except http.client.IncompleteRead as error:
            return response.status, error.partial, False
    finally:
        connection.close()


def test_request_limit_rejects_the_next_request_before_the_provider() -> None:
    event = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
    with tempfile.TemporaryDirectory(prefix="pd-limit-", dir="/tmp") as temporary, patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.side_effect = lambda: SimpleNamespace(status=200, read1=BytesIO(event).read1)
        with local_gateway(Path(temporary) / "g.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key", requests=2) as gateway:
            execution_id = uuid4()
            grant = ModelGrant.model_validate(grant_request(str(gateway.socket_path), "issue", execution_id))
            body = json.dumps({"model": "test-model", "input": "audit", "stream": True})
            port = gateway.http.server_address[1]
            assert [_post(port, grant.token, body)[0] for _ in range(2)] == [200, 200]
            assert _post(port, grant.token, body)[0] == 429
            assert upstream.return_value.request.call_count == 2
            assert grant_request(str(gateway.socket_path), "usage", execution_id) == {
                "completed_requests": 2,
                "input_tokens": 2,
                "output_tokens": 2,
                "usage_known": True,
                "in_flight": False,
                "budget_exhausted": True,
            }


@pytest.mark.parametrize(
    ("terminal", "receipt", "known"),
    [
        pytest.param("response.completed", {"input_tokens": 13, "output_tokens": 7}, True, id="completed-known"),
        pytest.param("response.incomplete", {"input_tokens": 13, "output_tokens": 7}, True, id="incomplete-known"),
        pytest.param("response.failed", {"input_tokens": 13, "output_tokens": 7}, True, id="failed-known"),
        pytest.param("response.completed", None, False, id="completed-missing"),
        pytest.param("response.incomplete", None, False, id="incomplete-missing"),
        pytest.param("response.failed", None, False, id="failed-missing"),
        pytest.param("response.completed", {"input_tokens": 13}, False, id="completed-invalid-counts"),
    ],
)
def test_terminal_stream_is_forwarded_whether_or_not_usage_is_reported(terminal: str, receipt, known: bool) -> None:
    event = ("data: " + json.dumps({"type": terminal, "response": {"status": terminal.removeprefix("response."), "usage": receipt, "output": "private output"}}) + "\n\n").encode()
    with tempfile.TemporaryDirectory(prefix="pd-u-", dir="/tmp") as temporary, patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.side_effect = lambda: SimpleNamespace(status=200, read1=BytesIO(event).read1)
        with local_gateway(Path(temporary) / "g.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key") as gateway:
            execution_id = uuid4()
            token = grant_request(str(gateway.socket_path), "issue", execution_id)["token"]
            body = json.dumps({"model": "test-model", "input": "private input", "stream": True})
            # The agent receives the provider's own bytes and judges them itself.
            assert _post(gateway.http.server_address[1], token, body) == (200, event, True)
            usage = grant_request(str(gateway.socket_path), "usage", execution_id)
            assert usage == {
                "completed_requests": 1,
                "input_tokens": 13 if known else 0,
                "output_tokens": 7 if known else 0,
                "usage_known": known,
                "in_flight": False,
                "budget_exhausted": False,
            }
            # A missing receipt does not close the grant: the agent may continue.
            assert _post(gateway.http.server_address[1], token, body)[0] == 200
            assert upstream.return_value.request.call_count == 2
            for private in (token, "test-provider-key", "private input", "private output"):
                assert private not in json.dumps(grant_request(str(gateway.socket_path), "usage", execution_id))
