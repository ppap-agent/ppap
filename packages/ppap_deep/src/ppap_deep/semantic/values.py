"""One scalar contract for field predicates and relation keys."""

from __future__ import annotations

import math
from typing import Any, cast

from .models import Key, Normalization, Predicate, Scalar


def normalized(value: Any, policy: Normalization) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite source value")
    if isinstance(value, str) and policy != "exact":
        value = " ".join(value.split())
        if policy == "casefold_whitespace":
            value = value.casefold()
    return value


def scalar_key(value: Scalar) -> tuple[str, Scalar]:
    normalized(value, "exact")
    return ("number" if type(value) in (int, float) else type(value).__name__, value)


def scalar_text(value: Scalar) -> str:
    if value is None:
        return ""
    if type(value) in (int, float) and value == 0:
        return "0"
    if type(value) is float and value.is_integer():
        return format(value, ".0f")
    return str(value)


def predicate_result(predicate: Predicate, value: Scalar) -> bool | None:
    normalized(value, "exact")
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
    normalized(value, "exact")
    normalized(predicate.value, "exact")
    left, right = cast(int | float, value), cast(int | float, predicate.value)
    return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[predicate.op]


def record_key(record: dict[str, Any], key: Key) -> tuple | None:
    if record["scope_state"] != "known" or any(record["states"].get(field) != "known" for field in key.fields):
        return None
    values = [normalized(record["values"][field], key.normalization) for field in key.fields]
    if all(value is None or isinstance(value, str) and not value.strip() for value in values):
        return None
    if key.template is not None:
        values = [normalized(key.template.format_map({field: scalar_text(value) for field, value in zip(key.fields, values, strict=True)}), key.normalization)]
    elif key.separator is not None:
        values = [normalized(key.separator.join(scalar_text(value) for value in values), key.normalization)]
    return tuple(scalar_key(value) for value in values)


def could_match(key: tuple, record: dict[str, Any], projection: Key) -> bool:
    if projection.separator is not None or projection.template is not None:
        if any(record["states"].get(field) != "known" for field in projection.fields):
            return True
        return record_key({**record, "scope_state": "known"}, projection) == key
    for component, field in zip(key, projection.fields, strict=True):
        if record["states"].get(field) == "known" and scalar_key(normalized(record["values"][field], projection.normalization)) != component:
            return False
    return True
