"""Scalar identity and field predicate evaluation."""

from __future__ import annotations

import math
from typing import cast

from .models import Predicate, Scalar


def _require_finite(value: Scalar) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite source value")


def scalar_key(value: Scalar) -> tuple[str, Scalar]:
    _require_finite(value)
    return ("number" if type(value) in (int, float) else type(value).__name__, value)


def predicate_result(predicate: Predicate, value: Scalar) -> bool | None:
    _require_finite(value)
    if predicate.op in {"empty", "not_empty"}:
        empty = value is None or (isinstance(value, str) and not value.strip())
        return empty if predicate.op == "empty" else not empty
    if predicate.op == "eq":
        return scalar_key(value) == scalar_key(predicate.value)
    if predicate.op == "ne":
        return scalar_key(value) != scalar_key(predicate.value)
    if predicate.op == "in":
        return any(scalar_key(value) == scalar_key(candidate) for candidate in predicate.values)
    if type(value) not in (int, float) or type(predicate.value) not in (int, float):
        return None
    _require_finite(value)
    _require_finite(predicate.value)
    left, right = cast(int | float, value), cast(int | float, predicate.value)
    return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[predicate.op]
