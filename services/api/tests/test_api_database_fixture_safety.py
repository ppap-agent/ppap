import pytest


def test_ci_requires_explicit_database_port(monkeypatch, auth_store_factory):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("PGPORT", raising=False)
    with pytest.raises(RuntimeError, match="explicit PGPORT"):
        auth_store_factory()
