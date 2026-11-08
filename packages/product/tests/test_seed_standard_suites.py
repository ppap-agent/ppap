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
            self.lookup = (params[0], params[1])
        return self

    def fetchone(self):
        revision_id = self.existing.get(self.lookup)
        return (revision_id,) if revision_id else None


class Store:
    role = "migrator"

    def __init__(self, existing=None):
        self.conn = Connection({} if existing is None else existing)
        self.created = []
        self.resolved = []
        self._seq = 0

    def _conn(self):
        return contextlib.nullcontext(self.conn)

    def resolve_suite(self, revision_id, tenant, *, conn):
        self.resolved.append(revision_id)

    def create_revision(self, **kwargs):
        validate_content(kwargs["files"], official=True)
        assert kwargs["tenant_id"] is None and kwargs["created_by"] is None
        self._seq += 1
        revision_id = f"rev-{self._seq}"
        self.created.append(revision_id)
        return SimpleNamespace(id=revision_id)


def test_seed_installs_customer_and_historical_sources_without_validation_or_publication():
    store = Store()
    result = seed_standard_suites(store)
    assert len(store.created) == len(result) == 76
    assert all(row["validation"] == "not-created" for row in result)
    u08 = [row for row in result if row["suite_key"] == "UAES:U08"]
    assert {row["source_path"] for row in u08} >= {"v5/UAES/U08"}
    assert len({row["source_path"] for row in u08}) == len({row["resolved_digest"] for row in u08}) == len({row["revision_id"] for row in u08}) == 2
    existing = {(row["suite_key"], row["resolved_digest"]): row["revision_id"] for row in result}
    again = Store(existing)
    assert seed_standard_suites(again) == result
    assert again.created == []
    assert again.resolved == [row["revision_id"] for row in result]


def test_api_role_cannot_install_official_sources():
    store = Store()
    store.role = "api"
    with pytest.raises(PermissionError, match="official_suite_write_forbidden"):
        seed_standard_suites(store)


def test_revision_digest_matches_content_contract():
    from product.seed_standard_suites import official_source_suites
    from product.suites import content_digest, resolved_digest

    suites = official_source_suites()
    assert len(suites) == 76
    for suite in suites:
        assert suite.revision_digest == resolved_digest(suite.suite_key, suite.related_suite_keys, {key: content_digest(value) for key, value in suite.files.items()})
