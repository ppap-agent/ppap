import contextlib
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from product.seed_standard_suites import discovery_row, install_official_sources, match_official_revision, official_source_suites, persisted_source_path, seed_standard_suites
from product.suites import validate_content


class Connection:
    def __init__(self, existing):
        self.existing = existing
        self.lookup = None

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, params=()):
        if "source_path" in sql and "SELECT" in sql:
            self.lookup = (params[0], params[1])
        return self

    def fetchall(self):
        return list(self.existing.get(self.lookup, ()))


class Store:
    role = "migrator"

    def __init__(self, existing=None):
        self.conn = Connection({} if existing is None else existing)
        self.created = []
        self.created_kwargs = []
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
        self.created_kwargs.append(kwargs)
        return SimpleNamespace(id=revision_id)


def _installed(result):
    existing = {}
    for row in result:
        existing.setdefault((row["suite_key"], row["resolved_digest"]), []).append((row["revision_id"], persisted_source_path(row["source_path"])))
    return existing


def test_seed_installs_customer_and_historical_sources_without_validation_or_publication():
    store = Store()
    result = seed_standard_suites(store)
    assert len(store.created) == len(result) == 116
    assert all(row["validation"] == "not-created" for row in result)
    u08 = [row for row in result if row["suite_key"] == "UAES:U08"]
    assert {row["source_path"] for row in u08} >= {"v5/UAES/U08"}
    # The legacy carrier and the directory leaf are different bytes, so their content digests differ.
    assert len({row["source_path"] for row in u08}) == len({row["resolved_digest"] for row in u08}) == len({row["revision_id"] for row in u08}) == 2
    by_id = {row["revision_id"]: row for row in result}
    for revision_id, kwargs in zip(store.created, store.created_kwargs, strict=True):
        assert kwargs["source_path"] == persisted_source_path(by_id[revision_id]["source_path"])
    directory = next(kwargs for kwargs, row in zip(store.created_kwargs, result, strict=True) if row["source_path"] == "v5/UAES/U08")
    assert directory["source_path"] == "v5/UAES/U08"
    assert directory["related_suite_keys"] == ("UAES:U07", "UAES:U16")
    assert sum(kwargs["source_path"] is None for kwargs in store.created_kwargs) == 75
    assert sum(kwargs["source_path"] is not None for kwargs in store.created_kwargs) == 41
    again = Store(_installed(result))
    assert seed_standard_suites(again) == result
    assert again.created == []
    assert again.resolved == [row["revision_id"] for row in result]


def test_api_role_cannot_install_official_sources():
    store = Store()
    store.role = "api"
    with pytest.raises(PermissionError, match="official_suite_write_forbidden"):
        seed_standard_suites(store)


def test_revision_digest_matches_content_contract():
    from product.suites import content_digest, resolved_digest

    suites = official_source_suites()
    assert len(suites) == 116
    assert max(Counter((suite.suite_key, suite.revision_digest) for suite in suites).values()) == 1
    non_directory = [suite.source_path for suite in suites if persisted_source_path(suite.source_path) is None]
    assert len(non_directory) == 75
    assert len(set(non_directory)) < len(non_directory)
    assert persisted_source_path("v5/UAES/U08") == "v5/UAES/U08"
    for suite in suites:
        assert suite.revision_digest == resolved_digest(suite.suite_key, suite.related_suite_keys, {key: content_digest(value) for key, value in suite.files.items()})


def test_identical_directory_copies_stay_distinct_without_a_new_digest():
    from audit.standard_suites import predefined_directory_suites, standard_suites

    directory = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    copy = replace(directory, source_path="v6/UAES/U08")
    legacy = next(suite for suite in standard_suites() if suite.suite_key == "UAES:U08")
    assert directory.files == copy.files
    assert directory.revision_digest == copy.revision_digest
    assert directory.revision_digest != legacy.revision_digest
    assert match_official_revision([("old", None)], persisted_path="v5/UAES/U08", shared_content=False) is None
    assert match_official_revision([("old", None)], persisted_path=None, shared_content=False) == "old"
    assert match_official_revision([("old", None)], persisted_path=None, shared_content=True) is None
    assert match_official_revision([("v5", "v5/UAES/U08"), ("v6", "v6/UAES/U08")], persisted_path="v6/UAES/U08", shared_content=True) == "v6"

    store = Store()
    first = install_official_sources(store, (directory, copy))
    assert [row["resolved_digest"] for row in first] == [directory.revision_digest, directory.revision_digest]
    assert len({row["revision_id"] for row in first}) == 2
    assert [kwargs["source_path"] for kwargs in store.created_kwargs] == ["v5/UAES/U08", "v6/UAES/U08"]
    assert all(kwargs["related_suite_keys"] == ("UAES:U07", "UAES:U16") for kwargs in store.created_kwargs)
    again = Store(_installed(first))
    assert install_official_sources(again, (directory, copy)) == first
    assert again.created == []

    historical = Store({(directory.suite_key, directory.revision_digest): [("historical-null", None)]})
    third = install_official_sources(historical, (directory, copy))
    assert "historical-null" not in {row["revision_id"] for row in third}
    assert [kwargs["source_path"] for kwargs in historical.created_kwargs] == ["v5/UAES/U08", "v6/UAES/U08"]

    reused = Store({(legacy.suite_key, legacy.revision_digest): [("legacy-row", None)]})
    assert install_official_sources(reused, (legacy,))[0]["revision_id"] == "legacy-row"
    assert reused.created == []


def test_directory_discovery_keeps_placeholders_and_states_the_reader_limit():
    import json

    from audit.standard_suites import predefined_directory_suites, standard_suites
    from product.suite_releases import leaf_capability

    physical = {suite.source_path: suite for suite in predefined_directory_suites()}
    assert len(physical) == 41
    official = {suite.source_path for suite in official_source_suites()}
    assert set(physical) <= official
    rows = {suite.source_path: discovery_row(suite) for suite in physical.values()}
    reader_ids = [clause_id for row in rows.values() for clause_id in row["capability"]["evidence_reader_clause_ids"]]
    assert len(reader_ids) == 52
    assert set(reader_ids) == {clause["id"] for path, suite in physical.items() if path.startswith("v5/UAES/") for clause in json.loads(suite.files["rules.json"])["rules"]}
    placeholder = rows["v5/理想/L01"]
    rules = json.loads(physical["v5/理想/L01"].files["rules.json"])
    assert rules["directory_coverage"] == "complete"
    assert placeholder["capability"]["evidence_reader_clause_ids"] == []
    assert placeholder["capability"]["bound_clause_count"] == placeholder["capability"]["clause_count"] > 0
    assert "verified" not in placeholder["capability"]
    u08 = rows["v5/UAES/U08"]["capability"]
    assert u08["evidence_reader_clause_ids"] == [clause["id"] for clause in json.loads(physical["v5/UAES/U08"].files["rules.json"])["rules"]]
    assert u08["bound_clause_count"] == 12
    assert u08["clause_count"] == 12
    assert leaf_capability({"rules.json": b'{"rules":[{"id":"C1"},{"id":"C2"}],"checker_binding":{"C1":"checker:create_audit"},"directory_coverage":"complete"}'}) == {
        "clause_count": 2,
        "bound_clause_count": 1,
        "evidence_reader_clause_ids": [],
    }
    legacy = next(suite for suite in standard_suites() if suite.suite_key == "UAES:U08")
    assert "capability" not in discovery_row(legacy)


def test_source_lookup_rejects_a_blank_path_before_opening_a_database():
    from product.store.suites import SuiteStore

    store = SuiteStore.__new__(SuiteStore)
    with pytest.raises(ValueError, match="invalid_suite_source_path"):
        store.official_revision_id_for_source(" v5/UAES/U08")
