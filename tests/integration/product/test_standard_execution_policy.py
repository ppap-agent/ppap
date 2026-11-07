"""Real acceptance/PG/Worker/history; presence results isolate policy from AI quality."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from api import create_app
from api.web_contract import Round
from documents.model_access import UNCONFIGURED_MODEL_IDENTITY
from fastapi.testclient import TestClient
from product import build_product_store
from product.fixtures import RULE_A
from product.settings import Settings
from pydantic import TypeAdapter
from worker import process_claimed


@pytest.fixture
def system(auth_store_factory, core_system):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_", "OCR_", "PADDLE_"))}
    with patch.dict(os.environ, keep, clear=True):
        settings = Settings(usage_charge_enabled=False, git_sha="a" * 40)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        core_system.adopt_presence(store)
        core_system.install(store)
        worker = build_product_store(settings, objects=store.objects, role="worker")
        with TestClient(create_app(settings, auth_store=store)) as client:
            assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
            yield client, worker, settings, core_system


def accept(client, core):
    source = core.workbook("unchanged original")
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


def snapshot(worker, round_id):
    from product.store.suites import SuiteStore

    work = worker.round_work(round_id)
    return SuiteStore(worker.settings, worker.objects, role="worker").read_snapshot(round_id, work.tenant_id)


def test_standard_policy_survives_replay_completion_and_new_configuration(system):
    client, worker, settings, core = system
    original, draft, command = accept(client, core)
    frozen = snapshot(worker, original["id"])
    assert frozen.model_identity == UNCONFIGURED_MODEL_IDENTITY
    assert original["executor_revision"].startswith("checker-core-v1:")
    job = worker.claim_job("policy-worker")
    process_claimed(worker, job, "policy-worker")
    completed = client.get(f"/rounds/{original['id']}").json()
    TypeAdapter(Round).validate_python(completed)
    assert completed["execution"] == "completed"
    assert snapshot(worker, original["id"]) == frozen
    assert completed["coverage"]["checker_execution"]["input_digest"] == frozen.input_digest
    assert completed["findings"][0]["status"] == "PASS"
    settings.deep_reasoning_effort = "high"
    replay = client.post(f"/drafts/{draft}/accept", json=command)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == original["id"]
    assert snapshot(worker, original["id"]) == frozen
    assert worker.claim_job("duplicate") is None
    fresh, _, _ = accept(client, core)
    assert snapshot(worker, fresh["id"]).model_identity == frozen.model_identity
    assert fresh["executor_revision"] != original["executor_revision"]
    assert client.get(f"/rounds/{original['id']}").json() == completed
    # The old Controller configuration cannot execute this newly selected policy.
    result = process_claimed(worker, worker.claim_job("policy-worker"), "policy-worker")
    assert result["failure"]["code"] == "executor_revision_mismatch"


@pytest.mark.parametrize("field,value", [("deep_reasoning_effort", "high"), ("git_sha", "b" * 40), ("deep_sandbox_profile", "ppap")])
def test_standard_configuration_mismatch_is_terminal_without_execution_or_retry(system, field, value):
    client, worker, settings, core = system
    original, draft, command = accept(client, core)
    setattr(settings, field, value)
    job = worker.claim_job("policy-worker")
    result = process_claimed(worker, job, "policy-worker")
    assert result["failure"]["code"] == "executor_revision_mismatch"
    assert result["failure"]["retryable"] is False
    assert result["findings"] == []
    loaded = client.get(f"/rounds/{original['id']}").json()
    assert loaded["execution"] == "failed"
    assert snapshot(worker, original["id"]).runtime_revision == original["executor_revision"]
    assert loaded["executor_revision"] == original["executor_revision"]
    assert loaded["failure"] == result["failure"]
    assert loaded["findings"] == []
    assert worker.claim_job("no-automatic-retry") is None
    replay = client.post(f"/drafts/{draft}/accept", json=command)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == original["id"]
    assert snapshot(worker, original["id"]).runtime_revision == original["executor_revision"]


def test_legacy_label_is_not_backfilled_and_new_policy_cannot_be_silently_lost(system):
    client, worker, _settings, core = system
    legacy, _, _ = accept(client, core)
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
    assert after["execution"] == "failed"
    assert after["failure"]["code"] == "executor_revision_mismatch"
    assert after["executor_revision"] == "in-repo-engine-v1"
    assert "execution_policy" not in after

    current, _, _ = accept(client, core)
    with worker._conn() as conn:
        conn.execute("UPDATE engine.jobs SET payload = payload - 'checker_snapshot_digest' WHERE audit_round_id = %s", (current["id"],))
    result = process_claimed(worker, worker.claim_job("current-worker"), "current-worker")
    assert result["note"] == "deep_execution_failed"
    assert result["failure"]["retryable"] is False
    assert result["findings"] == []
    assert worker.claim_job("no-retry") is None


@pytest.mark.parametrize("field,value", [("MODEL_NAME", "ignored-legacy-model"), ("OCR_MODEL_NAME", "ignored-legacy-ocr")])
def test_legacy_model_environment_does_not_replace_personal_core_identity(system, field, value):
    client, worker, settings, core = system
    original, _, _ = accept(client, core)
    frozen = snapshot(worker, original["id"])
    setattr(settings, field, value)
    fresh, _, _ = accept(client, core)
    assert fresh["executor_revision"] == original["executor_revision"]
    assert snapshot(worker, fresh["id"]).model_identity == frozen.model_identity
    process_claimed(worker, worker.claim_job("fixed-core"), "fixed-core")
    result = client.get(f"/rounds/{original['id']}").json()
    assert result["execution"] == "completed"
    assert result["findings"][0]["status"] == "PASS"
    assert snapshot(worker, original["id"]) == frozen
