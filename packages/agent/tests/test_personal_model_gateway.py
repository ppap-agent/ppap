"""Personal DeepSeek Responses/tool transport through the existing gateway."""

import http.client
import json
from contextlib import contextmanager
from io import BytesIO
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.execution.gateway import Gateway, Grants
from documents.model_access import ModelUnavailable
from openai import OpenAI


@contextmanager
def personal_gateway(resolve_key):
    # Exercise the real HTTP gateway; UDS framing has separate existing tests.
    grants = Grants("deepseek-flash")
    server = Gateway(("127.0.0.1", 0), grants, "https://api.deepseek.com", resolve_key)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("receipt", [None, {"input_tokens": 17, "output_tokens": 4, "total_tokens": 21}])
def test_existing_gateway_uses_current_user_secret_and_real_sdk_tool_shape(receipt):
    key = str(uuid4())
    provider = {
        "id": "response-controlled",
        "object": "response",
        "created_at": 0,
        "model": "deepseek-flash",
        "status": "completed",
        "output": [{"type": "function_call", "id": "call-controlled", "call_id": "call-1", "name": "read_source", "arguments": '{"page":1}', "status": "completed"}],
        "usage": receipt,
    }
    body = BytesIO(json.dumps(provider).encode())
    resolved = []

    def resolve_key():
        resolved.append(True)
        return key

    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.return_value.status = 200
        upstream.return_value.getresponse.return_value.read1 = body.read1
        with personal_gateway(resolve_key) as gateway:
            execution_id = uuid4()
            grant = {"token": gateway.grants.issue(execution_id), "base_url": f"http://127.0.0.1:{gateway.server_address[1]}/v1"}
            assert key not in json.dumps(grant)
            import httpx

            with OpenAI(api_key=grant["token"], base_url=grant["base_url"], max_retries=0, http_client=httpx.Client(trust_env=False)) as client:
                if receipt is None:
                    # EOF without valid usage is intentionally uncertain. A
                    # partial provider reply cannot authorize another request.
                    with pytest.raises(Exception):
                        client.responses.create(
                            model="deepseek-flash", input="Read page one.", tools=[{"type": "function", "name": "read_source", "parameters": {"type": "object"}}], reasoning={"effort": "high"}
                        )
                else:
                    result = client.responses.create(
                        model="deepseek-flash", input="Read page one.", tools=[{"type": "function", "name": "read_source", "parameters": {"type": "object"}}], reasoning={"effort": "high"}
                    )
                    assert result.output[0].type == "function_call"
                    assert result.output[0].name == "read_source"
            usage = gateway.grants.usage(execution_id)
            assert usage["uncertain"] is (receipt is None)
            assert usage["completed_requests"] == (0 if receipt is None else 1)
            assert key not in json.dumps(usage)
            arguments = upstream.return_value.request.call_args.args
            assert arguments[0:2] == ("POST", "/responses")
            assert arguments[3]["Authorization"] == "Bearer " + key
            sent = json.loads(arguments[2])
            assert sent["reasoning"] == {"effort": "high"}
            assert sent["tools"][0]["type"] == "function"
            assert sent["model"] == "deepseek-flash"
            assert sent["store"] is False
            assert len(resolved) == 1


def test_revoke_is_checked_before_any_provider_send():
    def revoked():
        raise ModelUnavailable()

    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        with personal_gateway(revoked) as gateway:
            execution_id = uuid4()
            grant = {"token": gateway.grants.issue(execution_id), "base_url": f"http://127.0.0.1:{gateway.server_address[1]}/v1"}
            client = http.client.HTTPConnection("127.0.0.1", gateway.server_address[1], timeout=2)
            try:
                client.request("POST", "/v1/responses", json.dumps({"input": "check", "model": "deepseek-flash"}), {"Authorization": "Bearer " + grant["token"]})
                response = client.getresponse()
                assert response.status == 503
                response.read()
            finally:
                client.close()
            upstream.return_value.request.assert_not_called()


def test_rebinding_runtime_revokes_all_previous_user_grants():
    grants = Grants("deepseek-flash")
    first, second = uuid4(), uuid4()
    first_token = grants.issue(first)
    grants.revoke_all()
    second_token = grants.issue(second)
    with pytest.raises(ValueError, match="Invalid execution grant"):
        grants.authorize(first_token, {"model": "deepseek-flash", "input": "first"}, 10)
    lease = grants.authorize(second_token, {"model": "deepseek-flash", "input": "second"}, 10)
    assert lease.execution_id == second


def test_each_grant_pins_its_own_identity_even_when_host_switches_active_user():
    alice, bob = str(uuid4()), str(uuid4())
    active = [alice]
    keys = {alice: str(uuid4()), bob: str(uuid4())}

    def bind_key():
        owner = active[0]
        return lambda: keys[owner]

    grants = Grants("deepseek-flash", provider_key_factory=bind_key)
    token = grants.issue(uuid4())
    lease = grants.authorize(token, {"model": "deepseek-flash", "input": "check"}, 10)
    active[0] = bob
    assert lease.provider_key() == keys[alice]
    keys[alice] = str(uuid4())
    assert lease.provider_key() == keys[alice]
