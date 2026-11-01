"""Release observations keep working while old API processes are stopped."""

import runpy
from pathlib import Path

import psycopg
import pytest
from product.db import connect
from psycopg.errors import ReadOnlySqlTransaction

RELEASE = runpy.run_path(str(Path(__file__).resolve().parents[3] / "deploy/tke/release-current.py"))


@pytest.mark.parametrize("full", [False, True])
def test_release_snapshot_uses_api_role_and_preserves_history(auth_store_factory, full):
    store = auth_store_factory()
    store.seed_isolated_dev()
    expected = {}
    with connect(store.settings, "api") as conn:
        expected["migrations"] = [row[0] for row in conn.execute("SELECT version FROM product.schema_migrations ORDER BY version")]
        expected["active_jobs"] = [list(row) for row in conn.execute("SELECT kind,status,count(*) FROM engine.jobs WHERE status IN ('queued','leased') GROUP BY kind,status ORDER BY kind,status")]
        if full:
            for name in ("file_versions", "rule_versions", "audit_drafts", "audit_rounds", "findings", "report_exports", "usage_events"):
                sql = "SELECT count(*),md5(string_agg(fingerprint,chr(44) ORDER BY fingerprint)) "
                sql += f"FROM (SELECT md5(to_jsonb(t)::text) AS fingerprint FROM product.{name} t) rows"
                expected[name] = list(conn.execute(sql).fetchone())

    query = RELEASE["database_sql"](full=full).decode()
    diagnostics = "SELECT current_user, current_setting('transaction_read_only'), current_setting('transaction_isolation'); COMMIT;"
    with psycopg.connect(store.settings.dsn("bootstrap"), dbname=store.settings.pg_database, autocommit=True) as conn:
        cursor = conn.execute(query.replace("COMMIT;", diagnostics))
        results = []
        while True:
            if cursor.description:
                results.append(cursor.fetchone())
            if not cursor.nextset():
                break
    assert results == [(expected,), ("api", "on", "repeatable read")]

    with psycopg.connect(store.settings.dsn("bootstrap"), dbname=store.settings.pg_database, autocommit=True) as conn:
        with pytest.raises(ReadOnlySqlTransaction):
            conn.execute(query.replace("COMMIT;", "DELETE FROM product.file_versions; COMMIT;"))
        conn.rollback()
