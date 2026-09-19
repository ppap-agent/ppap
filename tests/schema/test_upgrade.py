"""Upgrade an empty database and the previous schema to latest."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "packages")]

from tools.migrate import apply_migrations, latest_version, migration_versions, previous_version


def _dsn(database: str) -> str:
    from ppap_core.settings import Settings

    settings = Settings()
    user = os.environ.get("PPAP_PG_MIGRATOR_USER", settings.pg_migrator_user)
    password = os.environ.get("PPAP_PG_MIGRATOR_PASSWORD", settings.pg_migrator_password)
    return (
        f"postgresql://{user}:{password}@{settings.pg_host}:{settings.pg_port}/{database}"
    )


def _bootstrap_dsn() -> str:
    from ppap_core.settings import Settings

    settings = Settings()
    user = os.environ.get("PPAP_PG_BOOTSTRAP_USER", settings.pg_bootstrap_user)
    password = os.environ.get("PPAP_PG_BOOTSTRAP_PASSWORD", settings.pg_bootstrap_password)
    return f"postgresql://{user}:{password}@{settings.pg_host}:{settings.pg_port}/postgres"


def _applied(conn) -> list[str]:
    rows = conn.execute("SELECT version FROM product.schema_migrations ORDER BY version").fetchall()
    return [row[0] for row in rows]


class SchemaUpgradeTests(unittest.TestCase):
    def test_migration_files_are_forward_only(self):
        versions = migration_versions()
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(latest_version(), versions[-1])
        self.assertEqual(previous_version(), versions[-2])
        self.assertTrue(all(name.endswith(".sql") for name in versions))

    def test_empty_and_previous_schema_reach_latest(self):
        import psycopg
        from psycopg import sql

        try:
            bootstrap = psycopg.connect(_bootstrap_dsn(), autocommit=True)
        except Exception as exc:
            self.skipTest(f"postgres not available: {exc}")
        empty = "ppap_schema_empty_" + uuid.uuid4().hex[:8]
        previous = "ppap_schema_prev_" + uuid.uuid4().hex[:8]
        try:
            from ppap_core.settings import Settings

            owner = os.environ.get("PPAP_PG_MIGRATOR_USER", Settings().pg_migrator_user)
            for name in (empty, previous):
                bootstrap.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner))
                )
            with psycopg.connect(_dsn(empty), autocommit=True) as conn:
                first = apply_migrations(conn)
                self.assertEqual(first, migration_versions())
                self.assertEqual(_applied(conn), migration_versions())
                self.assertEqual(apply_migrations(conn), [])
                tables = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT tablename FROM pg_tables
                        WHERE schemaname IN ('product', 'engine')
                        """
                    )
                }
            self.assertIn("audit_rounds", tables)
            self.assertIn("usage_events", tables)
            self.assertIn("jobs", tables)
            with psycopg.connect(_dsn(previous), autocommit=True) as conn:
                before_latest = apply_migrations(conn, stop_before=latest_version())
                self.assertEqual(before_latest, migration_versions()[:-1])
                self.assertEqual(_applied(conn), migration_versions()[:-1])
                upgraded = apply_migrations(conn)
                self.assertEqual(upgraded, [latest_version()])
                self.assertEqual(_applied(conn), migration_versions())
        finally:
            for name in (empty, previous):
                bootstrap.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
            bootstrap.close()


if __name__ == "__main__":
    unittest.main()
