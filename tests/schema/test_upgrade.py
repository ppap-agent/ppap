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
            # Two ZIP notifications of one Task are distinct intents. Full replay
            # must not rebuild an older index that collapses their identities.
            self.schema.require(
                self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    empty,
                    "-c",
                    "INSERT INTO engine.jobs(id,tenant_id,kind,payload,status) "
                    f"SELECT gen_random_uuid(),'{tenant_id}','feishu_notification',"
                    "jsonb_build_object('stage','zip_ready','task_id','replay-task','package_id',gen_random_uuid()::text),'queued' FROM generate_series(1,2)",
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
                "SELECT count(*) FROM engine.jobs WHERE kind='feishu_notification' AND payload->>'stage'='zip_ready'",
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
            # Least privilege of the runtime roles on official heads and provider credentials.
            privileges = {
                ("ppap_api", "official_suite_heads"): "SELECT",
                ("ppap_worker", "official_suite_heads"): "SELECT",
                ("ppap_worker", "provider_authorizations"): "",
            }
            for (role, table), expected in privileges.items():
                granted = self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    empty,
                    "-Atc",
                    "SELECT string_agg(p, ',' ORDER BY o) FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) WITH ORDINALITY AS t(p, o) "
                    f"WHERE has_table_privilege('{role}', 'product.{table}', p)",
                )
                self.schema.require(granted, f"read {role} privileges on {table}")
                self.assertEqual(granted.stdout.strip().decode(), expected, (role, table))

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

            # Rows written before the latest migration survive the upgrade.
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
                "SELECT ticket FROM product.app_tickets WHERE provider='feishu' AND app_id='upgrade-test-app'",
            )
            self.schema.require(ticket, "read preserved ticket")
            self.assertEqual(ticket.stdout.strip(), b"upgrade-test-ticket")

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
            # Replayed creations of retired tables end dropped again.
            self.assertTrue(
                names.isdisjoint(
                    {
                        "rule_formalizations",
                        "rule_formalization_operations",
                        "rule_formalization_confirmations",
                        "suite_upgrade_operations",
                        "suite_official_update_views",
                        "suite_candidate_operations",
                        "template_feedback",
                    }
                )
            )
            # Checker history import columns, triggers and functions end dropped again.
            for database in (empty, previous):
                history = self.schema.psql(
                    self.schema.migrator_user(),
                    self.schema.migrator_password(),
                    database,
                    "-Atc",
                    "SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema='product'"
                    " AND (table_name, column_name) IN (('round_inputs','checker_history'),('suite_revisions','history_only')))"
                    " + (SELECT count(*) FROM pg_proc WHERE pronamespace='product'::regnamespace AND proname IN ('check_history_import','check_history_suite','reject_history_release'))"
                    " + (SELECT count(*) FROM pg_trigger WHERE tgname IN ('check_history_import','check_history_suite','reject_history_release'))",
                )
                self.schema.require(history, f"read retired history objects in {database}")
                self.assertEqual(history.stdout.strip(), b"0", database)
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
