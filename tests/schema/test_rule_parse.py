"""Actual PostgreSQL acceptance, lease and authorization boundaries for rule jobs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import unittest

import pytest
import uuid
from unittest.mock import patch



class RuleParseDatabaseTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _schema_support(self, schema_tools, memory_objects_type, sample_rule_body_factory):
        self.schema = schema_tools
        self.memory_objects_type = memory_objects_type
        self.rule_body = sample_rule_body_factory
    def test_idempotent_acceptance_unknown_lease_late_result_and_current_source_authorization(self):
        from fastapi.testclient import TestClient
        from ppap_api import create_app
        from ppap_product_store import AuthStore
        from ppap_core.db import connect
        from ppap_core.settings import Settings
        from ppap_worker import process_claimed

        self.schema.require_postgres()
        database = "ppap_rule_parse_" + uuid.uuid4().hex[:12]
        self.schema.require(self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {self.schema.migrator_user()}"), "create isolated rule parse DB")
        try:
            self.schema.require(self.schema.psql(self.schema.migrator_user(), self.schema.migrator_password(), database, "-f", str(self.schema.MIGRATIONS / "apply.sql")), "apply rule parse schema")
            settings = Settings(
                pg_database=database, pg_host=os.environ.get("PGHOST", "127.0.0.1"), pg_port=int(os.environ.get("PGPORT", "5432")),
                pg_migrator_user=self.schema.migrator_user(), pg_migrator_password=self.schema.migrator_password(),
            )
            objects = self.memory_objects_type()
            api = AuthStore(settings, objects)
            worker = AuthStore(settings, objects, role="worker")
            api.seed_isolated_dev()
            alice = api.principal_for_fixture("alice")
            content = json.dumps(self.rule_body()).encode()
            source = api.create_staging(alice, "rules.json", "application/json", len(content))
            api.put_blob(source, content)
            api.finalize_version(source)
            request_id = str(uuid.uuid4())

            def accept():
                return api.accept_rule_parse(alice, request_id, source.file_id, source.id)

            with ThreadPoolExecutor(max_workers=2) as pool:
                accepted = list(pool.map(lambda _: accept(), range(2)))
            self.assertEqual([row["id"] for row in accepted], [request_id, request_id])
            with worker._conn() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM engine.jobs").fetchone()[0], 1)
            with self.assertRaisesRegex(ValueError, "rule_parse_conflict"):
                api.accept_rule_parse(alice, request_id, source.file_id, source.id, "控制计划")
            job = worker.claim_job("owner")
            self.assertEqual(job.id, request_id)
            self.assertFalse(worker.renew_job_lease(job, "other"))
            self.assertTrue(worker.renew_job_lease(job, "owner"))
            with worker._conn() as conn:
                conn.execute("UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (job.id,))
            self.assertEqual(api.get_rule_parse(alice, job.id)["status"], "unknown")
            self.assertIsNone(worker.claim_job("second"))
            self.assertEqual(accept()["status"], "unknown")
            with patch("ppap_excel_audit_engine.checkers.client.AIClient", side_effect=AssertionError("unexpected external model call")):
                process_claimed(worker, job, "owner")
            result = api.get_rule_parse(alice, job.id)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["result"]["body"], self.rule_body())
            self.assertFalse(worker.complete_job(job, "other", {"error": "late"}, "failed"))
            self.assertEqual(api.get_rule_parse(alice, job.id), result)
            with worker._conn() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM engine.attempts WHERE job_id = %s", (job.id,)).fetchone()[0], 1)
            with TestClient(create_app(settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, api)) as client:
                for user in ("bob", "carol"):
                    client.cookies.clear()
                    self.assertEqual(client.post("/isolated-dev/sessions", json={"subject": user}).status_code, 200)
                    self.assertEqual(client.get("/rules/parse/" + job.id).status_code, 403)
                client.cookies.clear()
                client.post("/isolated-dev/sessions", json={"subject": "alice"})
                self.assertEqual(client.get("/rules/parse/" + job.id).status_code, 200)
                with connect(settings, "migrator") as conn:
                    conn.execute("DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s", (source.file_id, alice.user_id))
                self.assertEqual(client.get("/rules/parse/" + job.id).status_code, 403)
        finally:
            self.schema.require(self.schema.psql(self.schema.bootstrap_user(), self.schema.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop isolated rule parse DB")


if __name__ == "__main__":
    unittest.main()
