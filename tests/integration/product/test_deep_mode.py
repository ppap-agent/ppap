"""Core-backed Deep mode with real PostgreSQL authorization and persistence.

Transport and model source generation are controlled in-process. The real Core
manifest, checker execution and artifact storage run; this is not OS isolation,
UDS transport, provider quality or container acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch

import agent
import pytest
import worker
from agent.client import DeepClient, DeepError
from agent.core_program import CoreRuntime, run_audit_manifest, write_audit_manifest
from agent.mapping import spec_from_work
from agent.protocol import ExecuteRequest, RequestEnvelope
from agent.run import Controller, ExecutionContext
from agent.source import authorize_execution, object_file_source
from api import create_app
from audit.audits import (
    ANALYSIS_DEEP,
    ANALYSIS_STANDARD,
    DEEP_PROTOCOL_REVISION,
    EXECUTOR_DEEP,
    EXECUTOR_STANDARD,
    STANDARD_EXECUTOR_REVISION,
    ClaimedJob,
    frozen_executor,
    job_payload,
    request_fingerprint,
)
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest, runtime_revision
from audit.standard_suites import library_revision
from documents.model_access import UNCONFIGURED_MODEL_IDENTITY
from fastapi.testclient import TestClient
from product import build_product_store
from product.capabilities import report
from product.db import connect
from product.feishu import UserTokens
from product.fixtures import RULE_A, RULE_B
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.storage import S3Objects
from product.store.checker_development import CheckerDevelopmentStore
from product.store.suites import SuiteStore
from pydantic import ValidationError
from worker import process_claimed
from worker.deep_controller import open_store

# This source checks only the exact synthetic fixture bytes. It is deliberately
# not a PPAP business checker or a replacement for provider-semantic acceptance.
FIXTURE_CHECKER = """from pathlib import Path
from types import SimpleNamespace
from audit.contracts import AuditResultItem

def create_audit(audit_file, rule, group_context):
    def check(clause):
        matches = Path(audit_file.file_url).read_bytes() == b"target-bytes"
        return AuditResultItem(classification="fixture", item="Fixture bytes", priority="P0",
                               status="PASS" if matches else "NG", suggestions="Synthetic byte check",
                               clause_id=clause, checks_performed=1)
    return SimpleNamespace(checks=[SimpleNamespace(rule_item=SimpleNamespace(id=row["id"]),
                           check=lambda clause=row["id"]: check(clause)) for row in rule.rules])
"""


class ControlledSourceRunner:
    """Supply fixture source; production development trials still run it."""

    wall_seconds = 100
    input_tokens = output_tokens = 0

    def __init__(self, _grant):
        pass

    def run(self, work, prompt, schema, deadline, **kwargs):
        return schema(entrypoint="checker:create_audit", files={"checker.py": FIXTURE_CHECKER})


class ControlledCoreGuest:
    """Run the production manifest without claiming a guest OS boundary."""

    def run(self, job, inputs, work, deadline, cancelled):
        identity = json.loads((inputs / "manifest.json").read_text())["snapshot"]["model_identity"]
        grant = work / "controlled-model-grant"
        grant.mkdir()
        (grant / "model.json").write_text(
            json.dumps(
                {
                    "base_url": "http://127.0.0.1:1/v1",
                    "token": "synthetic-unused-grant-1234567890",
                    **identity,
                    "wall_seconds": 100,
                    "max_turns": 20,
                }
            )
        )
        with patch("agent.execution.guest_model.PiRunner", ControlledSourceRunner):
            run_audit_manifest(inputs, work, grant)


@pytest.mark.parametrize("guest_kind", ["deep", "shared"])
@pytest.mark.parametrize(
    "identity",
    [
        UNCONFIGURED_MODEL_IDENTITY,
        {"provider": "openai-compatible", "model": "frozen-model", "revision": "frozen-revision", "deployment": "a" * 64},
    ],
    ids=["unconfigured", "personal-endpoint"],
)
def test_controlled_guests_bind_full_frozen_model_identity(tmp_path, core_system, guest_kind, identity):
    sources = {"target": b"target-bytes"}
    programs = {"checker.py": FIXTURE_CHECKER.encode()}
    digest = checker_digest(programs)
    rules = {"type": "控制计划", "rules": [{"id": "presence", "rule_to_check": "Fixture bytes"}]}
    snapshot = ExecutionSnapshot(
        "tenant",
        "initiator",
        (FrozenInput("target", hashlib.sha256(sources["target"]).hexdigest(), "target", "target.txt"),),
        (FrozenTarget("target", "suite-revision", "fixture", "b" * 64, rules, RuleBinding("fixture", digest, {"presence": "checker:create_audit"})),),
        "runtime",
        "library",
        identity,
        round_id="round",
    )
    inputs, work = tmp_path / "inputs", tmp_path / "work"
    work.mkdir()
    mode = "deep" if guest_kind == "deep" else "standard"
    job = write_audit_manifest(inputs, snapshot, sources, {digest: programs}, analysis_mode=mode)
    guest = ControlledCoreGuest() if guest_kind == "deep" else core_system

    guest.run(job, inputs, work, monotonic() + 100, Event())

    result = json.loads((work / "result" / "bundle.json").read_text())
    assert result["accepted_input_digest"] == snapshot.input_digest
    assert [finding["status"] for finding in result["findings"]] == ["PASS"]
    assert json.loads((inputs / "manifest.json").read_text())["snapshot"]["model_identity"] == identity


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class DeepProtocolTests(unittest.TestCase):
    def test_envelope_and_spec_forbid_host_execution_fields(self):
        spec = {
            "protocol_revision": DEEP_PROTOCOL_REVISION,
            "round_id": "r1",
            "executor": "ppap-checker-core",
            "executor_revision": "core-runtime",
            "checker_snapshot_digest": "a" * 64,
            "files": [
                {
                    "file_version_id": "v1",
                    "role": "target",
                    "filename": "a.xlsx",
                    "sha256": "ab",
                }
            ],
        }
        ExecuteRequest.model_validate(spec)
        for extra in (
            {"command": "rm -rf /"},
            {"path": "/etc/passwd"},
            {"host_path": "/tmp"},
            {"mount": "/var/run/docker.sock"},
            {"image": "evil:latest"},
            {"env": {"K": "V"}},
        ):
            with self.assertRaises(ValidationError):
                ExecuteRequest.model_validate({**spec, **extra})
            with self.assertRaises(ValidationError):
                RequestEnvelope.model_validate({"op": "execute", "spec": spec, **extra})
        with self.assertRaises(ValidationError):
            RequestEnvelope.model_validate({"op": "shell", "spec": spec})

    def test_package_does_not_use_docker_socket(self):
        blob = ""
        for path in Path(agent.__file__).resolve().parent.rglob("*.py"):
            blob += path.read_text()
        blob += Path(worker.__file__).read_text()
        self.assertNotIn("/var/run/docker.sock", blob)
        self.assertNotIn("docker.sock", blob)
        self.assertNotIn("from docker", blob)

    def test_fingerprint_stays_items_only(self):
        items = [
            {"file_version_id": "00000000-0000-4000-8000-000000000001", "role": "target", "rule_version_id": RULE_A},
        ]
        self.assertEqual(request_fingerprint(items), request_fingerprint(items))
        self.assertEqual(frozen_executor("standard"), (EXECUTOR_STANDARD, STANDARD_EXECUTOR_REVISION))
        self.assertEqual(frozen_executor("deep"), (EXECUTOR_DEEP, DEEP_PROTOCOL_REVISION))
        payload = job_payload("r1", "deep")
        self.assertEqual(payload["executor"], EXECUTOR_DEEP)
        self.assertEqual(payload["executor_revision"], DEEP_PROTOCOL_REVISION)


class DeepModeTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store))
        self._controllers = {}

    def tearDown(self):
        self._env.stop()

    def _scalar(self, query: str, params=()):
        with self.store._conn() as conn:
            return conn.execute(query, params).fetchone()[0]

    def _execute(self, query: str, params=()) -> None:
        with self.store._conn() as conn:
            conn.execute(query, params)

    def _attempt_diagnostic(self, job_id: str, attempt: int) -> dict:
        return self._scalar(
            "SELECT diagnostic FROM engine.attempts WHERE job_id = %s AND attempt = %s",
            (job_id, attempt),
        )

    def _login(self) -> None:
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code, 200)

    def _upload(self, name: str, body: bytes) -> dict:
        created = self.client.post(
            "/files",
            json={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)},
        )
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        return ready.json()

    def _accept_deep(self, *uploads: dict, key: str = "deep-1", mode: str = ANALYSIS_DEEP, rule_version_id: str = RULE_A) -> str:
        draft = self.client.post("/drafts").json()["id"]
        items = []
        for index, row in enumerate(uploads):
            if len(uploads) > 1 and index == len(uploads) - 1:
                items.append({"file_version_id": row["id"], "role": "reference"})
            else:
                items.append({"file_version_id": row["id"], "role": "target", "rule_version_id": rule_version_id})
        saved = self.client.put(f"/drafts/{draft}", json={"items": items, "analysis_mode": mode})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["analysis_mode"], mode)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        return accepted.json()["id"]

    def _start_controller(self) -> str:
        # Only RPC transport and the external object SDK are substituted. Tenant,
        # lease, frozen input, byte digest and artifact publication checks are real.
        sdk_patch = patch("product.storage.s3_client")
        self.s3 = sdk_patch.start().return_value
        self.addCleanup(sdk_patch.stop)
        self.s3.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(self.store.objects.get(kwargs["Key"]))}
        self.runtime_calls: list[ExecuteRequest] = []
        controller_store = build_product_store(self.settings, objects=self.store.objects, role="worker")
        suites = SuiteStore(self.settings, self.store.objects, role="worker")
        runtime = CoreRuntime(
            ControlledCoreGuest(),
            lambda spec: suites.read_snapshot(spec.round_id, spec.tenant_id),
            suites.resolve_suite,
            save_round_artifacts=CheckerDevelopmentStore(controller_store).save_round_artifacts,
        )
        execute = runtime.execute

        def observed_execute(spec, files, *, context):
            self.runtime_calls.append(spec)
            return execute(spec, files, context=context)

        runtime.execute = observed_execute
        controller = Controller(
            runtime,
            object_file_source(self.settings, connect=connect, objects=S3Objects(self.settings)),
            executor_revision=runtime_revision(self.settings.deep_execution_revision(), library_revision()),
            authorize=authorize_execution(self.settings, connect=connect, open_store=open_store),
        )
        path = f"controlled-core-{len(self._controllers)}"
        self._controllers[path] = controller

        def transport(client, spec, *, cancelled=None, on_progress=None):
            context = ExecutionContext()
            if cancelled is not None:
                context.cancelled = cancelled
            if on_progress is not None:
                context.on_progress = on_progress
            # Serialize the request and response through production envelopes.
            response = self._controllers[client.socket_path].handle_raw(
                {"op": "execute", "spec": spec.model_dump(mode="json")},
                context,
            )
            if response["op"] == "error":
                raise DeepError(response["code"])
            from agent.protocol import ExecuteResponse

            return ExecuteResponse.model_validate(response["result"])

        transport_patch = patch.object(DeepClient, "execute", transport)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        return path

    def _claimed_spec(self, claimed: ClaimedJob) -> ExecuteRequest:
        work = self.store.round_work(claimed.round_id)
        self.assertIsNotNone(work)
        return spec_from_work(work, "ppap-checker-core", claimed.payload["executor_revision"], DEEP_PROTOCOL_REVISION, checker_snapshot_digest=claimed.payload["checker_snapshot_digest"]).model_copy(
            update={"job_id": claimed.id, "attempt": claimed.attempt, "model_identity": dict(claimed.payload["model_identity"])}
        )

    def test_default_draft_is_standard(self):
        self._login()
        draft = self.client.post("/drafts")
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["analysis_mode"], ANALYSIS_STANDARD)

    def test_core_persists_generated_findings_and_artifact(self):
        # Controlled source checks fixture bytes, not PPAP semantic audit quality.
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.txt", b"reference-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, reference, key="deep-happy")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["analysis_mode"], ANALYSIS_DEEP)
        self.assertEqual(body["executor"], "ppap-checker-core")
        self.assertEqual(body["executor_revision"], runtime_revision(self.settings.deep_execution_revision(), library_revision()))
        work = self.store.round_work(round_id)
        self.assertEqual(work.analysis_mode, ANALYSIS_DEEP)
        self.assertTrue(work.inputs[0].sha256)
        self.assertFalse(hasattr(worker, "evaluate_round"))
        self.assertFalse(hasattr(worker, "engine_runner"))
        claimed = self.store.claim_job("deep-worker")
        result = process_claimed(self.store, claimed, "deep-worker")
        self.assertEqual(result["note"], "checker_core_audit")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "completed")
        self.assertEqual({row["status"] for row in loaded["findings"]}, {"PASS"}, loaded["findings"])
        self.assertTrue(all(row["locator"]["verification_status"] == "unverified" for row in loaded["findings"]))
        self.assertEqual(loaded["coverage"]["checker_execution"]["input_digest"], claimed.payload["checker_snapshot_digest"])
        self.assertEqual(self._scalar("SELECT count(*) FROM product.checker_round_artifacts WHERE round_id=%s", (round_id,)), 1)
        self.assertIn("report.xlsx", loaded["coverage"]["checked"])
        self.assertTrue(any("context.txt" in row for row in loaded["coverage"]["unchecked"]))
        diagnostic = self._attempt_diagnostic(claimed.id, claimed.attempt)
        self.assertEqual(diagnostic["executor"], "ppap-checker-core")
        self.assertIsNone(loaded["failure"])
        self.assertEqual(diagnostic["usage"], {})

    def test_controlled_checker_reads_the_original_instead_of_returning_canned_pass(self):
        self._login()
        target = self._upload("report.xlsx", b"different-target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, key="deep-nonmatching-fixture")
        claimed = self.store.claim_job("deep-worker")
        process_claimed(self.store, claimed, "deep-worker")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "completed")
        self.assertEqual({row["status"] for row in loaded["findings"]}, {"NG"})
        self.assertEqual(self._scalar("SELECT count(*) FROM product.checker_round_artifacts WHERE round_id=%s", (round_id,)), 1)

    def test_runtime_unavailable_does_not_fall_back_to_standard(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept_deep(target, key="deep-unavailable")
        with patch("subprocess.Popen", side_effect=AssertionError("local runtime")) as start_process:
            claimed = self.store.claim_job("deep-worker")
            result = process_claimed(self.store, claimed, "deep-worker")
        start_process.assert_not_called()
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["failure"]["code"], "deep_runtime_unavailable")
        self.assertTrue(result["failure"]["retryable"])
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "failed")
        self.assertEqual(loaded["analysis_mode"], ANALYSIS_DEEP)
        self.assertEqual(loaded["findings"], [])
        self.assertEqual(loaded["failure"], result["failure"])

    def test_controller_rejects_changed_identity_and_frozen_inputs_before_object_reads(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.txt", b"reference-bytes")
        round_id = self._accept_deep(target, reference, key="deep-authorized")
        claimed = self.store.claim_job("deep-worker")
        spec = self._claimed_spec(claimed)
        original = spec.model_dump()

        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "carol"}).status_code, 200)
        other_file = self._upload("other-tenant.xlsx", b"other-tenant-bytes")
        other_round = self._accept_deep(other_file, key="deep-other-tenant", rule_version_id=RULE_B)
        other_claimed = self.store.claim_job("other-worker")
        other_spec = self._claimed_spec(other_claimed)
        client = DeepClient(self._start_controller())
        target_spec = next(row for row in spec.files if row.role == "target")
        reference_spec = next(row for row in spec.files if row.role == "reference")

        changed_targets = {
            "other tenant file": {"file_version_id": other_file["id"], "file_id": other_file["file_id"]},
            "digest": {"sha256": "0" * 64},
            "role": {"role": "reference"},
            "rule version": {"rule_version_id": other_file["id"]},
            "rule body": {"rule_body": {"kind": "other"}},
            "rule execution": {"rule_execution": {"profile": "other"}},
            "rule source": {"rule_source_file_version_id": reference["id"]},
        }
        requests = {
            "tenant": spec.model_copy(update={"tenant_id": other_spec.tenant_id}),
            "initiator": spec.model_copy(update={"initiating_user_id": other_spec.initiating_user_id}),
            "snapshot": spec.model_copy(update={"checker_snapshot_digest": "0" * 64}),
            "missing model identity": spec.model_copy(update={"model_identity": {}}),
            "mode": spec.model_copy(update={"analysis_mode": "standard"}),
            "invalid identity": spec.model_copy(update={"tenant_id": "not-a-uuid"}),
            "round": spec.model_copy(update={"round_id": other_round}),
            "job": spec.model_copy(update={"job_id": other_claimed.id}),
            "attempt": spec.model_copy(update={"attempt": claimed.attempt + 1}),
            "missing reference": spec.model_copy(update={"files": [target_spec]}),
            "extra file": spec.model_copy(update={"files": [*spec.files, other_spec.files[0]]}),
            "reference identity": spec.model_copy(update={"files": [target_spec, reference_spec.model_copy(update={"file_version_id": other_file["id"]})]}),
        }
        for name, changes in changed_targets.items():
            files = [row.model_copy(update=changes) if row.role == "target" else row for row in spec.files]
            requests[name] = spec.model_copy(update={"files": files})
        for field in ("provider", "model", "revision", "deployment"):
            identity = {**spec.model_identity, field: "f" * 64 if field == "deployment" else "changed"}
            requests[f"model {field}"] = spec.model_copy(update={"model_identity": identity})
        for name, changed in requests.items():
            with self.subTest(name=name):
                with self.assertRaises(DeepError) as caught:
                    client.execute(changed)
                self.assertEqual(caught.exception.code, "deep_input_unauthorized")
                self.s3.get_object.assert_not_called()
                self.assertEqual(self.runtime_calls, [])

        self.assertEqual(spec.model_dump(), original)
        result = client.execute(spec)
        self.assertEqual(result.round_id, round_id)
        self.assertEqual(result.files, spec.files)
        self.assertEqual({row.status for row in result.findings}, {"PASS"})
        self.assertEqual(self.s3.get_object.call_count, 2)
        self.assertEqual(self.runtime_calls, [spec])

    def test_controller_checks_stored_bytes_against_the_authorized_frozen_digest(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self._accept_deep(target, key="deep-corrupted-object")
        claimed = self.store.claim_job("deep-worker")
        spec = self._claimed_spec(claimed)
        client = DeepClient(self._start_controller())
        original_get = self.s3.get_object.side_effect
        self.s3.get_object.side_effect = lambda **_kwargs: {"Body": BytesIO(b"changed-object-bytes")}
        with self.assertRaises(DeepError) as caught:
            client.execute(spec)
        self.assertEqual(caught.exception.code, "deep_integrity_mismatch")
        self.s3.get_object.assert_called_once()
        self.assertEqual(self.runtime_calls, [])

        self.s3.get_object.side_effect = original_get
        result = client.execute(spec)
        self.assertEqual(result.files, spec.files)
        self.assertEqual(self.runtime_calls, [spec])

    def test_executor_revision_mismatch_requires_resubmit(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, key="deep-revision")
        frozen = runtime_revision(self.settings.deep_execution_revision(), library_revision())
        self.settings.deep_executor_revision = "ppap-deep-v999"
        claimed = self.store.claim_job("deep-worker")
        result = process_claimed(self.store, claimed, "deep-worker")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["failure"]["code"], "executor_revision_mismatch")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "failed")
        self.assertEqual(loaded["executor_revision"], frozen)
        self.assertEqual(loaded["failure"], result["failure"])

    def test_policy_change_needs_a_new_round_and_keeps_completed_history(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        first_id = self._accept_deep(target, key="policy-a")
        process_claimed(self.store, self.store.claim_job("policy-worker"), "policy-worker")
        first = self.client.get(f"/rounds/{first_id}").json()
        self.assertEqual(first["execution"], "completed")
        old_revision = first["executor_revision"]

        waiting_id = self._accept_deep(target, key="policy-a-waiting")
        self.settings.git_sha = "b" * 40
        self.settings.deep_controller_socket = self._start_controller()
        result = process_claimed(self.store, self.store.claim_job("policy-worker"), "policy-worker")
        self.assertEqual(result["failure"]["code"], "executor_revision_mismatch")
        waiting = self.client.get(f"/rounds/{waiting_id}").json()
        self.assertEqual(waiting["executor_revision"], old_revision)
        self.assertEqual(waiting["execution"], "failed")

        new_id = self._accept_deep(target, key="policy-b")
        process_claimed(self.store, self.store.claim_job("policy-worker"), "policy-worker")
        current = self.client.get(f"/rounds/{new_id}").json()
        self.assertEqual(current["execution"], "completed")
        self.assertNotEqual(current["executor_revision"], old_revision)
        historical = self.client.get(f"/rounds/{first_id}").json()
        self.assertEqual(historical["executor_revision"], old_revision)
        self.assertEqual(historical["findings"], first["findings"])
        self.assertEqual(historical["execution"], "completed")

    def test_acceptance_replay_keeps_the_original_policy_when_current_config_is_invalid(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        draft_id = self.client.post("/drafts").json()["id"]
        saved = self.client.put(f"/drafts/{draft_id}", json={"analysis_mode": "deep", "items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]})
        self.assertEqual(saved.status_code, 200, saved.text)
        command = {"idempotency_key": "frozen-policy-replay"}
        accepted = self.client.post(f"/drafts/{draft_id}/accept", json=command)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        original = accepted.json()

        self.settings.git_sha = ""
        replayed = self.client.post(f"/drafts/{draft_id}/accept", json=command)

        self.assertEqual(replayed.status_code, 201, replayed.text)
        self.assertEqual(replayed.json()["id"], original["id"])
        self.assertEqual(replayed.json()["executor_revision"], original["executor_revision"])
        claimed = self.store.claim_job("replay-reader")
        self.assertEqual(claimed.round_id, original["id"])
        self.assertIsNone(self.store.claim_job("second-reader"))

    def test_technical_retry_fencing_rejects_stale_attempt(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, key="deep-fence")
        first = self.store.claim_job("w1")
        self.assertEqual(first.attempt, 1)
        self.assertIsNone(self.store.claim_job("w2"))
        self._execute(
            "UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (first.id,),
        )
        controller = DeepClient(self.settings.deep_controller_socket)
        first_spec = self._claimed_spec(first)
        with self.assertRaises(DeepError) as expired:
            controller.execute(first_spec)
        self.assertEqual(expired.exception.code, "deep_input_unauthorized")
        self.s3.get_object.assert_not_called()
        self.assertEqual(self.runtime_calls, [])
        second = self.store.claim_job("w2")
        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        with self.assertRaises(DeepError) as stale_attempt:
            controller.execute(first_spec)
        self.assertEqual(stale_attempt.exception.code, "deep_input_unauthorized")
        self.s3.get_object.assert_not_called()
        self.assertEqual(self.runtime_calls, [])
        rejected = self.store.complete_job(
            first,
            "w1",
            {
                "findings": [],
                "coverage": {"checked": [], "unchecked": [], "locator_gap": False},
                "note": "deep_lease_lost",
                "failure": {"code": "deep_lease_lost", "message": "stale", "retryable": False},
            },
            outcome="failed",
        )
        self.assertFalse(rejected)
        process_claimed(self.store, second, "w2")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["result_attempt"], 2)
        self.assertEqual(body["findings"][0]["status"], "PASS")
        self.assertIsNone(body["failure"])
        stale = ClaimedJob(first.id, first.kind, first.payload, first.attempt, first.round_id)
        self.assertEqual(stale.round_id, round_id)

    def test_progress_uses_current_lease_and_survives_failure_completion(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept_deep(target, key="deep-progress")
        first = self.store.claim_job("w1")
        progress = {"phase": "checking", "completed_count": 1, "total_count": 3}
        self.assertFalse(self.store.audit_progress(first, "w2", progress))
        self.assertTrue(self.store.audit_progress(first, "w1", progress))
        running = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(running["progress"]["phase"], "checking")
        self.assertEqual(running["progress"]["completed_count"], 1)
        self.assertIsNotNone(running["progress"]["updated_at"])

        self._execute(
            "UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (first.id,),
        )
        self.assertFalse(self.store.audit_progress(first, "w1", {"phase": "validating"}))
        second = self.store.claim_job("w2")
        self.assertEqual(second.attempt, 2)
        self.assertFalse(self.store.audit_progress(first, "w1", {"phase": "validating"}))
        self.assertTrue(self.store.audit_progress(second, "w2", {"phase": "validating"}))
        failure = {"code": "deep_timeout", "message": "timeout", "retryable": False}
        self.assertTrue(
            self.store.complete_job(
                second,
                "w2",
                {"findings": [], "coverage": {}, "failure": failure, "usage": {"input_tokens": 7}},
                outcome="failed",
            )
        )
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["findings"], [])
        self.assertEqual(loaded["progress"]["phase"], "validating")
        self.assertEqual(loaded["failure"], failure)
        diagnostic = self._attempt_diagnostic(second.id, second.attempt)
        self.assertEqual(diagnostic["usage"], {"input_tokens": 7})
        self.assertFalse(self.store.audit_progress(second, "w2", {"phase": "validating"}))

    def test_same_key_different_mode_is_idempotency_conflict(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        first_draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{first_draft}",
            json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        first = self.client.post(f"/drafts/{first_draft}/accept", json={"idempotency_key": "same-key"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["analysis_mode"], ANALYSIS_STANDARD)
        other = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{other}",
            json={
                "items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}],
                "analysis_mode": ANALYSIS_DEEP,
            },
        )
        conflict = self.client.post(f"/drafts/{other}/accept", json={"idempotency_key": "same-key"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"], "idempotency_conflict")

    def test_mode_freezes_on_accept_and_resubmit_inherits_until_draft_changes(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept_deep(target, key="deep-freeze")
        nxt = self.client.post("/drafts", json={"predecessor_id": round_id}).json()
        self.assertEqual(nxt["analysis_mode"], ANALYSIS_DEEP)
        saved = self.client.put(
            f"/drafts/{nxt['id']}",
            json={
                "items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": RULE_A}],
                "analysis_mode": ANALYSIS_STANDARD,
            },
        )
        self.assertEqual(saved.json()["analysis_mode"], ANALYSIS_STANDARD)
        second = self.client.post(f"/drafts/{nxt['id']}/accept", json={"idempotency_key": "deep-freeze-2"}).json()
        self.assertEqual(second["analysis_mode"], ANALYSIS_STANDARD)
        original = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(original["analysis_mode"], ANALYSIS_DEEP)

    def test_invalid_analysis_mode_is_rejected(self):
        self._login()
        draft = self.client.post("/drafts").json()["id"]
        blocked = self.client.put(f"/drafts/{draft}", json={"analysis_mode": "semantic"})
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["error"], "invalid_analysis_mode")

    def test_core_ready_does_not_require_deep(self):
        self.assertEqual(report(self.settings)["deep"], {"state": "disabled"})
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.settings.deep_controller_socket = "/tmp/ppap-deep.sock"
        self.assertEqual(report(self.settings)["deep"], {"state": "configured"})
        self.assertEqual(set(report(self.settings)["deep"]), {"state"})

    def test_production_does_not_offer_placeholder_deep(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_PG_MIGRATOR_PASSWORD="prod-migrator",
            PPAP_PG_BOOTSTRAP_PASSWORD="prod-boot",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="https://llm.example",
            LLM_MODEL="x",
            PPAP_WEB_URL="https://app.example",
            PPAP_DEEP_CONTROLLER_SOCKET="/tmp/ppap-deep.sock",
        ):
            settings = Settings()
        self.assertFalse(settings.deep_offered())
        self.assertFalse(settings.public_config()["deep_offered"])
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.auth_store(settings)))
        self.assertFalse(client.get("/public-config").json()["deep_offered"])
        store = self.auth_store(settings)
        store.seed_isolated_dev()
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, store))
        principal = store.principal_for_fixture("alice")
        now = datetime.now(timezone.utc)
        raw = store.create_session(principal, UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7)))
        client.cookies.set(SESSION_COOKIE, raw)
        draft = client.post("/drafts").json()["id"]
        blocked = client.put(f"/drafts/{draft}", json={"analysis_mode": ANALYSIS_DEEP})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "deep_not_available")
        saved = client.put(f"/drafts/{draft}", json={"analysis_mode": ANALYSIS_STANDARD})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["analysis_mode"], ANALYSIS_STANDARD)

    def test_production_offers_deep_when_enabled(self):
        with _clean_env(
            PPAP_PROFILE="production",
            PPAP_PG_API_PASSWORD="prod-api",
            PPAP_PG_WORKER_PASSWORD="prod-worker",
            PPAP_PG_MIGRATOR_PASSWORD="prod-migrator",
            PPAP_PG_BOOTSTRAP_PASSWORD="prod-boot",
            PPAP_S3_ACCESS_KEY="prod-access",
            PPAP_S3_SECRET_KEY="prod-secret",
            PPAP_S3_ENDPOINT="https://objects.example.internal",
            LLM_BASE_URL="https://llm.example",
            LLM_MODEL="x",
            PPAP_WEB_URL="https://app.example",
            PPAP_DEEP_CONTROLLER_SOCKET="/tmp/ppap-deep.sock",
            PPAP_DEEP_CUSTOMER_ENABLED="true",
        ):
            settings = Settings()
        self.assertTrue(settings.deep_offered())
        client = TestClient(create_app(settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.auth_store(settings)))
        self.assertTrue(client.get("/public-config").json()["deep_offered"])

    def test_isolated_dev_still_offers_deep(self):
        self._login()
        self.assertTrue(self.settings.deep_offered())
        self.assertTrue(self.client.get("/public-config").json()["deep_offered"])
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(f"/drafts/{draft}", json={"analysis_mode": ANALYSIS_DEEP})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["analysis_mode"], ANALYSIS_DEEP)


if __name__ == "__main__":
    unittest.main()
