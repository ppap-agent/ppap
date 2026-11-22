"""Real acceptance/PG/Worker/history; presence results isolate policy from AI quality."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from api import create_app
from api.web_contract import Round
from fastapi.testclient import TestClient
from product import build_product_store
from product.fixtures import RULE_A
from product.settings import Settings
from product.store.model_credentials import platform_selection
from pydantic import TypeAdapter
from worker import process_claimed


@pytest.fixture
def system(auth_store_factory, core_system, audit_round):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    with patch.dict(os.environ, keep, clear=True):
        settings = Settings(usage_charge_enabled=False, git_sha="a" * 40)
        store = auth_store_factory(settings)
        store.seed_isolated_dev()
        core_system.adopt_presence(store)
        core_system.install(store)
        worker = build_product_store(settings, objects=store.objects, role="worker")
        with TestClient(create_app(settings, auth_store=store)) as client:
            assert client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code == 200
            yield client, worker, settings, lambda: accept(client, store, core_system, audit_round)


def accept(client, store, core, audit_round):
    """A confirmed task's round, and a replay of that same confirmation."""
    source = core.workbook("unchanged original")
    response = client.post("/files", json={"original_filename": "original.xlsx", "byte_size": len(source)})
    assert response.status_code == 201, response.text
    version = response.json()
    path = f"/files/{version['file_id']}/versions/{version['id']}"
    assert client.put(path + "/blob", content=source).status_code == 200
    assert client.post(path + "/finalize").status_code == 200
    principal = store.principal_for_fixture("alice")
    accepted = audit_round(store, principal, {version["id"]: RULE_A})
    with store._conn() as conn:
        request_id = str(conn.execute("SELECT idempotency_key FROM product.audit_rounds WHERE id = %s", (accepted["id"],)).fetchone()[0])

    def replay():
        return store.confirm_audit_task(principal, accepted["source_draft_id"], request_id, 0)["round_id"]

    return accepted, replay


def snapshot(worker, round_id):
    from product.store.suites import SuiteStore

    work = worker.round_work(round_id)
    return SuiteStore(worker.settings, worker.objects, role="worker").read_snapshot(round_id, work.tenant_id)


def test_standard_policy_survives_replay_completion_and_new_configuration(system):
    client, worker, settings, accept = system
    original, replay = accept()
    frozen = snapshot(worker, original["id"])
    # Without its own key, the tenant freezes the platform selection.
    assert frozen.model_identity == platform_selection().identity
    assert original["executor_revision"].startswith("checker-core-v1:")
    job = worker.claim_job("policy-worker")
    process_claimed(worker, job, "policy-worker")
    # The task's report is the completed audit's own follow-up work.
    report = worker.claim_job("policy-worker")
    assert report.kind == "report_pdf"
    process_claimed(worker, report, "policy-worker")
    completed = client.get(f"/rounds/{original['id']}").json()
    TypeAdapter(Round).validate_python(completed)
    assert completed["execution"] == "completed"
    assert snapshot(worker, original["id"]) == frozen
    assert completed["coverage"]["checker_execution"]["input_digest"] == frozen.input_digest
    assert completed["findings"][0]["status"] == "PASS"
    settings.model_coding_reasoning_effort = "high"
    assert replay() == original["id"]
    assert snapshot(worker, original["id"]) == frozen
    assert worker.claim_job("duplicate") is None
    fresh, _ = accept()
    assert snapshot(worker, fresh["id"]).model_identity == frozen.model_identity
    assert fresh["executor_revision"] != original["executor_revision"]
    assert client.get(f"/rounds/{original['id']}").json() == completed
    # The old Controller configuration cannot execute this newly selected policy.
    result = process_claimed(worker, worker.claim_job("policy-worker"), "policy-worker")
    assert result["failure"]["code"] == "executor_revision_mismatch"


def test_standard_configuration_mismatch_is_terminal_without_execution_or_retry(system):
    client, worker, settings, accept = system
    original, replay = accept()
    settings.git_sha = "b" * 40
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
    assert replay() == original["id"]
    assert snapshot(worker, original["id"]).runtime_revision == original["executor_revision"]


def test_legacy_label_is_not_backfilled_and_new_policy_cannot_be_silently_lost(system):
    client, worker, _settings, accept = system
    legacy, _ = accept()
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

    current, _ = accept()
    with worker._conn() as conn:
        conn.execute("UPDATE engine.jobs SET payload = payload - 'checker_snapshot_digest' WHERE audit_round_id = %s", (current["id"],))
    result = process_claimed(worker, worker.claim_job("current-worker"), "current-worker")
    assert result["note"] == "deep_execution_failed"
    assert result["failure"]["retryable"] is False
    assert result["findings"] == []
    assert worker.claim_job("no-retry") is None


def test_legacy_model_environment_does_not_replace_the_frozen_identity(system):
    client, worker, settings, accept = system
    original, _ = accept()
    frozen = snapshot(worker, original["id"])
    settings.model_text_model = "ignored-new-model"
    fresh, _ = accept()
    assert fresh["executor_revision"] == original["executor_revision"]
    assert snapshot(worker, fresh["id"]).model_identity == frozen.model_identity
    process_claimed(worker, worker.claim_job("fixed-core"), "fixed-core")
    result = client.get(f"/rounds/{original['id']}").json()
    assert result["execution"] == "completed"
    assert result["findings"][0]["status"] == "PASS"
    assert snapshot(worker, original["id"]) == frozen
