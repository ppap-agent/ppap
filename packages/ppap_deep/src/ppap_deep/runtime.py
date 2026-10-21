"""Production semantic runtime. No fixed-checker or placeholder Deep fallback."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from ppap_core.settings import Settings
from ppap_core.storage import S3Objects

from ppap_deep.auditor import SemanticRuntime
from ppap_deep.isolation import ContainerRuntime, RuntimeConfig, local_gateway


@contextmanager
def runtime_from_env(settings: Settings):
    if os.environ.get("PPAP_DEEP_RUNTIME", "") != "container-only":
        raise ValueError("PPAP_DEEP_RUNTIME must explicitly select container-only")
    profile = os.environ.get("PPAP_DEEP_SANDBOX_PROFILE", "")
    if profile not in {"off", "ppap"}:
        raise ValueError("PPAP_DEEP_SANDBOX_PROFILE must explicitly select off or ppap")
    state = Path("/run/ppap-isolation")
    policy = settings.deep_execution_policy()
    if policy.sandbox_profile != profile:
        raise ValueError("Deep runtime profile differs from the configured execution policy")
    with local_gateway(state / "gateway.sock", upstream=settings.llm_base_url, model=settings.llm_model, provider_key=settings.llm_api_key) as gateway:
        runtime = ContainerRuntime(RuntimeConfig(state=state, gateway_socket=gateway.socket_path, gateway_host="127.0.0.1", **policy.model_dump()))
        runtime.recover()
        yield SemanticRuntime(runtime, S3Objects(settings))
