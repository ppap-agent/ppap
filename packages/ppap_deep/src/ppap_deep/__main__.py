"""Host/sidecar Controller process. Owns the isolated runtime; Worker is a UDS client only."""
from __future__ import annotations

import signal
import sys
import threading

from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_core.settings import load_settings
from ppap_deep.client import DEEP_REQUEST_TIMEOUT
from ppap_deep.controller import Controller, UnixControllerServer
from ppap_deep.runtime import runtime_from_env
from ppap_deep.source import object_file_source, authorize_execution

STOP = threading.Event()


def _stop(_signum, _frame) -> None:
    STOP.set()


def main() -> int:
    settings = load_settings()
    socket_path = settings.deep_controller_socket.strip()
    if not socket_path:
        print("PPAP_DEEP_CONTROLLER_SOCKET is required", file=sys.stderr)
        return 2
    revision = settings.deep_executor_revision.strip() or DEEP_PROTOCOL_REVISION
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with runtime_from_env(settings) as runtime:
        server = UnixControllerServer(
            socket_path,
            Controller(runtime, object_file_source(settings), executor_revision=revision,
                       authorize=authorize_execution(settings)),
            request_timeout=DEEP_REQUEST_TIMEOUT,
        )
        server.start()
        try:
            STOP.wait()
        finally:
            server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
