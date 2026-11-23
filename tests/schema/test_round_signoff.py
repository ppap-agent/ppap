"""0096 (#357): a round is signed whole or not at all, its signing names a recorded action, and the migration runs again."""

from __future__ import annotations

import unittest
import uuid

import pytest


class RoundSignoffSchemaTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools):
        self.schema = schema_tools

    def test_a_round_is_signed_whole_by_a_recorded_action_and_the_migration_runs_again(self):
        self.schema.require_postgres()
        database = "ppap_schema_signoff_" + uuid.uuid4().hex[:8]
        migrator = (self.schema.migrator_user(), self.schema.migrator_password(), database)

        def sql(statement: str):
            return self.schema.psql(*migrator, "-Atc", statement)

        try:
            self.schema.require(
                self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {self.schema.migrator_user()}"),
                "create database",
            )
            for attempt in ("first", "again"):
                self.schema.require(self.schema.apply(database), f"apply every migration ({attempt})")
            tenant, user, round_id, action = (str(uuid.uuid4()) for _ in range(4))
            self.schema.require(
                sql(
                    f"INSERT INTO product.tenants (id, provider, external_key, display_name) VALUES ('{tenant}', 'feishu', 'signoff', 'Signoff');"
                    f"INSERT INTO product.users (id) VALUES ('{user}');"
                    f"INSERT INTO product.resources (id, tenant_id, kind) VALUES ('{round_id}', '{tenant}', 'audit_round');"
                    "INSERT INTO product.audit_rounds (id, tenant_id, created_by, idempotency_key, request_fingerprint, execution, result_attempt) "
                    f"VALUES ('{round_id}', '{tenant}', '{user}', '{round_id}', 'signoff', 'completed', 1);"
                    "INSERT INTO product.actions (id, tenant_id, user_id, channel, method, route, path) "
                    f"VALUES ('{action}', '{tenant}', '{user}', 'web', 'POST', '/rounds/{{round_id}}/signoff', '/rounds/{round_id}/signoff');"
                ),
                "save a round and its creator's signing action",
            )
            # Part of a signoff is none: when, by which action and what are set and cleared together.
            for partial in ("signed_at = now()", "signoff = '{}'", f"signoff_action_id = '{action}'", f"signed_at = now(), signoff = '[]', signoff_action_id = '{action}'"):
                refused = sql(f"UPDATE product.audit_rounds SET {partial} WHERE id = '{round_id}'")
                self.assertNotEqual(refused.returncode, 0, partial)
                self.assertIn(b"audit_rounds_signoff_whole", refused.stderr, partial)
            # The signing must be someone's recorded action.
            unknown = sql(f"UPDATE product.audit_rounds SET signed_at = now(), signoff = '{{}}', signoff_action_id = '{uuid.uuid4()}' WHERE id = '{round_id}'")
            self.assertNotEqual(unknown.returncode, 0)
            self.assertIn(b"audit_rounds_signoff_action_fk", unknown.stderr)
            self.schema.require(sql(f"UPDATE product.audit_rounds SET signed_at = now(), signoff = '{{}}', signoff_action_id = '{action}' WHERE id = '{round_id}'"), "sign")
            self.schema.require(sql(f"UPDATE product.audit_rounds SET signed_at = NULL, signoff = NULL, signoff_action_id = NULL WHERE id = '{round_id}'"), "withdraw")
            granted = sql(
                "SELECT bool_and(has_column_privilege('ppap_api', 'product.audit_rounds', column_name, 'UPDATE')) FROM unnest(ARRAY['signed_at', 'signoff', 'signoff_action_id']) AS column_name"
            )
            self.schema.require(granted, "read the API's privileges")
            self.assertEqual(granted.stdout.strip(), b"t")
        finally:
            self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
