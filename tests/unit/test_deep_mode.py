"""Native Deep analysis mode. Independent protocol; does not import ppap-agent."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from tests import pathsetup

ROOT = pathsetup.ROOT

from fastapi.testclient import TestClient

from ppap_core.audits import (
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
from ppap_core.capabilities import report
from ppap_core.fixtures import RULE_A
from ppap_core.settings import Settings
from ppap_api import create_app
from ppap_deep.controller import Controller, ScriptedRuntime, UnixControllerServer
from ppap_deep.mapping import passing_response
from ppap_deep.protocol import ExecuteRequest, RequestEnvelope
from ppap_worker import process_claimed
from tests.unit.memory_auth import MemoryAuthStore


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class DeepProtocolTests(unittest.TestCase):
    def test_envelope_and_spec_forbid_host_execution_fields(self):
        spec = {
            "protocol_revision": DEEP_PROTOCOL_REVISION,
            "round_id": "r1",
            "executor": "ppap-deep",
            "executor_revision": DEEP_PROTOCOL_REVISION,
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
        for path in (ROOT / "packages" / "ppap_deep").rglob("*.py"):
            blob += path.read_text()
        blob += (ROOT / "services" / "worker" / "ppap_worker" / "__init__.py").read_text()
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
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = MemoryAuthStore(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(
            create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store)
        )
        self._servers: list[UnixControllerServer] = []

    def tearDown(self):
        for server in self._servers:
            server.close()
        self._env.stop()

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

    def _accept_deep(self, *uploads: dict, key: str = "deep-1", mode: str = ANALYSIS_DEEP) -> str:
        draft = self.client.post("/drafts").json()["id"]
        items = []
        for index, row in enumerate(uploads):
            if len(uploads) > 1 and index == len(uploads) - 1:
                items.append({"file_version_id": row["id"], "role": "reference"})
            else:
                items.append({"file_version_id": row["id"], "role": "target", "rule_version_id": RULE_A})
        saved = self.client.put(f"/drafts/{draft}", json={"items": items, "analysis_mode": mode})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["analysis_mode"], mode)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": key})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        return accepted.json()["id"]

    def _start_controller(self, revision: str = DEEP_PROTOCOL_REVISION) -> str:
        tmp = tempfile.mkdtemp(prefix="ppap-deep-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = str(Path(tmp) / "c.sock")

        def source(file_version_id: str) -> bytes:
            version = self.store.versions_by_id[file_version_id]
            return self.store.version_bytes(version)

        server = UnixControllerServer(
            path,
            Controller(
                ScriptedRuntime(lambda spec, _blobs: passing_response(spec)),
                source,
                executor_revision=revision,
            ),
        )
        server.start()
        self._servers.append(server)
        return path

    def test_default_draft_is_standard(self):
        self._login()
        draft = self.client.post("/drafts")
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["analysis_mode"], ANALYSIS_STANDARD)

    def test_happy_path_writes_product_findings_without_standard_engine(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.pdf", b"reference-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, reference, key="deep-happy")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["analysis_mode"], ANALYSIS_DEEP)
        self.assertEqual(body["executor"], EXECUTOR_DEEP)
        self.assertEqual(body["executor_revision"], DEEP_PROTOCOL_REVISION)
        work = self.store.round_work(round_id)
        self.assertEqual(work.analysis_mode, ANALYSIS_DEEP)
        self.assertTrue(work.inputs[0].sha256)
        with patch("ppap_worker.evaluate_round", side_effect=AssertionError("standard path")):
            with patch("ppap_worker.engine_runner", side_effect=AssertionError("standard engine")):
                claimed = self.store.claim_job("deep-worker")
                result = process_claimed(self.store, claimed, "deep-worker")
        self.assertEqual(result["note"], "deep_audit")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "completed")
        self.assertEqual({row["status"] for row in loaded["findings"]}, {"PASS"})
        self.assertTrue(any(row["item"] == "深度审核已完成" for row in loaded["findings"]))
        self.assertIn("report.xlsx", loaded["coverage"]["checked"])
        self.assertTrue(any("context.pdf" in row for row in loaded["coverage"]["unchecked"]))
        diagnostic = self.store.attempts[(claimed.id, claimed.attempt)]["diagnostic"]
        self.assertEqual(diagnostic["executor"], EXECUTOR_DEEP)

    def test_runtime_unavailable_does_not_fall_back_to_standard(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept_deep(target, key="deep-unavailable")
        with patch("ppap_worker.evaluate_round", side_effect=AssertionError("standard path")):
            with patch("ppap_worker.engine_runner", side_effect=AssertionError("standard engine")):
                claimed = self.store.claim_job("deep-worker")
                result = process_claimed(self.store, claimed, "deep-worker")
        self.assertEqual(result["findings"][0]["status"], "ERR")
        self.assertEqual(result["findings"][0]["suggestions"], "deep_runtime_unavailable")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "failed")
        self.assertEqual(loaded["analysis_mode"], ANALYSIS_DEEP)

    def test_executor_revision_mismatch_requires_resubmit(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, key="deep-revision")
        self.settings.deep_executor_revision = "ppap-deep-v2"
        with patch("ppap_worker.evaluate_round", side_effect=AssertionError("standard path")):
            claimed = self.store.claim_job("deep-worker")
            result = process_claimed(self.store, claimed, "deep-worker")
        self.assertEqual(result["findings"][0]["suggestions"], "executor_revision_mismatch")
        loaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(loaded["execution"], "failed")
        self.assertEqual(loaded["executor_revision"], DEEP_PROTOCOL_REVISION)

    def test_technical_retry_fencing_rejects_stale_attempt(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, key="deep-fence")
        first = self.store.claim_job("w1")
        self.assertEqual(first.attempt, 1)
        self.assertIsNone(self.store.claim_job("w2"))
        self.store.jobs[first.id]["lease_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        second = self.store.claim_job("w2")
        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        rejected = self.store.complete_job(
            first,
            "w1",
            {
                "findings": [
                    {
                        "item_key": "target:" + target["id"],
                        "classification": "执行",
                        "item": "审核执行",
                        "priority": "P0",
                        "status": "ERR",
                        "suggestions": "late-deep",
                        "locator": None,
                    }
                ],
                "coverage": {"checked": [], "unchecked": [], "locator_gap": True},
                "note": "stale",
            },
        )
        self.assertFalse(rejected)
        process_claimed(self.store, second, "w2")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["result_attempt"], 2)
        self.assertEqual(body["findings"][0]["status"], "PASS")
        self.assertNotEqual(body["findings"][0]["suggestions"], "late-deep")
        stale = ClaimedJob(first.id, first.kind, first.payload, first.attempt, first.round_id)
        self.assertEqual(stale.round_id, round_id)

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
        second = self.client.post(
            f"/drafts/{nxt['id']}/accept", json={"idempotency_key": "deep-freeze-2"}
        ).json()
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


if __name__ == "__main__":
    unittest.main()
