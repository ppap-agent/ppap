"""Trusted resource limits. Every execution is one task: one file checked, or one file reviewed."""

# One file's Suite programs, in standard and deep rounds alike.
CHECK_SECONDS = 3 * 60
# One deep task: the review of one file after its programs ran. It keeps its program result when unfinished.
DEEP_TOTAL_SECONDS = 6 * 60
# The longest single task; a model grant never outlives it.
TASK_SECONDS = max(CHECK_SECONDS, DEEP_TOTAL_SECONDS)
MAX_INPUT_TOTAL = 128 * 1024 * 1024
MAX_INPUT_FILES = 256
