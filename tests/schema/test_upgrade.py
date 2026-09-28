"""Upgrade empty and previous databases through the canonical migration runner."""
from __future__ import annotations

import unittest
import uuid

import pytest


class SchemaUpgradeTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools):
        self.schema = schema_tools

    def test_numbered_files_are_the_only_ordering_authority(self):
        versions = [path.name for path in self.schema.migration_files()]
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(self.schema.latest_version(), versions[-1])
        self.assertEqual(self.schema.previous_version(), versions[-2])
        self.assertTrue(all(name.endswith(".sql") for name in versions))
        self.assertFalse((self.schema.MIGRATIONS / "apply.sql").exists())

        runner = (self.schema.MIGRATIONS / "apply.sh").read_text()
        self.assertIn("LC_ALL=C", runner)
        self.assertIn("/migrations/[0-9][0-9][0-9][0-9]_*.sql", runner)
        self.assertIn("psql -d \"$PPAP_PG_DATABASE\" -v ON_ERROR_STOP=1 -f \"$migration\"", runner)
        for name in versions:
            self.assertNotIn(name, runner)

    def test_empty_and_previous_schema_reach_latest_through_shared_runner(self):
        self.schema.require_postgres()
        versions = [path.name for path in self.schema.migration_files()]
        empty = "ppap_schema_empty_" + uuid.uuid4().hex[:8]
        previous = "ppap_schema_prev_" + uuid.uuid4().hex[:8]
        try:
            for name in (empty, previous):
                self.schema.require(
                    self.schema.psql(
                        self.schema.bootstrap_user(),
                        self.schema.bootstrap_password(),
                        "postgres",
                        "-c",
                        f"CREATE DATABASE {name} OWNER {self.schema.migrator_user()}",
                    ),
                    f"create {name}",
                )

            self.schema.require(self.schema.apply(empty), "apply empty")
            self.assertEqual(self.schema.applied(empty), versions)
            self.schema.require(self.schema.apply(empty), "re-apply empty")
            self.assertEqual(self.schema.applied(empty), versions)

            for name in versions[:-1]:
                self.schema.require(
                    self.schema.psql(
                        self.schema.migrator_user(),
                        self.schema.migrator_password(),
                        previous,
                        "-f",
                        str(self.schema.MIGRATIONS / name),
                    ),
                    f"prepare previous schema with {name}",
                )
            self.assertEqual(self.schema.applied(previous), versions[:-1])

            self.schema.require(self.schema.apply(previous), "upgrade previous")
            self.assertEqual(self.schema.applied(previous), versions)

            tables = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                empty,
                "-Atc",
                "SELECT tablename FROM pg_tables WHERE schemaname IN ('product', 'engine')",
            )
            self.schema.require(tables, "list tables")
            names = set((tables.stdout or b"").decode().split())
            self.assertIn("audit_rounds", names)
            self.assertIn("usage_events", names)
            self.assertIn("jobs", names)
        finally:
            for name in (empty, previous):
                self.schema.psql(
                    self.schema.bootstrap_user(),
                    self.schema.bootstrap_password(),
                    "postgres",
                    "-c",
                    f"DROP DATABASE IF EXISTS {name} WITH (FORCE)",
                )
