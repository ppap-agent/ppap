"""Readiness over the real local transport, without business or model calls."""

import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from agent.execution.isolation.container import ContainerRuntime
from agent.readiness import probe
from agent.run import Controller, UnconfiguredRuntime, UnixControllerServer


def forbidden(*_args, **_kwargs):
    raise AssertionError("readiness must not access business data")


def test_live_readiness_tracks_runtime_degraded_and_missing_tools():
    with tempfile.TemporaryDirectory(prefix="ready-") as directory:
        root = Path(directory)
        tool = root / "tool"
        tool.touch()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as gateway:
            gateway.bind(str(root / "gateway"))
            # Observe the real readiness method without starting a privileged guest.
            runtime = object.__new__(ContainerRuntime)
            runtime.config = SimpleNamespace(python_executable=tool, agent_executable=tool, gateway_socket=root / "gateway")
            runtime.degraded = False
            controller = Controller(UnconfiguredRuntime(), forbidden, authorize=forbidden, readiness=runtime.ready)
            server = UnixControllerServer(str(root / "controller"), controller)
            server.start()
            try:
                assert probe(server.path)
                runtime.degraded = True
                assert not probe(server.path)
                runtime.degraded = False
                tool.unlink()
                assert not probe(server.path)
            finally:
                server.close()


def test_readiness_is_fail_closed_and_strict():
    controller = Controller(UnconfiguredRuntime(), forbidden)
    assert controller.handle_raw({"op": "ready"}) == {"op": "ready", "ready": False}
    controller.readiness = forbidden
    assert controller.handle_raw({"op": "ready"}) == {"op": "ready", "ready": False}
    assert controller.handle_raw({"op": "ready", "command": "anything"})["code"] == "deep_protocol_invalid"


def test_missing_or_stale_socket_is_not_ready():
    assert not probe("")
    with tempfile.TemporaryDirectory(prefix="ready-") as directory:
        path = str(Path(directory) / "socket")
        assert not probe(path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
            stale.bind(path)
        assert not probe(path)


def test_silent_listener_times_out_without_retry():
    with tempfile.TemporaryDirectory(prefix="ready-") as directory:
        path = str(Path(directory) / "socket")
        done = threading.Event()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)

            def silent():
                conn, _ = listener.accept()
                with conn:
                    done.wait(2)

            thread = threading.Thread(target=silent)
            thread.start()
            try:
                started = time.monotonic()
                assert not probe(path, timeout=0.05)
                assert time.monotonic() - started < 1
            finally:
                done.set()
                thread.join(2)


def test_configuration_observation_is_a_frozen_non_secret_startup_identity():
    identity = {"agent": "pi", "agent_version": "1.5.0", "model": "model", "reasoning_effort": "medium", "executor_revision": "a" * 64}
    controller = Controller(UnconfiguredRuntime(), forbidden, authorize=forbidden, configuration=identity)
    identity["model"] = "later"
    observed = controller.handle_raw({"op": "configuration"})
    assert observed["configuration"]["model"] == "model"
    observed["configuration"]["model"] = "caller mutation"
    assert controller.handle_raw({"op": "configuration"})["configuration"]["model"] == "model"
    assert controller.handle_raw({"op": "configuration", "command": "anything"})["code"] == "deep_protocol_invalid"
