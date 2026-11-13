"""Isolated PostgreSQL proof for legacy carrier heads.

New admission refuses a carrier head, directly or as a related head.
A directory pin still freezes that revision.
"""

import json
import os
import uuid

import pytest
from audit.standard_suites import library_revision
from product.db import connect
from product.legacy_head_cutover import legacy_carrier_program
from product.settings import Settings
from product.store.suite_releases import SuiteReleaseStore
from product.store.suites import SuiteStore, freeze_selected_targets
from psycopg.types.json import Jsonb


def _files(checker: bytes, entry: str) -> dict:
    rules = {"title": "cutover", "rules": [{"id": "C1"}], "checker_binding": {"C1": entry}}
    return {"checker.py": checker, "rules.json": json.dumps(rules).encode()}


CARRIER = _files(
    b"from audit.standard_library import check_clause, prepare_target\n"
    b"RESOURCES = {'carriers': {'.xlsx': 'audit.checkers.v4.example:Missing'}, 'methods': {'C1': 'check'}}\n"
    b"def check(context):\n    return check_clause(context, RESOURCES)\n",
    "checker:check",
)
DIRECTORY = _files(b"def create_audit(context):\n    return []\n", "checker:create_audit")


@pytest.fixture
def cutover_db(schema_tools, memory_objects_type):
    schema_tools.require_postgres()
    database = "ppap_legacy_head_" + uuid.uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap"),
        "create legacy head database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    objects = memory_objects_type()
    try:
        yield settings, objects
    finally:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"),
            "drop legacy head database",
        )


def _proof(digest: str) -> dict:
    return {
        "reasons": [],
        "total_samples": 4,
        "passed_samples": 4,
        "new_errors": 0,
        "library_revision": library_revision(),
        "proof_digest": "ab" * 32,
        "report": {"related_revision_ids": []},
        "suite_digest": digest,
    }


def _install(store, key: str, files: dict, source_path: str | None):
    return store.create_revision(tenant_id=None, suite_key=key, files=files, created_by=None, source_path=source_path)


def _validate(conn, revision_id: str, digest: str):
    conn.execute(
        """INSERT INTO product.suite_validations(tenant_id, revision_id, status, revision, suite_digest, related_suite_digests, proof)
           VALUES (NULL, %s, 'verified', 1, %s, '{}', %s)""",
        (revision_id, digest, Jsonb(_proof(digest))),
    )


def _official_head(conn, key: str, revision_id: str):
    conn.execute(
        """INSERT INTO product.official_suite_heads(suite_key, current_revision_id, revision, validation_revision)
           VALUES (%s, %s, 1, 1)""",
        (key, revision_id),
    )


def _pin(conn, tenant: str, key: str, revision_id: str):
    conn.execute(
        """INSERT INTO product.tenant_suite_heads(tenant_id, suite_key, current_revision_id, revision, force_unverified)
           VALUES (%s, %s, %s, 1, true)""",
        (tenant, key, revision_id),
    )


def _admit(store, conn, tenant: str, key: str, *, preparation: bool):
    return freeze_selected_targets(store, conn, tenant, [{"role": "target", "file_version_id": "file-1", "rule_body": {"suite_key": key}}], preparation=preparation)


def test_legacy_heads_reject_new_admission_and_a_directory_pin_is_admitted(cutover_db, monkeypatch):
    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy resolver")

    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    assert legacy_carrier_program(CARRIER) is True
    assert legacy_carrier_program(DIRECTORY) is False

    official_key, pinned_key, directory_key = "OFFICIAL:OLD", "PIN:OLD", "PIN:V5"
    official_carrier = _install(suites, official_key, CARRIER, None)
    pinned_carrier = _install(suites, pinned_key, CARRIER, None)
    selected_carrier = _install(suites, directory_key, CARRIER, None)
    selected_directory = _install(suites, directory_key, DIRECTORY, "ACME/SEL")
    tenant_pin, tenant_directory, tenant_open = (str(uuid.uuid4()) for _ in range(3))

    with connect(settings, "migrator") as conn:
        for tenant, label in ((tenant_pin, "pin"), (tenant_directory, "dir"), (tenant_open, "open")):
            conn.execute(
                "INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,%s)",
                (tenant, tenant, label),
            )
        for revision in (official_carrier, pinned_carrier, selected_carrier, selected_directory):
            _validate(conn, revision.id, revision.resolved_digest)
        _official_head(conn, official_key, official_carrier.id)
        _official_head(conn, pinned_key, pinned_carrier.id)
        _official_head(conn, directory_key, selected_carrier.id)
        _pin(conn, tenant_pin, pinned_key, pinned_carrier.id)
        _pin(conn, tenant_directory, directory_key, selected_directory.id)

    with connect(settings, "migrator") as conn, conn.transaction():
        with pytest.raises(ValueError, match="suite_version_confirmation_required"):
            _admit(suites, conn, tenant_open, official_key, preparation=False)
        with pytest.raises(ValueError, match="suite_version_confirmation_required"):
            _admit(suites, conn, tenant_pin, pinned_key, preparation=True)
        selected = _admit(suites, conn, tenant_directory, directory_key, preparation=False)
    assert calls == []
    assert selected[0].suite_revision_id == selected_directory.id
    assert selected[0].suite_digest == selected_directory.resolved_digest

    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish_official(official_carrier.id, expected_revision=1, expected_validation_revision=1)
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key=%s", (official_key,)).fetchone()[0] == official_carrier.id


def test_directory_admission_refuses_a_related_carrier_head(cutover_db, monkeypatch):
    from product.identity import Principal

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("legacy resolver")

    monkeypatch.setattr("audit.presets.preset_source_bindings", forbidden)
    monkeypatch.setattr("audit.engine.routing.resolve_engine_agent_class", forbidden)
    settings, objects = cutover_db
    suites = SuiteStore(settings, objects, role="migrator")
    releases = SuiteReleaseStore(settings, objects, role="migrator")
    carrier = _install(suites, "REL:CARRIER", CARRIER, None)
    directory = suites.create_revision(
        tenant_id=None,
        suite_key="REL:DIR",
        files=DIRECTORY,
        related_suite_keys=("REL:CARRIER",),
        created_by=None,
        source_path="ACME/DIR",
    )
    tenant, user = (str(uuid.uuid4()) for _ in range(2))
    with connect(settings, "migrator") as conn:
        conn.execute("INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,'rel')", (tenant, tenant))
        conn.execute("INSERT INTO product.users(id) VALUES(%s)", (user,))
        conn.execute("INSERT INTO product.memberships(user_id,tenant_id,role) VALUES(%s,%s,'member')", (user, tenant))
        _validate(conn, carrier.id, carrier.resolved_digest)
        _validate(conn, directory.id, directory.resolved_digest)
        conn.execute(
            "UPDATE product.suite_validations SET related_suite_digests=%s WHERE revision_id=%s",
            (Jsonb({"REL:CARRIER": carrier.resolved_digest}), directory.id),
        )
        _official_head(conn, "REL:CARRIER", carrier.id)
    principal = Principal(user, tenant, "test", "test", tenant, user, "member")
    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish(principal, "REL:DIR", directory.id, 0, str(uuid.uuid4()), force_unverified=True)
    with pytest.raises(ValueError, match="suite_version_confirmation_required"):
        releases.publish_official(directory.id, expected_revision=0, expected_validation_revision=1)
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT current_revision_id::text FROM product.official_suite_heads WHERE suite_key='REL:CARRIER'").fetchone()[0] == carrier.id
        assert conn.execute("SELECT count(*) FROM product.official_suite_heads WHERE suite_key='REL:DIR'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM product.tenant_suite_heads WHERE tenant_id=%s AND suite_key='REL:DIR'", (tenant,)).fetchone()[0] == 0
        _pin(conn, tenant, "REL:DIR", directory.id)
        with conn.transaction():
            with pytest.raises(ValueError, match="suite_version_confirmation_required"):
                _admit(suites, conn, tenant, "REL:DIR", preparation=True)
    assert calls == []
