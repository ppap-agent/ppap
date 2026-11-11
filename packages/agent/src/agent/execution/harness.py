"""Production semantic runtime. No fixed-checker or placeholder Deep fallback."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from documents.model_access import ModelUnavailable

from agent.client import DeepError, ModelNotStarted
from agent.core_program import CoreRuntime
from agent.execution.isolation import ContainerRuntime, RuntimeConfig, local_gateway


@contextmanager
def runtime_from_env(settings, objects, *, model_access_for_request=None, model_identity_for_request=None, snapshot_for_request=None, suite_for_revision=None, save_round_artifacts=None):
    if os.environ.get("PPAP_DEEP_RUNTIME", "") != "container-only":
        raise ValueError("PPAP_DEEP_RUNTIME must explicitly select container-only")
    profile = os.environ.get("PPAP_DEEP_SANDBOX_PROFILE", "")
    if profile not in {"off", "ppap"}:
        raise ValueError("PPAP_DEEP_SANDBOX_PROFILE must explicitly select off or ppap")
    state = Path("/run/ppap-isolation")
    policy = settings.deep_execution_policy()
    if policy.sandbox_profile != profile:
        raise ValueError("Deep runtime profile differs from the configured execution policy")
    slot = threading.Lock()

    def unavailable():
        raise ModelUnavailable()

    # This inert default cannot send: every production grant requires a host
    # binding. Neither global endpoint nor active user is rebound between jobs.
    with local_gateway(
        state / "gateway.sock",
        upstream="https://unconfigured.invalid",
        model="unconfigured",
        provider_key=unavailable,
        require_binding=True,
        requests=settings.deep_model_max_requests,
    ) as gateway:
        config = RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump())
        bootstrap = ContainerRuntime(config)
        bootstrap.recover()

        class TenantModelRuntime:
            def ready(self):
                return bootstrap.ready()

            def execute(self, spec, files, *, context):
                return self._invoke(spec, lambda semantic, runtime: semantic.execute(spec, files, context=context))

            def execute_precheck(self, payload, snapshot, sources, programs, context):
                from agent.precheck_program import execute_precheck

                return self._invoke(None, lambda semantic, runtime: execute_precheck(runtime, payload, snapshot, sources, programs, context))

            def develop_and_run(self, request, *, spec, context):
                return self._invoke(spec, lambda semantic, runtime: semantic.develop_and_run(request, spec=spec, context=context))

            def _invoke(self, spec, operation):
                with slot:
                    if bootstrap.degraded:
                        raise DeepError("deep_runtime_unavailable")
                    expected = dict(spec.model_identity) if spec is not None else None

                    def resolve_access(purpose):
                        # The product callback returns the model for this use only while
                        # the tenant's selection still equals the frozen identity.
                        if spec is None or model_access_for_request is None:
                            raise ModelUnavailable()
                        return model_access_for_request(spec, purpose)

                    runtime = None
                    try:
                        # Metadata reads do not require a key. Pure native work
                        # remains available, but queued configuration changes do
                        # not silently alter an already accepted operation.
                        if spec is not None:
                            try:
                                if model_identity_for_request is None or model_identity_for_request(spec) != expected:
                                    raise ModelUnavailable()
                            except ModelUnavailable:
                                # Dispatch may already be persisted, but no
                                # guest or provider request has started here.
                                raise ModelNotStarted() from None
                        selected = settings.deep_execution_policy(expected)
                        runtime = ContainerRuntime(
                            RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **selected.model_dump()),
                            model_grant_binding=lambda execution_id: gateway.http.grants.bind(execution_id, expected or {}, resolve_access),
                        )
                        semantic = CoreRuntime(runtime, snapshot_for_request, suite_for_revision, objects=objects, save_round_artifacts=save_round_artifacts)
                        return operation(semantic, runtime)
                    except ModelUnavailable:
                        raise DeepError("model_unavailable") from None
                    finally:
                        # Leases retain their own resolver, identity and upstream
                        # connection even if a late HTTP handler is still draining.
                        gateway.http.grants.revoke_all()
                        if runtime is not None and runtime.degraded:
                            bootstrap.degraded = True

        yield TenantModelRuntime()
