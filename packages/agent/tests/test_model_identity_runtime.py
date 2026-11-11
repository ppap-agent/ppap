"""Per-operation model identity, including interleaved and delayed HTTP work."""

import hashlib
import http.client
import json
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from agent.client import DeepError, ModelNotStarted
from agent.execution.gateway import Gateway, Grants
from agent.execution.guest_model import ModelGrant
from documents.model_access import ModelAccess, ModelUnavailable, current_model_access, model_access_scoped
from pydantic import SecretStr


def access(name):
    return ModelAccess(api_key=SecretStr("fixture-key-" + name), provider="responses-compatible", base_url=f"https://{name}.example/team/v1", model="model-" + name, revision=str(uuid4()))


def identity(chosen):
    """The frozen selection identity of one key that serves every use."""
    return {
        "source": "tenant",
        "provider": chosen.provider,
        "model": chosen.model,
        "vision_model": chosen.model,
        "coding_model": chosen.model,
        "revision": chosen.revision,
        "deployment": hashlib.sha256(chosen.base_url.encode()).hexdigest(),
    }


def selected(current, frozen):
    """Like the product callback: a key only while the current selection equals the frozen one."""

    def resolve(purpose):
        chosen = current[0]
        if chosen is None or identity(chosen) != frozen:
            raise ModelUnavailable("model_configuration_changed")
        return chosen

    return resolve


@contextmanager
def gateway():
    grants = Grants("inert-default", require_binding=True)

    def forbidden():
        raise AssertionError("Platform fallback must not run")

    server = Gateway(("127.0.0.1", 0), grants, "https://unused.example/v1", forbidden)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        grants.revoke_all()
        server.shutdown()
        thread.join()
        server.server_close()


def issue(grants, chosen, resolver):
    execution = uuid4()
    with grants.bind(execution, identity(chosen), resolver):
        token = grants.issue(execution)
    return execution, token


def post(server, token, prompt="check"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
    try:
        connection.request("POST", "/v1/responses", json.dumps({"model": "cli-compaction-alias", "input": prompt}), {"Authorization": "Bearer " + token})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def response(status=200):
    body = BytesIO(json.dumps({"output": [], "usage": {"input_tokens": 3, "output_tokens": 2}}).encode())
    return SimpleNamespace(status=status, read1=body.read1)


def test_interleaved_grants_bind_endpoint_model_and_initiator_without_rebinding():
    alice, bob = access("alice"), access("bob")
    current = {"alice": alice, "bob": bob}
    owners = []

    def resolver(owner):
        def resolve(purpose):
            owners.append(owner)
            return current[owner]

        return resolve

    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream, gateway() as server:
        upstream.return_value.getresponse.side_effect = [response(), response(), response()]
        a, token_a = issue(server.grants, alice, resolver("alice"))
        b, token_b = issue(server.grants, bob, resolver("bob"))
        for token, prompt in ((token_b, "bob"), (token_a, "late-alice"), (token_b, "bob-again")):
            assert post(server, token, prompt)[0] == 200
        assert owners == ["bob", "alice", "bob"]
        assert [call.args[0] for call in upstream.call_args_list] == ["bob.example", "alice.example", "bob.example"]
        calls = upstream.return_value.request.call_args_list
        for call, chosen in zip(calls, (bob, alice, bob), strict=True):
            assert call.args[0:2] == ("POST", "/team/v1/responses")
            assert call.args[3]["Authorization"] == "Bearer " + chosen.api_key.get_secret_value()
            assert json.loads(call.args[2])["model"] == chosen.model
        assert server.grants.usage(a)["completed_requests"] == 1
        assert server.grants.usage(b)["completed_requests"] == 2
        public = json.dumps(server.grants.identity(token_a))
        assert alice.base_url not in public and alice.api_key.get_secret_value() not in public
        # No implicit issuer or stale binding can mint an unrelated grant.
        with pytest.raises(ValueError, match="not bound"):
            server.grants.issue(uuid4())
        with pytest.raises(ValueError, match="not bound"):
            server.grants.issue(a)


@pytest.mark.parametrize("change", ["endpoint", "model", "revision", "provider", "removed"])
def test_configuration_change_after_grant_is_known_no_send(change):
    chosen = access("alice")
    current = [chosen]

    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream, gateway() as server:
        execution, token = issue(server.grants, chosen, selected(current, identity(chosen)))
        current[0] = {
            "endpoint": replace(chosen, base_url="https://other.example/v1"),
            "model": replace(chosen, model="different"),
            "revision": replace(chosen, revision=str(uuid4()), api_key=SecretStr("rotated-fixture-key")),
            "provider": replace(chosen, provider="other"),
            "removed": None,
        }[change]
        status, body = post(server, token)
        assert status == 503
        upstream.assert_not_called()
        assert chosen.base_url.encode() not in body and chosen.api_key.get_secret_value().encode() not in body
        receipt = server.grants.usage(execution)
        assert receipt["usage_known"] is True and receipt["completed_requests"] == 0
        assert receipt["transport_failure"]["error_type"] == "ModelUnavailable"


def test_redirect_does_not_send_any_request_to_another_endpoint():
    chosen = access("alice")
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream, gateway() as server:
        upstream.return_value.getresponse.return_value = response(status=307)
        execution, token = issue(server.grants, chosen, lambda purpose: chosen)
        assert post(server, token)[0] == 502
        assert upstream.call_count == upstream.return_value.request.call_count == 1
        assert server.grants.usage(execution)["usage_known"] is False


def test_revoked_late_grant_cannot_use_a_new_users_access():
    alice, bob = access("alice"), access("bob")
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream, gateway() as server:
        execution, token_a = issue(server.grants, alice, lambda purpose: alice)
        server.grants.revoke(execution)
        _, token_b = issue(server.grants, bob, lambda purpose: bob)
        assert post(server, token_a)[0] == 403
        upstream.assert_not_called()
        upstream.return_value.getresponse.return_value = response()
        assert post(server, token_b)[0] == 200
        assert upstream.call_args.args[0] == "bob.example"
        assert upstream.return_value.request.call_args.args[3]["Authorization"] == "Bearer " + bob.api_key.get_secret_value()


def test_optimizer_authored_trials_use_same_grant_for_text_and_vision(tmp_path, monkeypatch):
    from agent.execution.guest import run_entrypoint

    inputs, work, credentials = (tmp_path / name for name in ("inputs", "work", "grant"))
    for directory in (inputs, work, credentials):
        directory.mkdir()
    (inputs / "manifest.json").write_text(json.dumps({"operation": "adapt-checker"}))
    chosen = access("custom")
    grant = ModelGrant(
        base_url="http://127.0.0.1:9876/v1",
        token="temporary-local-grant-not-a-provider-key",
        wall_seconds=100,
        model="coding-m",
        text_model=chosen.model,
        vision_model="vision-m",
        provider=chosen.provider,
        revision=chosen.revision,
    )
    (credentials / "model.json").write_text(grant.model_dump_json())
    monkeypatch.setattr("agent.execution.guest_model.PiRunner", lambda raw: SimpleNamespace(grant=ModelGrant.model_validate(raw), wall_seconds=100))
    observed = []

    def trial(*args):
        text, vision = current_model_access("text"), current_model_access("vision")
        observed.append(text)
        assert text.provider == chosen.provider and text.model == chosen.model and text.revision == chosen.revision
        assert vision.model == "vision-m" and current_model_access("coding").model == "coding-m"
        for selected in (text, vision):
            assert selected.base_url == grant.base_url and selected.api_key.get_secret_value() == grant.token
            assert selected.base_url != chosen.base_url

    monkeypatch.setattr("agent.checker_program.run_adapt_manifest", trial)
    run_entrypoint(inputs, work, credentials)
    assert len(observed) == 1
    assert not model_access_scoped()


def test_runtime_rejects_queued_change_but_native_work_needs_no_key(monkeypatch):
    from agent.execution import harness
    from audit.policy import DeepExecutionPolicy

    monkeypatch.setenv("PPAP_DEEP_RUNTIME", "container-only")
    monkeypatch.setenv("PPAP_DEEP_SANDBOX_PROFILE", "off")
    chosen = access("alice")
    current = [identity(chosen)]
    policy = DeepExecutionPolicy(app_revision="a" * 40, model="unconfigured", model_revision="b" * 64, sandbox_profile="off")
    settings = SimpleNamespace(
        deep_execution_policy=lambda identity=None: policy.model_copy(update={"model": identity["model"]}) if identity else policy,
        deep_model_max_requests=7,
    )
    instances, calls = [], []
    grants = Grants("inert-default", require_binding=True)

    @contextmanager
    def fake_gateway(path, **kwargs):
        assert kwargs["require_binding"] is True
        assert kwargs["requests"] == 7 and not {"input_tokens", "output_tokens"} & set(kwargs)
        yield SimpleNamespace(socket_path=path, http=SimpleNamespace(grants=grants))

    class Runtime:
        degraded = False

        def __init__(self, config, **kwargs):
            self.config, self.binding = config, kwargs.get("model_grant_binding")
            instances.append(self)

        def ready(self):
            return True

        def recover(self):
            pass

    class Semantic:
        def __init__(self, runtime, *args, **kwargs):
            self.runtime = runtime

        def execute(self, spec, files, context):
            assert self.runtime.config.model == spec.model_identity["model"]
            calls.append(spec)
            return "native-completed"

    def no_key(spec, purpose):
        raise AssertionError("Native checks must not resolve credentials")

    monkeypatch.setattr(harness, "local_gateway", fake_gateway)
    monkeypatch.setattr(harness, "ContainerRuntime", Runtime)
    monkeypatch.setattr(harness, "CoreRuntime", Semantic)
    spec = SimpleNamespace(model_identity=identity(chosen))
    with harness.runtime_from_env(settings, object(), model_access_for_request=no_key, model_identity_for_request=lambda _: current[0]) as runtime:
        assert runtime.execute(spec, {}, context=None) == "native-completed"

        def late_unavailable(*args, **kwargs):
            raise ModelUnavailable()

        monkeypatch.setattr(Semantic, "execute", late_unavailable)
        with pytest.raises(DeepError, match="model_unavailable") as late:
            runtime.execute(spec, {}, context=None)
        assert not isinstance(late.value, ModelNotStarted)
        current[0] = identity(replace(chosen, revision=str(uuid4())))
        with pytest.raises(ModelNotStarted, match="model_unavailable"):
            runtime.execute(spec, {}, context=None)
    assert calls == [spec]
    assert len(instances) == 3


def test_container_issues_only_actual_execution_binding_and_stages_no_endpoint(tmp_path, monkeypatch):
    from agent.execution.isolation.container import ContainerRuntime

    chosen = access("custom")
    grants = Grants("inert-default", require_binding=True)
    runtime = object.__new__(ContainerRuntime)
    runtime.config = SimpleNamespace(gateway_socket=tmp_path / "control", gateway_host="127.0.0.1", model=chosen.model)
    runtime.model_grant_binding = lambda execution: grants.bind(execution, identity(chosen), lambda purpose: chosen)
    (tmp_path / "grant").mkdir()
    execution = uuid4()

    def issue_local(path, method, received, **kwargs):
        assert received == execution and method == "issue"
        token = grants.issue(received)
        return {"base_url": "http://127.0.0.1:9876/v1", "token": token, **grants.identity(token), "wall_seconds": 100}

    monkeypatch.setattr("agent.execution.isolation.container.grant_request", issue_local)
    monkeypatch.setattr("agent.execution.isolation.container.os.chown", lambda *args: None)
    runtime._write_grant(execution, tmp_path)
    rendered = (tmp_path / "grant" / "model.json").read_text()
    grant = ModelGrant.model_validate_json(rendered)
    assert chosen.base_url not in rendered and chosen.api_key.get_secret_value() not in rendered
    assert grant.provider == chosen.provider and grant.model == chosen.model
    assert grant.revision == chosen.revision and grant.deployment == identity(chosen)["deployment"]
    assert grants.bindings == {}
    lease, purpose = grants.authorize(grant.token, {"model": chosen.model}, 20)
    assert lease.access_resolver(purpose) == chosen


@pytest.mark.parametrize("field", ["source", "provider", "model", "vision_model", "coding_model", "revision", "deployment"])
def test_core_rejects_request_with_different_frozen_identity(tmp_path, field):
    from agent.core_program import CoreRuntime
    from agent.protocol import ExecuteRequest
    from agent.run import ExecutionContext

    chosen = access("custom")
    snapshot = SimpleNamespace(input_digest="a" * 64, tenant_id="tenant", round_id="round", runtime_revision="runtime", initiating_user_id="initiator", model_identity=identity(chosen))
    spec = ExecuteRequest(
        protocol_revision="protocol",
        round_id="round",
        executor="ppap-checker-core",
        executor_revision="runtime",
        tenant_id="tenant",
        initiating_user_id="initiator",
        checker_snapshot_digest="a" * 64,
        files=[],
        model_identity={**identity(chosen), field: "changed"},
    )
    runtime = CoreRuntime(object(), lambda _: snapshot, lambda *args: pytest.fail("Must reject before suite reads"))
    with pytest.raises(DeepError, match="deep_input_unauthorized"):
        runtime.execute(spec, {}, context=ExecutionContext())


def test_http_request_waiting_in_queue_checks_current_configuration_before_send():
    from threading import Event

    chosen = access("alice")
    current = [chosen]
    queued, release = Event(), Event()

    class QueueGate:
        def acquire(self, **kwargs):
            queued.set()
            return release.wait(2)

        def release(self):
            pass

    result = []
    with patch("agent.execution.gateway.http.client.HTTPSConnection") as upstream, gateway() as server:
        server.inference_lock = QueueGate()
        execution, token = issue(server.grants, chosen, selected(current, identity(chosen)))
        pending = Thread(target=lambda: result.append(post(server, token)), daemon=True)
        pending.start()
        try:
            assert queued.wait(2)
            current[0] = replace(chosen, base_url="https://changed.example/v1", revision=str(uuid4()))
        finally:
            release.set()
            pending.join(3)
        assert result[0][0] == 503
        upstream.assert_not_called()
        assert server.grants.usage(execution)["usage_known"] is True


def test_controller_source_authorization_matches_frozen_job_identity():
    from agent.protocol import ExecuteRequest
    from agent.source import authorize_execution

    frozen = identity(access("alice"))
    spec = ExecuteRequest(
        protocol_revision="protocol",
        round_id=str(uuid4()),
        job_id=str(uuid4()),
        tenant_id=str(uuid4()),
        executor="ppap-checker-core",
        executor_revision="runtime",
        checker_snapshot_digest="a" * 64,
        files=[],
        model_identity={**frozen, "revision": str(uuid4())},
    )
    payload = {"executor": spec.executor, "executor_revision": spec.executor_revision, "checker_snapshot_digest": spec.checker_snapshot_digest, "model_identity": frozen}

    @contextmanager
    def connect(*args):
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: (payload,)))

    authorize = authorize_execution(None, connect=connect, open_store=lambda _: SimpleNamespace(round_work=lambda *args: pytest.fail("Reject before resolving source inputs")))
    with pytest.raises(DeepError, match="deep_input_unauthorized"):
        authorize(spec)
