"""Model call records (#360) with real PostgreSQL: an attempt keeps its record counts, and the operator view reads them."""

from __future__ import annotations

import os
from unittest.mock import patch

from api import create_app
from fastapi.testclient import TestClient
from product.fixtures import RULE_A
from product.identity import SESSION_COOKIE
from product.model_calls import read_round, write_attempt_summary
from product.settings import Settings
from worker import process_claimed


def test_an_attempt_keeps_its_record_counts_through_completion(auth_store_factory, core_system, audit_round):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    with patch.dict(os.environ, {**environment, "FEISHU_APP_ID": "cli_test", "FEISHU_APP_SECRET": "secret", "PPAP_BUILD_SHA": "a" * 40}, clear=True):
        settings = Settings(usage_charge_enabled=False)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        core_system.adopt_presence(store)
        core_system.install(store)
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, store))
        assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
        body = core_system.workbook("source")
        created = client.post("/files", json={"original_filename": "control.xlsx", "content_type": "application/octet-stream", "byte_size": len(body)}).json()
        assert client.put(f"/files/{created['file_id']}/versions/{created['id']}/blob", content=body).status_code == 200
        assert client.post(f"/files/{created['file_id']}/versions/{created['id']}/finalize").status_code == 200
        principal = store.load_session(client.cookies.get(SESSION_COOKIE)).principal
        round_id = audit_round(store, principal, {created["id"]: RULE_A})["id"]
        while (claimed := store.claim_job("test-worker")).kind != "audit":
            process_claimed(store, claimed, "test-worker")

        # The Controller writes the counts as the worker role, before the Worker completes the attempt.
        counts = {"recorded": 2, "failed": 0, "missing": 0, "errors": []}
        write_attempt_summary(settings, claimed.id, claimed.attempt, counts)
        assert read_round(settings, round_id) == (principal.tenant_id, [{"attempt": 1, "outcome": None, "summary": counts, "model_requests": None}])
        process_claimed(store, claimed, "test-worker")
        assert read_round(settings, round_id) == (principal.tenant_id, [{"attempt": 1, "outcome": "succeeded", "summary": counts, "model_requests": None}])
