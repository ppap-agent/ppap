"""Responses/tool transport through the gateway with the selected model key."""

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


def test_a_live_grant_gets_one_more_task_of_time_and_a_revoked_one_never_does(monkeypatch):
    import time

    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    grants = Grants("test-model", ttl=60)
    execution = uuid4()
    token = grants.issue(execution)
    now[0] += 50
    grants.renew(execution)
    now[0] += 50
    # Past the first task's time, still within the renewed one.
    lease, _ = grants.authorize(token, {"model": "test-model", "input": "next task"}, 10)
    grants.record_usage(lease, None)
    now[0] += 61
    with pytest.raises(ValueError, match="Invalid execution grant"):
        grants.renew(execution)
    with pytest.raises(ValueError, match="Invalid execution grant"):
        grants.authorize(token, {"model": "test-model", "input": "late"}, 10)
    revoked = uuid4()
    grants.issue(revoked)
    grants.revoke(revoked)
    with pytest.raises(ValueError, match="Invalid execution grant"):
        grants.renew(revoked)


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


def test_bound_grant_sends_each_request_unchanged_to_its_models_endpoint():
    from documents.model_access import ModelAccess
    from pydantic import SecretStr

    # As deployed: one model frozen for text and coding, so both uses share its endpoint and key.
    identity = {"source": "platform", "provider": "responses-compatible", "model": "text-m", "vision_model": "vision-m", "coding_model": "text-m", "revision": "r" * 64, "deployment": "d" * 64}
    accesses = {
        "coding": ModelAccess(api_key=SecretStr("text-key"), model="text-m", base_url="https://text.example.test/v1", reasoning_effort="medium"),
        "vision": ModelAccess(api_key=SecretStr("vision-key"), model="vision-m", base_url="https://vision.example.test/api/v1", reasoning_effort="none"),
        "text": ModelAccess(api_key=SecretStr("text-key"), model="text-m", base_url="https://text.example.test/v1", reasoning_effort="low"),
    }
    reply = json.dumps({"id": "r", "object": "response", "created_at": 0, "model": "m", "status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
    # Each use writes its own effort; the gateway sends exactly that.
    sent = [
        {"input": "x", "model": "vision-m", "reasoning": {"effort": "none"}},
        {"input": "x", "model": "text-m", "reasoning": {"effort": "medium", "summary": "auto"}},
        {"input": "x", "model": "text-m", "reasoning": {"effort": "low"}},
    ]
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
            statuses = []
            for body in (*sent, {"input": "x", "model": "not-frozen"}):
                client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
                try:
                    client.request("POST", "/v1/responses", json.dumps(body), {"Authorization": "Bearer " + token})
                    statuses.append(client.getresponse().status)
                finally:
                    client.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
    assert statuses[:3] == [200, 200, 200] and statuses[3] != 200
    assert [call.args[0] for call in upstream.call_args_list] == ["vision.example.test", "text.example.test", "text.example.test"]
    requests = [(call.args[1], json.loads(call.args[2]), call.args[3]["Authorization"]) for call in upstream.return_value.request.call_args_list]
    assert [(path, key) for path, _body, key in requests] == [("/api/v1/responses", "Bearer vision-key"), ("/v1/responses", "Bearer text-key"), ("/v1/responses", "Bearer text-key")]
    assert [(body["model"], body["reasoning"]) for _path, body, _key in requests] == [(item["model"], item["reasoning"]) for item in sent]
    assert token not in json.dumps(requests)


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
