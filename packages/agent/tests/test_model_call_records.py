"""Model call records at the local gateway (#360).

A direct call (text, vision) from the guest hands its own record to the gateway; every other request (the deep
review agent) is recorded as sent and answered. No record carries the provider key or the grant, and a record the
host cannot keep never changes the call.
"""

import base64
import http.client
import json
from contextlib import contextmanager
from io import BytesIO
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.execution.gateway import Gateway, Grants
from agent.execution.guest_model import GatewayCallLog, ModelProtocolError
from documents.ai import run_json
from documents.model_access import ModelAccess, model_access_scope
from documents.model_calls import CALL_HEADER, model_call_recorder
from pydantic import SecretStr

# As deployed: one model frozen for text and the deep review.
IDENTITY = {"source": "platform", "provider": "responses-compatible", "model": "text-m", "vision_model": "text-m", "coding_model": "text-m", "revision": "r" * 64, "deployment": "d" * 64}
PROVIDER_KEY = "sk-" + "provider-0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes"
REPLY = {
    "id": "resp-controlled",
    "object": "response",
    "created_at": 0,
    "status": "completed",
    "model": "text-m",
    "output": [{"type": "message", "id": "msg-1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": '{"relation":"supports"}', "annotations": []}]}],
    "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
}


class Log:
    """What the host keeps, as RoundCallLog does: a direct call's record only for a call the gateway forwarded."""

    def __init__(self, fail: bool = False) -> None:
        self.seen_calls: list[str] = []
        self.begun = 0
        self.records: list[tuple[dict, dict, bool]] = []
        self.fail = fail

    def seen(self, call_id):
        self.seen_calls.append(call_id)

    def begin(self):
        self.begun += 1

    def write(self, record, images, *, exchange=False):
        if self.fail:
            raise OSError("object store unavailable")
        if not exchange and record["call_id"] not in self.seen_calls:
            raise ValueError("unknown_model_call")
        self.records.append((record, dict(images), exchange))


@contextmanager
def gateway(log):
    access = ModelAccess(api_key=SecretStr(PROVIDER_KEY), model="text-m", base_url="https://text.example.test/v1", revision="a" * 64, reasoning_effort="medium")
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream:
        upstream.return_value.getresponse.side_effect = lambda: type("R", (), {"status": 200, "read1": BytesIO(json.dumps(REPLY).encode()).read1})()
        grants = Grants("unconfigured", require_binding=True)
        server = Gateway(("127.0.0.1", 0), grants, "https://unconfigured.invalid", lambda: "")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            execution_id = uuid4()
            with grants.bind(execution_id, IDENTITY, lambda purpose: access, log):
                token = grants.issue(execution_id)
            yield f"http://127.0.0.1:{server.server_address[1]}/v1", token, upstream
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


def guest_call(base_url, token):
    """A checker's text call in the guest: the grant is its only credential, the gateway its only recorder."""
    grant = ModelAccess(api_key=SecretStr(token), model="text-m", base_url=base_url, source="grant", revision="r" * 64, reasoning_effort="low")
    with model_access_scope(lambda purpose: grant), model_call_recorder(GatewayCallLog(base_url, token)):
        return run_json("rule_text_evidence", instructions="judge", prompt="原文片段", max_tokens=50, timeout=10)


def agent_request(base_url, token):
    """The deep review agent's own request, with an inline image."""
    image = {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(PNG).decode()}
    body = {"model": "text-m", "input": [{"role": "user", "content": [{"type": "input_text", "text": "look"}, image]}]}
    port = int(base_url.split(":")[2].split("/")[0])
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        client.request("POST", "/v1/responses", json.dumps(body), {"Authorization": "Bearer " + token})
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


def test_each_model_call_is_recorded_once_without_key_or_grant():
    log = Log()
    with gateway(log) as (base_url, token, upstream):
        assert guest_call(base_url, token).output == {"relation": "supports"}
        assert agent_request(base_url, token)[0] == 200
    (direct, kept, exchange), (sent, images, exchanged) = log.records
    # The direct call: its own record, under the call the gateway forwarded, naming who really answered.
    assert not exchange and log.seen_calls == [direct["call_id"]]
    assert (direct["kind"], direct["task"], direct["code"], json.loads(direct["answer"])) == ("text", "rule_text_evidence", "ok", {"relation": "supports"})
    assert direct["identity"] == {"host": "text.example.test", "model": "text-m", "reasoning_effort": "low", "revision": "a" * 64, "source": "platform"}
    assert kept == {}
    # The agent's request as sent and answered; the image is kept once, apart, by digest.
    assert exchanged and log.begun == 1
    digest = next(iter(images))
    assert images == {digest: PNG}
    assert sent["request"]["input"][0]["content"][1]["image_url"] == f"<image sha256:{digest}>"
    assert sent["request"]["store"] is False and sent["response"] == REPLY and sent["code"] == "ok"
    assert sent["identity"]["host"] == "text.example.test"
    # The call's name never leaves the gateway, and no record holds the provider key or the grant.
    assert all(set(call.args[3]) == {"Authorization", "Content-Type", "X-Client-Request-Id"} for call in upstream.return_value.request.call_args_list)
    assert CALL_HEADER not in json.dumps([call.args[3] for call in upstream.return_value.request.call_args_list])
    stored = json.dumps([record for record, _images, _exchange in log.records], ensure_ascii=False)
    assert PROVIDER_KEY not in stored and token not in stored


def test_a_record_the_host_cannot_keep_never_changes_the_call():
    with gateway(Log(fail=True)) as (base_url, token, _upstream):
        assert guest_call(base_url, token).output == {"relation": "supports"}
        assert agent_request(base_url, token) == (200, json.dumps(REPLY).encode())


def test_a_record_needs_the_grant_and_a_forwarded_call():
    log = Log()
    with gateway(log) as (base_url, token, _upstream):
        record = {"call_id": uuid4().hex, "purpose": "text", "model": "text-m"}
        with pytest.raises(ModelProtocolError, match="403"):
            GatewayCallLog(base_url, "x" * 64).write(record, {})
        # A call the gateway never forwarded has no record to keep.
        with pytest.raises(ModelProtocolError, match="503"):
            GatewayCallLog(base_url, token).write(record, {})
    assert log.records == []
