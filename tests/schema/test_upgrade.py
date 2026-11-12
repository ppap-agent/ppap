"""Upgrade empty and previous databases through the canonical migration runner."""

from __future__ import annotations

import unittest
import uuid

import pytest


class SchemaUpgradeTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools):
        self.schema = schema_tools

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

            first_apply = self.schema.apply(empty)
            self.schema.require(first_apply, "apply empty")
            executed = [line.removeprefix("applying ") for line in (first_apply.stdout or b"").decode().splitlines() if line.startswith("applying ")]
            self.assertEqual(executed, versions)
            self.assertEqual(self.schema.applied(empty), versions)

            tenant_id = str(uuid.uuid4())
            self.schema.require(
                self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    empty,
                    "-c",
                    "INSERT INTO product.tenants (id, provider, external_key, display_name, default_customer_preference, customer_preference_revision) "
                    f"VALUES ('{tenant_id}', 'feishu', 'default-replay', 'Replay', 'UAES', 7)",
                ),
                "save customer default before migration replay",
            )
            # Two saved-rule notifications are distinct intents. Full replay
            # must not rebuild an older index that collapses their identities.
            self.schema.require(
                self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    empty,
                    "-c",
                    "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) "
                    f"SELECT gen_random_uuid(),'{tenant_id}','feishu_notification',"
                    "jsonb_build_object('stage','formalization_ready','operation_id',gen_random_uuid()::text),'queued' FROM generate_series(1,2)",
                ),
                "save distinct operation intents before migration replay",
            )
            second_apply = self.schema.apply(empty)
            self.schema.require(second_apply, "re-apply empty")
            repeated = [line.removeprefix("applying ") for line in (second_apply.stdout or b"").decode().splitlines() if line.startswith("applying ")]
            self.assertEqual(repeated, versions)
            self.assertEqual(self.schema.applied(empty), versions)
            intents = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                empty,
                "-Atc",
                "SELECT count(*) FROM engine.jobs WHERE kind='feishu_notification' AND payload->>'stage'='formalization_ready'",
            )
            self.schema.require(intents, "read preserved operation intents")
            self.assertEqual(intents.stdout.strip(), b"2")
            default = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                empty,
                "-Atc",
                f"SELECT default_customer_preference, customer_preference_revision FROM product.tenants WHERE id = '{tenant_id}'",
            )
            self.schema.require(default, "read customer default after migration replay")
            self.assertEqual(default.stdout.strip(), b"UAES|7")

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
            self.schema.require(
                self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    previous,
                    "-c",
                    "INSERT INTO product.app_tickets (provider, app_id, ticket) VALUES ('feishu', 'upgrade-test-app', 'upgrade-test-ticket')",
                ),
                "seed previous ticket",
            )

            previous_apply = self.schema.apply(previous)
            self.schema.require(previous_apply, "upgrade previous")
            executed = [line.removeprefix("applying ") for line in (previous_apply.stdout or b"").decode().splitlines() if line.startswith("applying ")]
            self.assertEqual(executed, versions)
            self.assertEqual(self.schema.applied(previous), versions)
            ticket = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                previous,
                "-Atc",
                "SELECT ticket, provider_event_ts IS NULL FROM product.app_tickets WHERE provider='feishu' AND app_id='upgrade-test-app'",
            )
            self.schema.require(ticket, "read preserved ticket")
            self.assertEqual(ticket.stdout.strip(), b"upgrade-test-ticket|t")
            # One kind of audit (#322): the mode columns and Deep-only tables are gone.
            deep = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                previous,
                "-Atc",
                "SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema='product' AND column_name='analysis_mode')"
                " + (SELECT count(*) FROM pg_tables WHERE schemaname='product' AND tablename IN ('deep_rule_changes','checker_round_artifacts'))",
            )
            self.schema.require(deep, "read removed audit mode schema")
            self.assertEqual(deep.stdout.strip(), b"0")
            # Checker optimization/generation are retired; deep mode is a task choice frozen on the Round
            # and reviews program results, so generated round programs are no longer recorded.
            retired = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                previous,
                "-Atc",
                "SELECT (SELECT count(*) FROM pg_tables WHERE schemaname='product' AND tablename IN ('checker_feedback','checker_optimizations'))"
                " + (SELECT count(*) FROM pg_proc WHERE proname IN ('retire_checker_optimizations','retire_checker_operator'))"
                " + (SELECT count(*) FROM pg_indexes WHERE indexname='optimization_notification_intent'),"
                " (SELECT count(*) FROM information_schema.columns WHERE table_schema='product' AND column_name='deep_mode' AND table_name IN ('audit_drafts','audit_rounds')),"
                " (SELECT count(*) FROM pg_tables WHERE schemaname='product' AND tablename='deep_round_programs'),"
                " (SELECT indexdef LIKE '%(round_id, file_version_id, charge, is_free_trial)%' FROM pg_indexes WHERE indexname='usage_events_file_slice_idx')",
            )
            self.schema.require(retired, "read deep mode schema")
            self.assertEqual(retired.stdout.strip(), b"0|2|0|t")

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

    def test_native_runner_stops_at_the_first_failed_migration(self):
        self.schema.require_postgres()
        database = "ppap_schema_fail_" + uuid.uuid4().hex[:8]
        scratch = "/tmp/ppap-migration-failure"
        try:
            self.schema.require(
                self.schema.psql(
                    self.schema.bootstrap_user(),
                    self.schema.bootstrap_password(),
                    "postgres",
                    "-c",
                    f"CREATE DATABASE {database} OWNER {self.schema.migrator_user()}",
                ),
                "create failure-test database",
            )
            setup = (
                f"rm -rf {scratch} && mkdir -p {scratch} && "
                f"printf '%s\\n' 'CREATE TABLE migration_before_failure(id integer);' > {scratch}/0001_before.sql && "
                f"printf '%s\\n' 'SELECT definitely_missing_migration_function();' > {scratch}/0002_fail.sql && "
                f"printf '%s\\n' 'CREATE TABLE migration_after_failure(id integer);' > {scratch}/0003_after.sql"
            )
            self.schema.require(self.schema.container_sh(setup), "prepare failing migration directory")

            failed = self.schema.apply(database, migrations_dir=scratch)
            self.assertNotEqual(failed.returncode, 0)

            before = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                database,
                "-Atc",
                "SELECT to_regclass('public.migration_before_failure') IS NOT NULL",
            )
            after = self.schema.psql(
                self.schema.migrator_user(),
                self.schema.migrator_password(),
                database,
                "-Atc",
                "SELECT to_regclass('public.migration_after_failure') IS NOT NULL",
            )
            self.schema.require(before, "check migration before failure")
            self.schema.require(after, "check migration after failure")
            self.assertEqual((before.stdout or b"").decode().strip(), "t")
            self.assertEqual((after.stdout or b"").decode().strip(), "f")
        finally:
            self.schema.container_sh(f"rm -rf {scratch}")
            self.schema.psql(
                self.schema.bootstrap_user(),
                self.schema.bootstrap_password(),
                "postgres",
                "-c",
                f"DROP DATABASE IF EXISTS {database} WITH (FORCE)",
            )
