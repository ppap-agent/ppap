"""Trusted resource limits for one Deep execution."""

DEEP_TOTAL_SECONDS = 40 * 60
# Deep mode writes task-specific programs first; the audit itself keeps the rest of
# the guest's time. A target not developed by then runs its Suite program.
DEEP_DEVELOPMENT_SECONDS = 25 * 60
MAX_INPUT_TOTAL = 128 * 1024 * 1024
MAX_INPUT_FILES = 256
