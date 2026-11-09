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
        for source_path, digest in ((" v5/UAES/U08", "a" * 64), ("x" * 513, "b" * 64), ("", "c" * 64)):
            with pytest.raises(psycopg.Error, match="suite_revision_source_path_shape"):
                conn.execute(
                    """INSERT INTO product.suite_revisions(id,revision_number,suite_key,overrides,resolved_digest,source_path)
                       VALUES (%s,1,%s,'{}',%s,%s)""",
                    (str(uuid.uuid4()), f"shape-{digest[:8]}", digest, source_path),
                )

    first = seed_standard_suites(store)
    assert len(first) == 116
    assert all(row["validation"] == "not-created" for row in first)
    directory_id = next(row["revision_id"] for row in first if row["suite_key"] == "UAES:U08" and row["source_path"] == "v5/UAES/U08")
    legacy_id = next(row["revision_id"] for row in first if row["suite_key"] == "UAES:U08" and row["source_path"] != "v5/UAES/U08")
    with connect(settings, "migrator") as conn:
        stored = dict(conn.execute("SELECT id::text, source_path FROM product.suite_revisions WHERE tenant_id IS NULL AND suite_key='UAES:U08'").fetchall())
        assert stored[directory_id] == "v5/UAES/U08"
        assert stored[legacy_id] is None
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (untouched.id,)).fetchone()[0] is None
        before = conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0]
    assert store.official_revision_id_for_source("v5/UAES/U08") == directory_id
    assert seed_standard_suites(store) == first
    with connect(settings, "migrator") as conn:
        assert conn.execute("SELECT count(*) FROM product.suite_revisions").fetchone()[0] == before
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (legacy_id,)).fetchone()[0] is None
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (untouched.id,)).fetchone()[0] is None
        assert conn.execute("SELECT source_path FROM product.suite_revisions WHERE id=%s", (directory_id,)).fetchone()[0] == "v5/UAES/U08"

    source = next(suite for suite in predefined_directory_suites() if suite.source_path == "v5/UAES/U08")
    copies = install_official_sources(store, (source, replace(source, source_path="v6/UAES/U08")))
    assert copies[0]["resolved_digest"] == copies[1]["resolved_digest"]
    assert copies[0]["revision_id"] != copies[1]["revision_id"]
    assert copies[0]["revision_id"] == directory_id
    assert store.official_revision_id_for_source("v6/UAES/U08") == copies[1]["revision_id"]
