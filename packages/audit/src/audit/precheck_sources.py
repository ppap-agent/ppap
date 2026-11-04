"""Packaged original 00-A v1.7 L2 criteria, not an execution status registry."""

import json
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def _source():
    return json.loads(files("audit").joinpath("precheck_l2.json").read_text(encoding="utf-8"))


def required_structure(preset_key: str) -> tuple[dict, ...]:
    """Return source wording/IDs. Callers must prove each criterion separately."""
    return tuple(deepcopy(_source()["entries"].get(preset_key, [])))


def structure_source() -> dict:
    return {key: value for key, value in _source().items() if key != "entries"}
