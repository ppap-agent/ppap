import contextlib
from types import SimpleNamespace

import pytest
from product.seed_standard_suites import seed_standard_suites
from product.suites import validate_content


class Connection:
    def __init__(self, existing):
        self.existing = existing
        self.lookup = None

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, params=()):
        if "SELECT id" in sql:
            self.lookup = params[0]
        return self

    def fetchone(self):
        return (self.lookup,) if self.lookup in self.existing else None


class Store:
    role = "migrator"

    def __init__(self, existing=()):
        self.conn = Connection(existing)
        self.created = []
        self.resolved = []

    def _conn(self):
        return contextlib.nullcontext(self.conn)

    def resolve_suite(self, revision_id, tenant, *, conn):
        self.resolved.append(revision_id)

    def create_revision(self, **kwargs):
        validate_content(kwargs["files"], official=True)
        assert kwargs["tenant_id"] is None and kwargs["created_by"] is None
        self.created.append(kwargs["suite_key"])
        return SimpleNamespace(id=kwargs["suite_key"])


def test_seed_installs_customer_and_historical_sources_without_validation_or_publication():
    store = Store()
    result = seed_standard_suites(store)
    assert len(store.created) == len(result) == 75
    assert all(row["validation"] == "not-created" for row in result)
    existing = Store(existing=store.created)
    assert seed_standard_suites(existing) == result
    assert existing.created == []
    assert existing.resolved == store.created


def test_api_role_cannot_install_official_sources():
    store = Store()
    store.role = "api"
    with pytest.raises(PermissionError, match="official_suite_write_forbidden"):
        seed_standard_suites(store)


def test_revision_digest_matches_content_contract():
    from audit.standard_suites import standard_suites
    from audit.suite_migration import generic_standard_suites
    from product.suites import content_digest, resolved_digest

    for suite in (*standard_suites(), *generic_standard_suites(include_history=True)):
        assert suite.revision_digest == resolved_digest(suite.suite_key, suite.related_suite_keys, {key: content_digest(value) for key, value in suite.files.items()})
