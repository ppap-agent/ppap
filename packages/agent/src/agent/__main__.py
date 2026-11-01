"""Host/sidecar Controller process. Owns the isolated runtime; Worker is a UDS client only."""

from __future__ import annotations


def main() -> int:
    from worker.deep_controller import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
