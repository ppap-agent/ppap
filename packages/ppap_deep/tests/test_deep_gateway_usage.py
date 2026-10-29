"""Provider receipts through the real loopback gateway, not audit verdicts."""

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
from ppap_deep.isolation.model_gateway import grant_request, local_gateway


@pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete", "response.failed"])
@pytest.mark.parametrize(
    ("receipt", "confirmed"),
    [
        pytest.param({"input_tokens": 13, "output_tokens": 7}, True, id="known"),
        pytest.param(None, False, id="missing"),
        pytest.param({"input_tokens": 13}, False, id="missing-output"),
        pytest.param({"input_tokens": True, "output_tokens": 7}, False, id="boolean"),
        pytest.param({"input_tokens": 13, "output_tokens": -1}, False, id="negative"),
    ],
)
def test_terminal_stream_retains_confirmed_usage_without_replaying_inference(terminal: str, receipt, confirmed: bool) -> None:
    event = ("data: " + json.dumps({"type": terminal, "response": {"status": terminal.removeprefix("response."), "usage": receipt, "output": "private output"}}) + "\n\n").encode()
    provider_body = BytesIO(event)
    with tempfile.TemporaryDirectory(prefix="pd-u-", dir="/tmp") as temporary, patch("ppap_deep.isolation.model_gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.return_value = SimpleNamespace(status=200, read1=provider_body.read1)
        with local_gateway(Path(temporary) / "g.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key") as gateway:
            execution_id = uuid4()
            token = grant_request(str(gateway.socket_path), "issue", execution_id)["token"]
            body = json.dumps({"model": "test-model", "input": "private input", "stream": True})

            def post() -> tuple[int, bytes, bool]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.http.server_address[1], timeout=2)
                try:
                    connection.request("POST", "/v1/responses", body=body, headers={"Authorization": "Bearer " + token})
                    response = connection.getresponse()
                    try:
                        return response.status, response.read(), True
                    except http.client.IncompleteRead as error:
                        return response.status, error.partial, False
                finally:
                    connection.close()

            received = post()
            usage = grant_request(str(gateway.socket_path), "usage", execution_id)
            if confirmed:
                assert usage == {"completed_requests": 1, "input_tokens": 13, "output_tokens": 7, "uncertain": False, "in_flight": False, "budget_exhausted": False}
                assert received == (200, event, True)
            else:
                assert {key: value for key, value in usage.items() if key != "transport_failure"} == {
                    "completed_requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "uncertain": True,
                    "in_flight": False,
                    "budget_exhausted": False,
                }
                failure = usage["transport_failure"]
                assert {key: failure[key] for key in ("phase", "error_type", "response_bytes", "last_event", "provider_status")} == {
                    "phase": "provider_event",
                    "error_type": "ValueError",
                    "response_bytes": len(event),
                    "last_event": terminal,
                    "provider_status": 200,
                }
                assert received == (200, b"", False)
            # A terminal failure is still the provider's original failure. It
            # does not authorize resending an already consumed request.
            assert post()[0] == (409 if confirmed else 403)
            assert upstream.return_value.request.call_count == 1
            assert grant_request(str(gateway.socket_path), "usage", execution_id) == usage
            for private in (token, "test-provider-key", "private input", "private output"):
                assert private not in json.dumps(usage)
