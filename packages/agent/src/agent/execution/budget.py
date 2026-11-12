"""Trusted resource limits for one Deep execution."""

DEEP_TOTAL_SECONDS = 40 * 60
# Deep review reads the program results after the Suite programs ran. A target not
# reviewed by then keeps its program result.
DEEP_REVIEW_SECONDS = 30 * 60
MAX_INPUT_TOTAL = 128 * 1024 * 1024
MAX_INPUT_FILES = 256
