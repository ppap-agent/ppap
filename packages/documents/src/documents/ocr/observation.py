"""Which of two peer OCR observations a request belongs to; never part of the model input."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_OBSERVATION: ContextVar[int | None] = ContextVar("ocr_observation", default=None)


def current_ocr_observation() -> int | None:
    """Execution identity for caches and Replay."""
    return _OBSERVATION.get()


@contextmanager
def ocr_observation_scope(number: int) -> Iterator[None]:
    """Keep two peer observations distinct without changing their prompts."""
    if type(number) is not int or number not in {1, 2}:
        raise ValueError("OCR observation number must be 1 or 2")
    token = _OBSERVATION.set(number)
    try:
        yield
    finally:
        _OBSERVATION.reset(token)
