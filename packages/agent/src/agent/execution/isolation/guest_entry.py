"""Low-privilege entrypoint. The trusted parent supplies only run-local paths."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    if os.getuid() != 10001 or os.getgid() != 10001 or os.environ.get("PPAP_GUEST_ISOLATION") != "container-only":
        raise RuntimeError("Low-privilege container guest is required")
    from agent.execution.guest import run_with_faults

    root = Path(os.environ["PPAP_GUEST_RUN"])
    run_with_faults(root / "inputs", root / "work", root / "grant")


if __name__ == "__main__":
    main()
