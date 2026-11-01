"""Real acceptance/PG/Worker/history; presence results isolate policy from AI quality."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from api import create_app
from api.web_contract import Round
from fastapi.testclient import TestClient
from product import build_product_store
from product.fixtures import RULE_A
from product.settings import Settings
from pydantic import TypeAdapter
from worker import process_claimed


@pytest.fixture
def system(auth_store_factory):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_", "OCR_", "PADDLE_"))}
    with patch.dict(os.environ, keep, clear=True):
        settings = Settings(usage_charge_enabled=False, git_sha="a" * 40)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        worker = build_product_store(settings, objects=store.objects, role="worker")
        with TestClient(create_app(settings, auth_store=store)) as client:
            assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
            yield client, worker, settings


def accept(client):
    source = b"unchanged original"
    response = client.post("/files", json={"original_filename": "original.xlsx", "byte_size": len(source)})
    assert response.status_code == 201, response.text
    version = response.json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=source).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    draft = client.post("/drafts").json()["id"]
    assert client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]}).status_code == 200
    command = {"idempotency_key": str(uuid.uuid4())}
    response = client.post(f"/drafts/{draft}/accept", json=command)
    assert response.status_code == 201, response.text
    return response.json(), draft, command


def test_standard_policy_survives_replay_completion_and_new_configuration(system):
    client, worker, settings = system
    original, draft, command = accept(client)
    frozen = original["execution_policy"]
    assert frozen["app_revision"] == "a" * 40
    assert original["executor_revision"].startswith("standard-v1:")
    job = worker.claim_job("policy-worker")
    process_claimed(worker, job, "policy-worker")
    completed = client.get(f"/rounds/{original['id']}").json()
    TypeAdapter(Round).validate_python(completed)
    assert completed["execution"] == "completed"
    assert completed["execution_policy"] == frozen
    assert completed["findings"][0]["status"] == "PASS"
    settings.MODEL_NAME = "new-model"
    replay = client.post(f"/drafts/{draft}/accept", json=command)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == original["id"]
    assert replay.json()["execution_policy"] == frozen
    assert worker.claim_job("duplicate") is None
    fresh, _, _ = accept(client)
    assert fresh["execution_policy"]["model"] == "new-model"
    assert fresh["executor_revision"] != original["executor_revision"]
    assert client.get(f"/rounds/{original['id']}").json() == completed
    process_claimed(worker, worker.claim_job("policy-worker"), "policy-worker")
    assert client.get(f"/rounds/{fresh['id']}").json()["execution"] == "completed"


@pytest.mark.parametrize("field,value", [("MODEL_NAME", "new-model"), ("git_sha", "b" * 40), ("OCR_MODEL_NAME", "new-ocr")])
def test_standard_configuration_mismatch_is_terminal_without_execution_or_retry(system, field, value):
    client, worker, settings = system
    original, draft, command = accept(client)
    setattr(settings, field, value)
    job = worker.claim_job("policy-worker")
    result = process_claimed(worker, job, "policy-worker")
    assert result["failure"] == {"code": "executor_revision_mismatch", "retryable": False}
    assert result["findings"] == []
    loaded = client.get(f"/rounds/{original['id']}").json()
    assert loaded["execution"] == "failed"
    assert loaded["execution_policy"] == original["execution_policy"]
    assert loaded["executor_revision"] == original["executor_revision"]
    assert loaded["failure"] == result["failure"]
    assert loaded["findings"] == []
    assert worker.claim_job("no-automatic-retry") is None
    replay = client.post(f"/drafts/{draft}/accept", json=command)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == original["id"]
    assert replay.json()["execution_policy"] == original["execution_policy"]


def test_legacy_label_is_not_backfilled_and_new_policy_cannot_be_silently_lost(system):
    client, worker, _settings = system
    legacy, _, _ = accept(client)
    # Seed the persisted shape used before this change. This is not a migration
    # of user history, nor a claim that the old configuration can be recovered.
    with worker._conn() as conn:
        conn.execute(
            "UPDATE engine.jobs SET payload = (payload - 'execution_policy') || '{\"executor_revision\":\"in-repo-engine-v1\"}'::jsonb WHERE audit_round_id = %s",
            (legacy["id"],),
        )
    before = client.get(f"/rounds/{legacy['id']}").json()
    assert "execution_policy" not in before
    process_claimed(worker, worker.claim_job("legacy-worker"), "legacy-worker")
    after = client.get(f"/rounds/{legacy['id']}").json()
    assert after["execution"] == "completed"
    assert after["executor_revision"] == "in-repo-engine-v1"
    assert "execution_policy" not in after

    current, _, _ = accept(client)
    with worker._conn() as conn:
        conn.execute("UPDATE engine.jobs SET payload = payload - 'execution_policy' WHERE audit_round_id = %s", (current["id"],))
    result = process_claimed(worker, worker.claim_job("current-worker"), "current-worker")
    assert result["failure"]["code"] == "executor_revision_mismatch"
    assert result["findings"] == []
    assert worker.claim_job("no-retry") is None
