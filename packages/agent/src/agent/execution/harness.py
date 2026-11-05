"""Production semantic runtime. No fixed-checker or placeholder Deep fallback."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from documents.model_access import ModelUnavailable

from agent.auditor import SemanticRuntime
from agent.client import DeepError
from agent.execution.isolation import ContainerRuntime, RuntimeConfig, local_gateway


@contextmanager
def runtime_from_env(settings, objects, *, model_access_for_request=None):
    if os.environ.get("PPAP_DEEP_RUNTIME", "") != "container-only":
        raise ValueError("PPAP_DEEP_RUNTIME must explicitly select container-only")
    profile = os.environ.get("PPAP_DEEP_SANDBOX_PROFILE", "")
    if profile not in {"off", "ppap"}:
        raise ValueError("PPAP_DEEP_SANDBOX_PROFILE must explicitly select off or ppap")
    state = Path("/run/ppap-isolation")
    policy = settings.deep_execution_policy()
    if policy.sandbox_profile != profile:
        raise ValueError("Deep runtime profile differs from the configured execution policy")
    # The gateway is still the existing trusted transport. Its credential is
    # selected for the active authorized request, not once at process startup.
    active = [None]
    slot = threading.Lock()

    def bind_key():
        spec = active[0]
        if spec is None or model_access_for_request is None:
            raise ModelUnavailable()
        # Capture the non-secret identity at grant issuance. A late HTTP handler
        # can never switch to another user's key when the next run starts.
        return lambda: model_access_for_request(spec).api_key.get_secret_value()

    def resolve_key():
        return bind_key()()

    from documents.model_access import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

    with local_gateway(state / "gateway.sock", upstream=DEEPSEEK_BASE_URL, model=DEEPSEEK_MODEL, provider_key=resolve_key, provider_key_factory=bind_key) as gateway:
        runtime = ContainerRuntime(RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump()))
        runtime.recover()
        semantic = SemanticRuntime(runtime, objects)

        class PersonalRuntime:
            def ready(self):
                return semantic.ready()

            def execute(self, spec, files, *, context):
                return self._invoke(spec, lambda: semantic.execute(spec, files, context=context))

            def develop_and_run(self, request, *, spec, context):
                return self._invoke(spec, lambda: semantic.develop_and_run(request, spec=spec, context=context))

            def _invoke(self, spec, operation):
                with slot:
                    gateway.http.grants.revoke_all()
                    active[0] = spec
                    try:
                        # Fail with an actionable reason, without trying another
                        # member or platform key. Each subsequent call rechecks.
                        resolve_key()
                        return operation()
                    except ModelUnavailable:
                        raise DeepError("personal_model_unavailable") from None
                    finally:
                        gateway.http.grants.revoke_all()
                        active[0] = None

        yield PersonalRuntime()
