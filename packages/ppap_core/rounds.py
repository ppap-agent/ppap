"""Product audit round statuses. Not a Live S04 proof."""
from __future__ import annotations

ROUND_ACCEPTED = "accepted"
ROUND_RUNNING = "running"
ROUND_SUCCEEDED = "succeeded"
ROUND_FAILED = "failed"
ROUND_STATUSES = {ROUND_ACCEPTED, ROUND_RUNNING, ROUND_SUCCEEDED, ROUND_FAILED}
