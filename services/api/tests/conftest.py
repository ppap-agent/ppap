"""API owner fixtures. Tests use injected callables, not sibling imports."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).with_name("store_fixture.py")
    spec = importlib.util.spec_from_file_location("ppap_api_owner_store", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_SUPPORT = _load()


@pytest.fixture(autouse=True)
def _inject_owner_store(request):
    if request.instance is None:
        return
    request.instance.auth_store = _SUPPORT.auth_store
    request.instance.seed_usage_event = _SUPPORT.seed_usage_event
    request.instance.set_trial_baseline = _SUPPORT.set_trial_baseline
    request.instance.MemoryObjects = _SUPPORT.MemoryObjects
