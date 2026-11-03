"""Bounded local readiness observation. Does not submit an audit or call a model."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

from agent.client import DeepError, read_frame, write_frame


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


def configuration(path: str) -> dict:
    deadline = time.monotonic() + 5
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(path)
        write_frame(conn, {"op": "configuration"}, deadline=deadline)
        response = read_frame(conn, deadline=deadline)
    if response.get("op") != "configuration" or not isinstance(response.get("configuration"), dict):
        raise ValueError("Controller configuration unavailable")
    identity = response["configuration"]
    version = subprocess.run(["/usr/local/bin/ppap-pi", "--version"], capture_output=True, timeout=10, check=True)
    if version.stdout.decode().strip() != identity.get("agent_version"):
        raise ValueError("Installed agent version differs")
    return identity


if __name__ == "__main__":
    path = os.environ.get("PPAP_DEEP_CONTROLLER_SOCKET", "")
    if sys.argv[1:] == ["--configuration"]:
        try:
            print(json.dumps(configuration(path), sort_keys=True))
        except Exception:
            raise SystemExit("Controller configuration readback failed") from None
    else:
        raise SystemExit(0 if probe(path) else 1)
