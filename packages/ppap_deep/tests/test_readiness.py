"""Readiness over the real local transport, without business or model calls."""

import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ppap_deep.auditor import SemanticRuntime
from ppap_deep.controller import Controller, UnconfiguredRuntime, UnixControllerServer
from ppap_deep.isolation.container import ContainerRuntime
from ppap_deep.readiness import probe


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
            runtime.config = SimpleNamespace(python_executable=tool, grok_executable=tool, gateway_socket=root / "gateway")
            runtime.degraded = False
            semantic = SemanticRuntime(runtime, None)
            controller = Controller(UnconfiguredRuntime(), forbidden, authorize=forbidden, readiness=semantic.ready)
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
