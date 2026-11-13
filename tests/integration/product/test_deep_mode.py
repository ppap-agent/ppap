"""Core Controller authorization and fencing with real PostgreSQL.

Transport is controlled in-process. The real Core manifest, adopted checker
execution and leased persistence run; this is not OS isolation, UDS transport,
provider quality or container acceptance. There is one kind of audit (#322).
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from io import BytesIO
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
from agent.client import DeepClient, DeepError
from agent.core_program import CoreRuntime, run_audit_manifest, write_audit_manifest
from agent.execution.guest_model import ModelGrant
from agent.mapping import spec_from_work
from agent.protocol import ExecuteRequest, RequestEnvelope
from agent.run import Controller, ExecutionContext
from agent.source import authorize_execution, object_file_source
from api import create_app
from audit.audits import DEEP_PROTOCOL_REVISION, ClaimedJob
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest, runtime_revision
from audit.standard_suites import library_revision
from fastapi.testclient import TestClient
from product.db import connect
from product.fixtures import RULE_A, RULE_B
from product.settings import Settings
from product.storage import S3Objects
from product.store.suites import SuiteStore
from pydantic import ValidationError
from worker import process_claimed
from worker.deep_controller import open_store

# This source checks only the exact synthetic fixture bytes. It is deliberately
# not a PPAP business checker or a replacement for provider-semantic acceptance.
FIXTURE_CHECKER = """from audit.contracts import AuditResultItem

def check(context):
    matches = context.sources[context.audit_file.audit_id] == b"target-bytes"
    return AuditResultItem(classification="fixture", item="Fixture bytes", priority="P0",
                           status="PASS" if matches else "NG", suggestions="Synthetic byte check",
                           clause_id=context.clause_id, checks_performed=1)
"""


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
                    **ModelGrant.fields_for(identity),
                    "wall_seconds": 100,
                }
            )
        )
        run_audit_manifest(inputs, work, grant)


@pytest.mark.parametrize(
    "identity",
    [
        {"source": "platform", "provider": "responses-compatible", "model": "unavailable", "vision_model": "unavailable", "coding_model": "unavailable", "revision": "0" * 64, "deployment": "0" * 64},
        {"source": "tenant", "provider": "responses-compatible", "model": "text-model", "vision_model": "vision-model", "coding_model": "coding-model", "revision": "a" * 64, "deployment": "b" * 64},
    ],
    ids=["platform-unconfigured", "tenant-key"],
)
def test_controlled_guests_bind_full_frozen_model_identity(tmp_path, core_system, identity):
    sources = {"target": b"target-bytes"}
    programs = {"checker.py": FIXTURE_CHECKER.encode()}
    digest = checker_digest(programs)
    rules = {"type": "控制计划", "rules": [{"id": "presence", "rule_to_check": "Fixture bytes"}]}
    snapshot = ExecutionSnapshot(
        "tenant",
        "initiator",
        (FrozenInput("target", hashlib.sha256(sources["target"]).hexdigest(), "target", "target.txt"),),
        (FrozenTarget("target", "suite-revision", "fixture", "b" * 64, rules, RuleBinding("fixture", digest, {"presence": "checker:check"})),),
        "runtime",
        "library",
        identity,
        round_id="round",
    )
    inputs, work = tmp_path / "inputs", tmp_path / "work"
    work.mkdir()
    job = write_audit_manifest(inputs, snapshot, sources, {digest: programs})

    core_system.run(job, inputs, work, monotonic() + 100, Event())

    result = json.loads((work / "result" / "bundle.json").read_text())
    assert result["accepted_input_digest"] == snapshot.input_digest
    assert [finding["status"] for finding in result["findings"]] == ["PASS"]
    assert json.loads((inputs / "manifest.json").read_text())["snapshot"]["model_identity"] == identity


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
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
        with self.assertRaises(ValidationError):
            ExecuteRequest.model_validate({**spec, "command": "rm -rf /"})
        with self.assertRaises(ValidationError):
            RequestEnvelope.model_validate({"op": "execute", "spec": spec, "mount": "/var/run/docker.sock"})
        with self.assertRaises(ValidationError):
            RequestEnvelope.model_validate({"op": "shell", "spec": spec})


class CoreControllerTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, core_system):
        self.auth_store = auth_store_factory
        self.core = core_system

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.core.adopt(self.store, RULE_A, FIXTURE_CHECKER)
        self.core.adopt(self.store, RULE_B, FIXTURE_CHECKER, subject="carol")
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

    def _accept(self, *uploads: dict, key: str = "core-1", rule_version_id: str = RULE_A) -> str:
        draft = self.client.post("/drafts").json()["id"]
        items = []
        for index, row in enumerate(uploads):
            if len(uploads) > 1 and index == len(uploads) - 1:
                items.append({"file_version_id": row["id"], "role": "reference"})
            else:
                items.append({"file_version_id": row["id"], "role": "target", "rule_version_id": rule_version_id})
        saved = self.client.put(f"/drafts/{draft}", json={"items": items})
        self.assertEqual(saved.status_code, 200, saved.text)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        return accepted.json()["id"]

    def _start_controller(self) -> str:
        # Only RPC transport and the external object SDK are substituted. Tenant,
        # lease, frozen input and byte digest checks are real.
        sdk_patch = patch("product.storage.s3_client")
        self.s3 = sdk_patch.start().return_value
        self.addCleanup(sdk_patch.stop)
        self.s3.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(self.store.objects.get(kwargs["Key"]))}
        self.runtime_calls: list[ExecuteRequest] = []
        suites = SuiteStore(self.settings, self.store.objects, role="worker")
        runtime = CoreRuntime(ControlledCoreGuest(), lambda spec: suites.read_snapshot(spec.round_id, spec.tenant_id), suites.resolve_suite)
        execute = runtime.execute

        def observed_execute(spec, files, *, context):
            self.runtime_calls.append(spec)
            return execute(spec, files, context=context)

        runtime.execute = observed_execute
        controller = Controller(
            runtime,
            object_file_source(self.settings, connect=connect, objects=S3Objects(self.settings)),
            executor_revision=runtime_revision(self.settings.deep_execution_revision(), library_revision()),
            # As in production: the Controller's configuration at start, with the frozen identity.
            executor_revision_for_request=lambda spec, started=self.settings.model_copy(): runtime_revision(started.deep_execution_revision(spec.model_identity), library_revision()),
            authorize=authorize_execution(self.settings, connect=connect, open_store=open_store),
        )
        path = f"controlled-core-{len(self._controllers)}"
        self._controllers[path] = controller

        def transport(client, spec, *, cancelled=None, on_progress=None, on_activity=None):
            context = ExecutionContext()
            if cancelled is not None:
                context.cancelled = cancelled
            if on_progress is not None:
                context.on_progress = on_progress
            if on_activity is not None:
                context.on_activity = on_activity
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

    def test_controller_rejects_changed_identity_and_frozen_inputs_before_object_reads(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.txt", b"reference-bytes")
        round_id = self._accept(target, reference, key="core-authorized")
        claimed = self.store.claim_job("deep-worker")
        spec = self._claimed_spec(claimed)
        original = spec.model_dump()

        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "carol"}).status_code, 200)
        other_file = self._upload("other-tenant.xlsx", b"other-tenant-bytes")
        other_round = self._accept(other_file, key="core-other-tenant", rule_version_id=RULE_B)
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
                # A changed model identity also selects another execution revision, so the
                # Controller may reject it as a revision mismatch first; both precede object reads.
                expected = {"deep_input_unauthorized", "executor_revision_mismatch"} if "model" in name else {"deep_input_unauthorized"}
                self.assertIn(caught.exception.code, expected)
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
        self._accept(target, key="core-corrupted-object")
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

    def test_technical_retry_fencing_rejects_stale_attempt(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept(target, key="core-fence")
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

    def test_progress_uses_current_lease_and_survives_failure_completion(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept(target, key="core-progress")
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


if __name__ == "__main__":
    unittest.main()
