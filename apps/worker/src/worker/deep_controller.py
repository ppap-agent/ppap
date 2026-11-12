"""Deep Controller composition. The agent package does not open product state."""

from __future__ import annotations

import signal
import sys
import threading

from agent.client import DEEP_REQUEST_TIMEOUT
from agent.execution.harness import runtime_from_env
from agent.run import Controller, UnixControllerServer
from agent.source import authorize_execution, object_file_source
from audit.checker_core import runtime_revision
from audit.standard_suites import library_revision
from product.db import connect
from product.settings import load_settings
from product.storage import S3Objects
from product.store.store import AuthStore
from product.store.suites import SuiteStore

STOP = threading.Event()


def _stop(_signum, _frame) -> None:
    STOP.set()


def model_access(store, spec, purpose):
    """The model for one use, only while the tenant's selection equals the frozen one."""
    from documents.model_access import ModelUnavailable

    selection = store.model_selection(spec.tenant_id)
    if selection.identity != dict(spec.model_identity):
        raise ModelUnavailable("model_configuration_changed")
    return selection.access(purpose)


def open_store(settings):
    return AuthStore(settings, role="worker")


def main() -> int:
    settings = load_settings()
    socket_path = settings.deep_controller_socket.strip()
    if not socket_path:
        print("PPAP_DEEP_CONTROLLER_SOCKET is required", file=sys.stderr)
        return 2
    revision = runtime_revision(settings.deep_execution_revision(), library_revision())
    objects = S3Objects(settings)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    store = open_store(settings)
    suites = SuiteStore(settings, objects, role="worker")
    with runtime_from_env(
        settings,
        objects,
        model_identity_for_request=lambda spec: store.model_identity(spec.tenant_id),
        model_access_for_request=lambda spec, purpose: model_access(store, spec, purpose),
        snapshot_for_request=lambda spec: suites.read_snapshot(spec.round_id, spec.tenant_id),
        suite_for_revision=suites.resolve_suite,
    ) as runtime:
        server = UnixControllerServer(
            socket_path,
            Controller(
                runtime,
                object_file_source(settings, connect=connect, objects=objects),
                executor_revision=revision,
                executor_revision_for_request=lambda spec: runtime_revision(settings.deep_execution_revision(spec.model_identity), library_revision()),
                authorize=authorize_execution(settings, connect=connect, open_store=open_store),
                readiness=runtime.ready,
                configuration=settings.deep_execution_policy().runtime_identity(),
            ),
            request_timeout=DEEP_REQUEST_TIMEOUT,
        )
        server.start()
        try:
            STOP.wait()
        finally:
            server.close()
    return 0
