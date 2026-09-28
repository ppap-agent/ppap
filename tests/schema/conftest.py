"""Schema fixtures. PostgreSQL remains the migration authority.

Support modules stay owner-local. Tests receive them by fixture injection so
importlib collection does not put sibling test modules on sys.path.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent


def _load(stem: str):
    path = _DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"ppap_schema_support_{stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_UPGRADE = _load("test_upgrade")
_OBJECTS = _load("memory_objects")
_SAMPLE = _load("sample_rule")

_EXPORTS = {
    "MIGRATIONS": _UPGRADE.MIGRATIONS,
    "_bootstrap_password": _UPGRADE._bootstrap_password,
    "_bootstrap_user": _UPGRADE._bootstrap_user,
    "_migrator_password": _UPGRADE._migrator_password,
    "_migrator_user": _UPGRADE._migrator_user,
    "_psql": _UPGRADE._psql,
    "_require": _UPGRADE._require,
    "_require_postgres": _UPGRADE._require_postgres,
    "migration_files": _UPGRADE.migration_files,
    "MemoryObjects": _OBJECTS.MemoryObjects,
    "rule_body": _SAMPLE.rule_body,
}


@pytest.fixture(autouse=True)
def _inject_schema_support(request):
    if request.instance is None:
        return
    for name, value in _EXPORTS.items():
        setattr(request.instance, name, value)
