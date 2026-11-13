"""Private release readback of immutable process-start configuration; no model calls."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from product.settings import Settings


def verify_process_config(expected: dict[str, str], *, process_env: dict[str, str] | None = None) -> dict[str, str]:
    if not isinstance(expected, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in expected.items()):
        raise ValueError("Invalid expected configuration")
    if process_env is None:
        process_env = dict(item.decode().split("=", 1) for item in Path("/proc/1/environ").read_bytes().split(b"\0") if b"=" in item)
    # Environment values belong to the running container, not a newly resolved ConfigMap.
    if any(process_env.get(key) != value for key, value in expected.items()):
        raise ValueError("Process-start configuration differs from the release")
    if process_env.get("PPAP_SERVICE") != "deep" and process_env.get("PPAP_MODEL_CODING_API_KEY"):
        raise ValueError("Deep credential must belong only to the Controller")
    previous = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(process_env)
        return Settings().deep_execution_policy().runtime_identity()
    finally:
        os.environ.clear()
        os.environ.update(previous)


def main() -> int:
    try:
        identity = verify_process_config(json.load(sys.stdin))
        print(json.dumps(identity, sort_keys=True))
        return 0
    except Exception:
        # Provider credentials and the full environment must never enter release logs.
        print("Execution configuration readback failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
