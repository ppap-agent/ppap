"""Responses/tool transport through the gateway with the selected model key."""

import http.client
import json
from contextlib import contextmanager
from io import BytesIO
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.execution.diagnostics import failure_facts
from agent.execution.gateway import Gateway, Grants
from documents.model_access import ModelUnavailable
from openai import OpenAI


@contextmanager
def key_gateway(resolve_key):
    # Exercise the real HTTP gateway; UDS framing has separate existing tests.
    grants = Grants("test-model")
    server = Gateway(("127.0.0.1", 0), grants, "https://models.example.test", resolve_key)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("receipt", [None, {"input_tokens": 17, "output_tokens": 4, "total_tokens": 21}])
def test_gateway_uses_the_selected_key_and_real_sdk_tool_shape(receipt):
    key = str(uuid4())
    provider = {
        "id": "response-controlled",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
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
        with key_gateway(resolve_key) as gateway:
            execution_id = uuid4()
            grant = {"token": gateway.grants.issue(execution_id), "base_url": f"http://127.0.0.1:{gateway.server_address[1]}/v1"}
            assert key not in json.dumps(grant)
            import httpx

            with OpenAI(api_key=grant["token"], base_url=grant["base_url"], max_retries=0, http_client=httpx.Client(trust_env=False)) as client:
                # A reply without a usage receipt is still the provider's answer.
                result = client.responses.create(
                    model="test-model", input="Read page one.", tools=[{"type": "function", "name": "read_source", "parameters": {"type": "object"}}], reasoning={"effort": "high"}
                )
                assert result.output[0].type == "function_call"
                assert result.output[0].name == "read_source"
            usage = gateway.grants.usage(execution_id)
            assert usage["usage_known"] is (receipt is not None)
            assert usage["completed_requests"] == 1
            assert key not in json.dumps(usage)
            arguments = upstream.return_value.request.call_args.args
            assert arguments[0:2] == ("POST", "/responses")
            assert arguments[3]["Authorization"] == "Bearer " + key
            sent = json.loads(arguments[2])
            assert sent["reasoning"] == {"effort": "high"}
            assert sent["tools"][0]["type"] == "function"
            assert sent["model"] == "test-model"
            assert sent["store"] is False
            assert len(resolved) == 1


def test_revoke_is_checked_before_any_provider_send():
    def revoked():
        raise ModelUnavailable()

    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        with key_gateway(revoked) as gateway:
            execution_id = uuid4()
            grant = {"token": gateway.grants.issue(execution_id), "base_url": f"http://127.0.0.1:{gateway.server_address[1]}/v1"}
            client = http.client.HTTPConnection("127.0.0.1", gateway.server_address[1], timeout=2)
            try:
                client.request("POST", "/v1/responses", json.dumps({"input": "check", "model": "test-model"}), {"Authorization": "Bearer " + grant["token"]})
                response = client.getresponse()
                assert response.status == 503
                response.read()
            finally:
                client.close()
            upstream.return_value.request.assert_not_called()
            usage = gateway.grants.usage(execution_id)
            assert usage["usage_known"] is True
            assert usage["completed_requests"] == 0
            assert usage["input_tokens"] == usage["output_tokens"] == 0
            assert usage["transport_failure"]["error_type"] == "ModelUnavailable"
            assert failure_facts({"usage": usage, "transport_failure": usage["transport_failure"]})["transport_failure"]["error_type"] == "ModelUnavailable"


def test_rebinding_runtime_revokes_all_previous_grants():
    grants = Grants("test-model")
    first, second = uuid4(), uuid4()
    first_token = grants.issue(first)
    grants.revoke_all()
    second_token = grants.issue(second)
    with pytest.raises(ValueError, match="Invalid execution grant"):
        grants.authorize(first_token, {"model": "test-model", "input": "first"}, 10)
    lease, _ = grants.authorize(second_token, {"model": "test-model", "input": "second"}, 10)
    assert lease.execution_id == second


def test_each_grant_pins_its_own_key_even_when_the_host_switches_it():
    alice, bob = str(uuid4()), str(uuid4())
    active = [alice]
    keys = {alice: str(uuid4()), bob: str(uuid4())}

    def bind_key():
        owner = active[0]
        return lambda: keys[owner]

    grants = Grants("test-model", provider_key_factory=bind_key)
    token = grants.issue(uuid4())
    lease, _ = grants.authorize(token, {"model": "test-model", "input": "check"}, 10)
    active[0] = bob
    assert lease.provider_key() == keys[alice]
    keys[alice] = str(uuid4())
    assert lease.provider_key() == keys[alice]


def test_bound_grant_sends_each_use_to_its_frozen_model_with_deployment_options():
    from documents.model_access import ModelAccess
    from pydantic import SecretStr

    identity = {"source": "platform", "provider": "responses-compatible", "model": "text-m", "vision_model": "vision-m", "coding_model": "coding-m", "revision": "r" * 64, "deployment": "d" * 64}
    accesses = {
        "coding": ModelAccess(api_key=SecretStr("coding-key"), model="coding-m", base_url="https://coding.example.test/v1", reasoning_effort="medium"),
        "vision": ModelAccess(
            api_key=SecretStr("vision-key"), model="vision-m", base_url="https://vision.example.test/api/v1", extra_body_json='{"reasoning":{"effort":"none"},"provider":{"require_parameters":true}}'
        ),
        "text": ModelAccess(api_key=SecretStr("text-key"), model="text-m", base_url="https://coding.example.test/v1", reasoning_effort="low"),
    }
    reply = json.dumps({"id": "r", "object": "response", "created_at": 0, "model": "m", "status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.side_effect = lambda: type("R", (), {"status": 200, "read1": BytesIO(reply).read1})()
        grants = Grants("unconfigured", require_binding=True)
        server = Gateway(("127.0.0.1", 0), grants, "https://unconfigured.invalid", lambda: "")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            execution_id = uuid4()
            with grants.bind(execution_id, identity, accesses.__getitem__):
                token = grants.issue(execution_id)
            for requested in ("vision-m", "pi-internal-alias"):
                client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
                try:
                    client.request("POST", "/v1/responses", json.dumps({"input": "x", "model": requested}), {"Authorization": "Bearer " + token})
                    assert client.getresponse().status == 200
                finally:
                    client.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
    (vision_host, *_), (coding_host, *_) = [call.args for call in upstream.call_args_list]
    assert (vision_host, coding_host) == ("vision.example.test", "coding.example.test")
    vision, coding = [(call.args[1], json.loads(call.args[2]), call.args[3]["Authorization"]) for call in upstream.return_value.request.call_args_list]
    assert vision[0] == "/api/v1/responses" and vision[2] == "Bearer vision-key"
    assert vision[1]["model"] == "vision-m" and vision[1]["reasoning"] == {"effort": "none"} and vision[1]["provider"] == {"require_parameters": True}
    assert coding[0] == "/v1/responses" and coding[2] == "Bearer coding-key"
    assert coding[1]["model"] == "coding-m" and coding[1]["reasoning"] == {"effort": "medium"}
    assert token not in json.dumps([vision, coding])


def test_a_stream_only_model_answers_a_non_streaming_guest_request_with_the_completed_response():
    from documents.model_access import ModelAccess
    from pydantic import SecretStr

    identity = {"source": "platform", "provider": "responses-compatible", "model": "m", "vision_model": "m", "coding_model": "m", "revision": "r" * 64, "deployment": "d" * 64}
    access = ModelAccess(api_key=SecretStr("coding-key"), model="m", base_url="https://models.example.test/backend-api/codex")
    completed = {"id": "r", "object": "response", "status": "completed", "output": [], "usage": {"input_tokens": 3, "output_tokens": 2}}
    stream = b'data: {"type":"response.created","response":{"id":"r"}}\n\n' + b"data: " + json.dumps({"type": "response.completed", "response": completed}).encode() + b"\n\n"
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.return_value = type("R", (), {"status": 200, "read1": BytesIO(stream).read1})()
        grants = Grants("unconfigured", require_binding=True)
        server = Gateway(("127.0.0.1", 0), grants, "https://unconfigured.invalid", lambda: "")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            execution = uuid4()
            with grants.bind(execution, identity, lambda purpose: access):
                token = grants.issue(execution)
            client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            try:
                client.request("POST", "/v1/responses", json.dumps({"input": "x", "model": "m"}), {"Authorization": "Bearer " + token})
                response = client.getresponse()
                assert response.status == 200 and response.getheader("Content-Type") == "application/json"
                assert json.loads(response.read()) == completed
            finally:
                client.close()
            assert grants.usage(execution)["input_tokens"] == 3
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
    sent = json.loads(upstream.return_value.request.call_args.args[2])
    assert sent["stream"] is True and upstream.return_value.request.call_args.args[1] == "/backend-api/codex/responses"
