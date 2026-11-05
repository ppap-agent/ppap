"""Immutable Suite constraints, byte resolution and creator deletion in PostgreSQL."""

import os
import uuid

import psycopg
import pytest
from product.db import connect
from product.settings import Settings
from product.store.suites import SuiteStore


@pytest.fixture
def suite_db(schema_tools, memory_objects_type):
    schema_tools.require_postgres()
    database = "ppap_suites_" + uuid.uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap"),
        "create Suite database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    objects = memory_objects_type()
    tenant, other, author = (str(uuid.uuid4()) for _ in range(3))
    with connect(settings, "migrator") as conn:
        for identifier in (tenant, other):
            conn.execute("INSERT INTO product.tenants(id,provider,external_key,display_name) VALUES(%s,'test',%s,'test')", (identifier, identifier))
        conn.execute("INSERT INTO product.users(id) VALUES(%s)", (author,))
    try:
        yield settings, objects, tenant, other, author
    finally:
        schema_tools.require(schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop Suite database")


def test_real_postgres_delta_constraints_and_author_deletion(suite_db):
    settings, objects, tenant, other, author = suite_db
    platform = SuiteStore(settings, objects, role="migrator")
    store = SuiteStore(settings, objects)
    baseline = platform.create_revision(tenant_id=None, created_by=None, suite_key="cp", files={"rules.json": b'{"rules":[{"id":"r1"}]}', "checker.py": b"def check(ctx): pass"})
    one = store.create_revision(tenant_id=tenant, created_by=author, suite_key="cp", base_revision_id=baseline.id, files={"checker.py": b"def check(ctx): return 1"})
    two = store.create_revision(tenant_id=tenant, created_by=author, suite_key="cp", base_revision_id=one.id, files={"templates.json": b'[{"kind":"structure"}]'})
    assert two.revision_number == 2 and list(two.overrides) == ["templates.json"]
    assert store.resolve_suite(two.id, tenant).files["rules.json"] == store.resolve_suite(baseline.id, tenant).files["rules.json"]
    assert store.get_revision(two.id, other) is None
    with pytest.raises(KeyError):
        store.create_revision(tenant_id=other, created_by=author, suite_key="cp", base_revision_id=one.id, files={})
    with connect(settings, "migrator") as conn:
        with pytest.raises(psycopg.Error, match="invalid_suite_base"):
            conn.execute(
                "INSERT INTO product.suite_revisions(id,tenant_id,suite_key,base_revision_id,overrides,resolved_digest,created_by,revision_number) VALUES(%s,%s,'cp',%s,'{}',%s,%s,1)",
                (str(uuid.uuid4()), other, one.id, "a" * 64, author),
            )
        with pytest.raises(psycopg.Error, match="immutable_checker_asset"):
            conn.execute("UPDATE product.suite_revisions SET resolved_digest=%s WHERE id=%s", ("b" * 64, two.id))
        conn.execute("DELETE FROM product.users WHERE id=%s", (author,))
    assert store.get_revision(two.id, tenant).created_by is None
    assert store.resolve_suite(two.id, tenant).files["checker.py"] == b"def check(ctx): return 1"
    with pytest.raises(PermissionError):
        store.create_revision(tenant_id=None, created_by=None, suite_key="cp", files={})


def test_real_postgres_rejects_missing_objects_and_original_byte_drift(suite_db):
    settings, objects, tenant, _, author = suite_db
    store = SuiteStore(settings, objects)
    revision = store.create_revision(tenant_id=tenant, created_by=author, suite_key="cp", files={"rules.json": b'{"rules":[{"id":"r1"}]}', "checker.py": b"def check(ctx): pass"})
    with connect(settings, "migrator") as conn:
        with pytest.raises(psycopg.Error, match="invalid_suite_object"):
            conn.execute(
                "INSERT INTO product.suite_revisions(id,tenant_id,suite_key,overrides,resolved_digest,created_by,revision_number) VALUES(%s,%s,'cp',%s,%s,%s,2)",
                (str(uuid.uuid4()), tenant, '{"checker.py":"' + "a" * 64 + '"}', "b" * 64, author),
            )
    key = f"suites/{tenant}/objects/{revision.overrides['checker.py']}"
    objects.data[key] = b"tampered"
    with pytest.raises(ValueError, match="digest_mismatch"):
        store.resolve_suite(revision.id, tenant)
