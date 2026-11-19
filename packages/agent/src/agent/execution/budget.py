"""Trusted resource limits. A round runs in one guest that shares its context; each file in it is a task with its own time."""

# Checking one file: its Suite programs, in standard and deep rounds alike. Clauses it cannot start in time are technical errors.
CHECK_SECONDS = 3 * 60
# One deep task: the review of one file after its programs ran. It keeps its program result when unfinished.
DEEP_TOTAL_SECONDS = 6 * 60
# The most a guest may run without starting its next task: the longest task plus guest start and result handling.
# The host gives the guest, and the guest's model grant, this much again at every task it reports; a round has no time of its own.
TASK_SECONDS = max(CHECK_SECONDS, DEEP_TOTAL_SECONDS) + 60
MAX_INPUT_TOTAL = 128 * 1024 * 1024
MAX_INPUT_FILES = 256
