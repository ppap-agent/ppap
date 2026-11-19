"""P1 duration estimate: one file's standard audit time measured from this tenant's finished rounds."""

from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.settings import Settings
from product.store.durations import per_file_interval
from psycopg.types.json import Jsonb


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


def test_interval_is_median_to_p90_of_per_file_seconds():
    # One 10-file round of 600 s is ten files of 60 s each.
    assert per_file_interval([(600.0, 10)]) == {"low": 60.0, "high": 60.0}
    # Files 10..100 s in steps of 10: median 55, 90th percentile 91.
    assert per_file_interval([(float(seconds), 1) for seconds in range(10, 101, 10)]) == {"low": 55.0, "high": 91.0}
    assert per_file_interval([(60.0, 1)] * 9) is None
    assert per_file_interval([(60.0, 0)] * 20) is None


class AuditDurationEstimateTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))

    def tearDown(self):
        self._env.stop()

    def _login(self, subject: str) -> None:
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": subject}).status_code, 200)

    def _round(self, principal, files: int, seconds: float, *, deep: bool = False, execution: str = "completed", days_ago: int = 1) -> None:
        round_id, rule_id = str(uuid.uuid4()), str(uuid.uuid4())
        with self.store._conn() as conn:
            conn.execute("INSERT INTO product.resources(id, tenant_id, kind) VALUES (%s, %s, 'audit_round')", (round_id, principal.tenant_id))
            conn.execute(
                """INSERT INTO product.audit_rounds(id, tenant_id, created_by, idempotency_key, request_fingerprint, execution, result_attempt, deep_mode, created_at)
                VALUES (%s, %s, %s, %s, 'duration', %s, 1, %s, now() - make_interval(days => %s))""",
                (round_id, principal.tenant_id, principal.user_id, round_id, execution, deep, days_ago),
            )
            conn.execute("INSERT INTO product.rule_versions(id, tenant_id, title, body) VALUES (%s, %s, '规则', '{}')", (rule_id, principal.tenant_id))
            for index in range(files):
                file_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
                conn.execute("INSERT INTO product.resources(id, tenant_id, kind) VALUES (%s, %s, 'file')", (file_id, principal.tenant_id))
                conn.execute("INSERT INTO product.files(id, tenant_id, created_by, original_filename) VALUES (%s, %s, %s, %s)", (file_id, principal.tenant_id, principal.user_id, f"f{index}.xlsx"))
                conn.execute(
                    """INSERT INTO product.file_versions(id, file_id, tenant_id, original_filename, content_type, byte_size, sha256, object_key, status)
                    VALUES (%s, %s, %s, %s, 'application/octet-stream', 1, %s, %s, 'ready')""",
                    (version_id, file_id, principal.tenant_id, f"f{index}.xlsx", "0" * 64, version_id),
                )
                conn.execute(
                    "INSERT INTO product.round_inputs(round_id, file_version_id, role, rule_version_id, rule_title, rule_body) VALUES (%s, %s, 'target', %s, '规则', %s)",
                    (round_id, version_id, rule_id, Jsonb({})),
                )
            job_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO engine.jobs(id, tenant_id, audit_round_id, kind, status, attempt) VALUES (%s, %s, %s, 'audit', 'succeeded', 1)",
                (job_id, principal.tenant_id, round_id),
            )
            conn.execute(
                """INSERT INTO engine.attempts(id, job_id, attempt, worker_id, finished_at, outcome)
                SELECT %s, %s, 1, 'w', r.created_at + make_interval(secs => %s), 'succeeded' FROM product.audit_rounds r WHERE r.id = %s""",
                (str(uuid.uuid4()), job_id, seconds, round_id),
            )

    def test_requires_authentication(self):
        self.assertEqual(self.client.get("/audit-duration-estimate").status_code, 401)

    def test_too_few_files_gives_no_estimate(self):
        alice = self.store.principal_for_fixture("alice")
        self._round(alice, 9, 540)
        self._login("alice")
        response = self.client.get("/audit-duration-estimate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"per_file_seconds": None})

    def test_standard_rounds_of_the_tenant_only(self):
        alice = self.store.principal_for_fixture("alice")
        bob = self.store.principal_for_fixture("bob")
        carol = self.store.principal_for_fixture("carol")
        self.assertEqual(alice.tenant_id, bob.tenant_id)
        self.assertNotEqual(alice.tenant_id, carol.tenant_id)
        self._round(alice, 5, 300)  # 60 s per file
        self._round(bob, 5, 600)  # 120 s per file, same company
        # None of these count: deep mode, failed, older than the window, another company.
        self._round(alice, 10, 30000, deep=True)
        self._round(alice, 10, 30000, execution="failed")
        self._round(alice, 10, 30000, days_ago=120)
        self._round(carol, 10, 30000)

        self._login("alice")
        self.assertEqual(self.client.get("/audit-duration-estimate").json(), {"per_file_seconds": {"low": 90.0, "high": 120.0}})
        self._login("carol")
        self.assertEqual(self.client.get("/audit-duration-estimate").json(), {"per_file_seconds": {"low": 3000.0, "high": 3000.0}})
