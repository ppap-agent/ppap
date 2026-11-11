"""Official source_path is a nullable directory identity. Legacy rows stay NULL."""

import os
import uuid
from dataclasses import replace

import psycopg
import pytest
from audit.standard_suites import predefined_directory_suites
from product.db import connect
from product.seed_standard_suites import install_official_sources, seed_standard_suites
from product.settings import Settings
from product.store.suites import SuiteStore


@pytest.fixture
def suite_db(schema_tools, memory_objects_type):
    schema_tools.require_postgres()
    database = "ppap_source_path_" + uuid.uuid4().hex[:10]
    schema_tools.require(
        schema_tools.psql(
            schema_tools.bootstrap_user(),
            schema_tools.bootstrap_password(),
            "postgres",
            "-c",
            f"CREATE DATABASE {database} OWNER {schema_tools.migrator_user()} TEMPLATE ppap",
        ),
        "create source path database",
    )
    settings = Settings(pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")))
    try:
        yield settings, memory_objects_type()
    finally:
        schema_tools.require(
            schema_tools.psql(schema_tools.bootstrap_user(), schema_tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"),
            "drop source path database",
        )


def test_real_postgres_source_path_shape_and_seed_do_not_backfill_null(suite_db):
    settings, objects, *_rest = suite_db
    store = SuiteStore(settings, objects, role="migrator")
    untouched = store.create_revision(
        tenant_id=None,
        created_by=None,
        suite_key="HIST:NULL",
        files={"rules.json": b'{"rules":[{"id":"r1"}]}', "checker.py": b"def check(ctx): pass"},
    )
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT count(*) FROM product.schema_migrations WHERE version='0059_predefined_source_path.sql'").fetchone()[0] == 1
        nullable = conn.execute(
            """SELECT is_nullable FROM information_schema.columns
               WHERE table_schema='product' AND table_name='suite_revisions' AND column_name='source_path'"""
        ).fetchone()[0]
        assert nullable == "YES"
        shape = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='suite_revision_source_path_shape'").fetchone()[0]
        assert "chr" not in shape and "btrim" in shape and "512" in shape
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (untouched.id,)).fetchone()[0] is None
        for source_path, digest in ((" UAES/U08", "a" * 64), ("x" * 513, "b" * 64), ("", "c" * 64)):
            with pytest.raises(psycopg.Error, match="suite_revision_source_path_shape"):
                conn.execute(
                    """INSERT INTO product.suite_revisions(id,revision_number,suite_key,overrides,resolved_digest,source_path)
                       VALUES (%s,1,%s,'{}',%s,%s)""",
                    (str(uuid.uuid4()), f"shape-{digest[:8]}", digest, source_path),
                )

    first = seed_standard_suites(store)
    assert len(first) == len(predefined_directory_suites())
    assert all(row["validation"] == "not-created" and row["source_path"] for row in first)
    directory_id = next(row["revision_id"] for row in first if row["source_path"] == "UAES/U08")
    with connect(settings, "migrator") as conn:
        stored = dict(conn.execute("SELECT id::text, source_path FROM product.suite_revisions WHERE tenant_id IS NULL AND suite_key='UAES:U08'").fetchall())
        assert stored == {directory_id: "UAES/U08"}
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (untouched.id,)).fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM product.suite_revisions WHERE tenant_id IS NULL AND source_path IS NULL AND suite_key <> 'HIST:NULL'").fetchone()[0] == 0
        before = conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0]
    assert store.official_revision_id_for_source("UAES/U08") == directory_id
    assert seed_standard_suites(store) == first
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == before
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (untouched.id,)).fetchone()[0] is None
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (directory_id,)).fetchone()[0] == "UAES/U08"

    source = next(suite for suite in predefined_directory_suites() if suite.source_path == "UAES/U08")
    copies = install_official_sources(store, (source, replace(source, source_path="UAES-v6/U08")))
    assert copies[0]["resolved_digest"] == copies[1]["resolved_digest"]
    assert copies[0]["revision_id"] != copies[1]["revision_id"]
    assert copies[0]["revision_id"] == directory_id
    assert store.official_revision_id_for_source("UAES-v6/U08") == copies[1]["revision_id"]


def test_real_postgres_company_path_migration_moves_only_version_prefixed_official_paths(suite_db, schema_tools):
    settings, objects, *_rest = suite_db
    store = SuiteStore(settings, objects, role="migrator")
    files = {"rules.json": b'{"rules":[{"id":"r1"}]}', "checker.py": b"def check(ctx): pass"}
    old = store.create_revision(tenant_id=None, created_by=None, suite_key="ACME:U08", files=files, source_path="v5/ACME/U08")
    current = store.create_revision(tenant_id=None, created_by=None, suite_key="ACME:U09", files={**files, "checker.py": b"def check(ctx): return 1"}, source_path="ACME/U09")
    with connect(settings, "migrator") as conn:
        digest = conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (old.id,)).fetchone()[0]
    for _ in range(2):
        schema_tools.require(
            schema_tools.psql(schema_tools.migrator_user(), schema_tools.migrator_password(), settings.pg_database, "-f", "migrations/0066_company_checker_source_paths.sql"),
            "apply company path migration",
        )
    with connect(settings, "migrator") as conn:
        rows = dict(conn.execute("SELECT id::text, source_path FROM product.suite_revisions WHERE id IN (%s, %s)", (old.id, current.id)).fetchall())
        assert rows == {old.id: "ACME/U08", current.id: "ACME/U09"}
        assert conn.execute("SELECT resolved_digest FROM product.suite_revisions WHERE id=%s", (old.id,)).fetchone()[0] == digest
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE product.suite_revisions SET source_path='ACME/X' WHERE id=%s", (old.id,))
