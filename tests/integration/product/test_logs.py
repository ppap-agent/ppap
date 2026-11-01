"""Structured logs carry request/tenant/round/job/attempt ids, not customer content."""

from __future__ import annotations

import io
import json
import logging
import os
import unittest
from unittest.mock import patch

import pytest
from api import create_app
from audit.audits import ClaimedJob
from product.fixtures import TENANT_A
from product.logs import LOGGER, JsonFormatter, bind, emit, new_request_id, reset, round_id_from_path
from product.settings import Settings
from worker import process_claimed


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


def _json_logger() -> io.StringIO:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter("deadbeef"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    return buf


class LogTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def tearDown(self):
        reset(bind(request_id="", tenant_id="", round_id="", job_id="", attempt=""))
        LOGGER.handlers.clear()

    def test_json_line_keeps_correlation_ids_and_drops_secrets(self):
        buf = _json_logger()
        tokens = bind(request_id="req-12345678", tenant_id=TENANT_A, round_id="round-1", job_id="job-1", attempt="2")
        try:
            emit("http_request", path="/rounds/x", access_token="secret", authorization="Bearer x", status=200)
        finally:
            reset(tokens)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["event"], "http_request")
        self.assertEqual(payload["git_sha"], "deadbeef")
        self.assertEqual(payload["request_id"], "req-12345678")
        self.assertEqual(payload["tenant_id"], TENANT_A)
        self.assertEqual(payload["round_id"], "round-1")
        self.assertEqual(payload["job_id"], "job-1")
        self.assertEqual(payload["attempt"], "2")
        self.assertEqual(payload["path"], "/rounds/x")
        self.assertEqual(payload["status"], 200)
        blob = json.dumps(payload)
        self.assertNotIn("secret", blob)
        self.assertNotIn("Bearer", blob)

    def test_request_id_rejects_newlines(self):
        self.assertNotIn("\n", new_request_id("abc\nheader"))
        self.assertEqual(new_request_id("client-req-0001"), "client-req-0001")

    def test_round_id_comes_from_path_not_query(self):
        rid = "00000000-0000-4000-8000-0000000000aa"
        self.assertEqual(round_id_from_path(f"/rounds/{rid}/review"), rid)
        self.assertEqual(round_id_from_path(f"/me?round={rid}"), "")

    def test_http_request_sets_header_and_skips_health(self):
        with _clean_env(PPAP_BUILD_SHA="a" * 40, LOG_FORMAT="json"):
            settings = Settings()
        store = self.auth_store(settings)
        store.seed_isolated_dev()
        client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, store))
        buf = _json_logger()
        health = client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.headers.get("x-request-id"))
        self.assertEqual(buf.getvalue().strip(), "")
        minted = client.post("/isolated-dev/sessions", json={"subject": "alice"})
        self.assertEqual(minted.status_code, 200)
        me = client.get("/me")
        self.assertEqual(me.status_code, 200)
        lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        events = [row["event"] for row in lines]
        self.assertIn("http_request", events)
        me_log = next(row for row in lines if row.get("path") == "/me")
        self.assertEqual(me_log["tenant_id"], TENANT_A)
        self.assertEqual(me_log["request_id"], me.headers["x-request-id"])
        self.assertNotIn("?", me_log["path"])

    def test_worker_logs_job_attempt_round_and_tenant(self):
        with _clean_env(PPAP_BUILD_SHA="a" * 40):
            settings = Settings()
        store = self.auth_store(settings)
        job_id = "00000000-0000-4000-8000-0000000000b1"
        claimed = ClaimedJob(job_id, "heartbeat", {}, 3, None, TENANT_A)
        buf = _json_logger()
        process_claimed(store, claimed, "worker-1")
        lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        claimed_log = next(row for row in lines if row["event"] == "job_claimed")
        done = next(row for row in lines if row["event"] == "job_result_discarded")
        self.assertEqual(claimed_log["job_id"], job_id)
        self.assertEqual(claimed_log["attempt"], "3")
        self.assertEqual(claimed_log["tenant_id"], TENANT_A)
        self.assertEqual(done["outcome"], "failed")
        self.assertEqual(done["note"], "unsupported_job_kind")
        self.assertNotIn("job_completed", [row["event"] for row in lines])
        self.assertNotIn("findings", json.dumps(lines))


if __name__ == "__main__":
    unittest.main()
