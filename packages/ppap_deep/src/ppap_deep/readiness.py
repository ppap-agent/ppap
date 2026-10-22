"""Bounded local readiness observation. Does not submit an audit or call a model."""

from __future__ import annotations

import os
import socket
import time

from ppap_deep.client import DeepError, read_frame, write_frame


def probe(path: str, *, timeout: float = 1.0) -> bool:
    if not path:
        return False
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(path)
            write_frame(conn, {"op": "ready"}, deadline=deadline)
            return read_frame(conn, deadline=deadline) == {"op": "ready", "ready": True}
    except (OSError, DeepError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if probe(os.environ.get("PPAP_DEEP_CONTROLLER_SOCKET", "")) else 1)
