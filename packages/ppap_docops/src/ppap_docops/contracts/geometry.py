"""Geometry contracts shared across ppap_docops modules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LineLike(Protocol):
    """Minimal line contract required by line-grid reconstruction logic."""

    x0: float
    y0: float
    x1: float
    y1: float
    page: int


__all__ = ["LineLike"]
