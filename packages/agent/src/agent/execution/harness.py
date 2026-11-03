"""Production semantic runtime. No fixed-checker or placeholder Deep fallback."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from agent.auditor import SemanticRuntime
from agent.execution.isolation import ContainerRuntime, RuntimeConfig, local_gateway


@contextmanager
def runtime_from_env(settings, objects):
    if os.environ.get("PPAP_DEEP_RUNTIME", "") != "container-only":
        raise ValueError("PPAP_DEEP_RUNTIME must explicitly select container-only")
    profile = os.environ.get("PPAP_DEEP_SANDBOX_PROFILE", "")
    if profile not in {"off", "ppap"}:
        raise ValueError("PPAP_DEEP_SANDBOX_PROFILE must explicitly select off or ppap")
    state = Path("/run/ppap-isolation")
    policy = settings.deep_execution_policy()
    if policy.sandbox_profile != profile:
        raise ValueError("Deep runtime profile differs from the configured execution policy")
    base_url, model, api_key = settings.deep_model_config()
    with local_gateway(state / "gateway.sock", upstream=base_url, model=model, provider_key=api_key) as gateway:
        runtime = ContainerRuntime(RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump()))
        runtime.recover()
        yield SemanticRuntime(runtime, objects)
