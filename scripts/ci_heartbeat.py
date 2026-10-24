"""Fail-closed subprocess execution with bounded time and progress heartbeats."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path


def run_with_heartbeat(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    interval: float = 30,
    timeout: float = 1800,
) -> None:
    """Print progress even during silent I/O; preserve the child exit code."""
    if interval <= 0 or timeout <= 0:
        raise ValueError("Heartbeat interval and timeout must be positive")
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                print(f"CI command exceeded {timeout:g}s; terminating process group", flush=True)
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise subprocess.CalledProcessError(124, command)
            try:
                return_code = process.wait(timeout=min(interval, timeout - elapsed))
            except subprocess.TimeoutExpired:
                print(f"CI heartbeat: child PID={process.pid}, elapsed={time.monotonic() - started:.0f}s", flush=True)
                continue
            if return_code:
                raise subprocess.CalledProcessError(return_code, command)
            return
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd:
        parser.error("missing command after --")
    try:
        run_with_heartbeat(cmd, cwd=Path.cwd(), interval=args.interval, timeout=args.timeout)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
