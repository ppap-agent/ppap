"""Native Deep analysis mode. Independent protocol; does not import ppap-agent."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import ppap_deep
import ppap_worker
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from ppap_api import create_app
from ppap_api import web_contract as wc
from ppap_application import build_product_store
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
from ppap_core.feishu import UserTokens
from ppap_core.fixtures import RULE_A, RULE_B
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings
from ppap_deep.auditor import SemanticRuntime
from ppap_deep.client import DeepClient, DeepError
from ppap_deep.controller import Controller, ScriptedRuntime, UnixControllerServer
from ppap_deep.mapping import passing_response, spec_from_work
from ppap_deep.protocol import ExecuteRequest, RequestEnvelope
from ppap_deep.semantic.guest import Manifest
from ppap_deep.semantic.guest import execute as execute_guest
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.trace import AuditExecution
from ppap_deep.source import authorize_execution, object_file_source
from ppap_worker import process_claimed
from pydantic import TypeAdapter, ValidationError


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
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
        for path in Path(ppap_deep.__file__).resolve().parent.rglob("*.py"):
            blob += path.read_text()
        blob += Path(ppap_worker.__file__).read_text()
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
        self._servers: list[UnixControllerServer] = []

    def tearDown(self):
        for server in self._servers:
            server.close()
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

    def _start_controller(self, revision: str | None = None, *, runtime=None) -> str:
        tmp = tempfile.mkdtemp(prefix="ppap-deep-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = str(Path(tmp) / "c.sock")

        # Substitute only the external S3 SDK. Authorization and object lookup use
        # production callbacks with the real database and Worker role.
        sdk_patch = patch("ppap_core.storage.s3_client")
        self.s3 = sdk_patch.start().return_value
        self.addCleanup(sdk_patch.stop)
        self.s3.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(self.store.objects.get(kwargs["Key"]))}
        self.runtime_calls: list[ExecuteRequest] = []

        def execute(spec: ExecuteRequest, _blobs: dict[str, bytes]):
            self.runtime_calls.append(spec)
            return passing_response(spec)

        server = UnixControllerServer(
            path,
            Controller(
                runtime if runtime is not None else ScriptedRuntime(execute),
                object_file_source(self.settings),
                executor_revision=revision or self.settings.deep_execution_revision(),
                authorize=authorize_execution(self.settings),
            ),
        )
        server.start()
        self._servers.append(server)
        return path

    def _claimed_spec(self, claimed: ClaimedJob) -> ExecuteRequest:
        work = self.store.round_work(claimed.round_id)
        self.assertIsNotNone(work)
        return spec_from_work(work, EXECUTOR_DEEP, claimed.payload["executor_revision"], DEEP_PROTOCOL_REVISION).model_copy(update={"job_id": claimed.id, "attempt": claimed.attempt})

    def test_default_draft_is_standard(self):
        self._login()
        draft = self.client.post("/drafts")
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(draft.json()["analysis_mode"], ANALYSIS_STANDARD)

    def test_happy_path_writes_product_findings_without_standard_engine(self):
        # ScriptedRuntime checks transport and persistence, not semantic audit agency.
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        reference = self._upload("context.pdf", b"reference-bytes")
        self.settings.deep_controller_socket = self._start_controller()
        round_id = self._accept_deep(target, reference, key="deep-happy")
        body = self.client.get(f"/rounds/{round_id}").json()
        self.assertEqual(body["analysis_mode"], ANALYSIS_DEEP)
        self.assertEqual(body["executor"], EXECUTOR_DEEP)
        self.assertEqual(body["executor_revision"], self.settings.deep_execution_revision())
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
        diagnostic = self._attempt_diagnostic(claimed.id, claimed.attempt)
        self.assertEqual(diagnostic["executor"], EXECUTOR_DEEP)
        self.assertIsNone(loaded["failure"])
        self.assertEqual(diagnostic["usage"], {})

    def test_local_plan_failure_preserves_independent_results_through_worker_and_public_readback(self):
        # JSON publication and real XLSX exercise the product/PG/UDS chain.
        # The model and S3 boundaries are controlled; this is not DOCX parsing,
        # real-provider quality, container isolation, or Feishu archive identity.
        self._login()
        worker = build_product_store(self.settings, objects=self.store.objects, role="worker")
        uploads, rules, original_bytes, selected = [], [], {}, []
        for prefix, filename, document_type, sample_count in [
            ("B", "控制计划.xlsx", "控制计划", 3),
            ("G", "PFMEA.xlsx", "潜在过程失效模式及后果分析", 1),
        ]:
            clauses = [
                {"id": prefix + "1", "category": "频次", "priority": "P1", "rule_to_check": "正文每行频次非空，否则不通过。", "type_of_rule": "table"},
                {"id": prefix + "2", "category": "样本数", "priority": "P1", "rule_to_check": "正文每行样本数至少为 2，否则不通过。", "type_of_rule": "table"},
            ]
            body = {"kind": "engine", "type": document_type, "rule_profile": "v3", "customers": ["测试客户"], "models": [], "parts": [], "rules": clauses}
            source = self._upload(prefix + "-rules.json", json.dumps(body, ensure_ascii=False).encode())
            published = self.client.post("/rules", json={"title": document_type + "客户规则", "body": body, "source_file_version_id": source["id"]})
            self.assertEqual(published.status_code, 201, published.text)
            rules.append(published.json())
            workbook = Workbook()
            workbook.active.title = "数据"
            workbook.active.append(["频次", "样本数", "文件类型"])
            workbook.active.append(["每批", sample_count, document_type])
            stream = BytesIO()
            workbook.save(stream)
            workbook.close()
            content = stream.getvalue()
            uploaded = self._upload(filename, content)
            uploads.append(uploaded)
            original_bytes[uploaded["id"]] = content
            validation_id = str(uuid.uuid4())
            queued = self.client.post("/file-validations", json={"request_id": validation_id, "source_file_id": uploaded["file_id"], "source_file_version_id": uploaded["id"]})
            self.assertEqual(queued.status_code, 202, queued.text)
            checked = worker.claim_job("partial-precheck")
            self.assertIsNotNone(checked)
            self.assertEqual(checked.kind, "file_validation")
            process_claimed(worker, checked, "partial-precheck")
            selected.append({"file_version_id": uploaded["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None})
        bad_target, good_target = [row["id"] for row in uploads]
        bad_rule, good_rule = [row["id"] for row in rules]
        owner = self

        class MetadataModel:
            def structured_completion(self, **request):
                if request["operation"] == "document_classification":
                    return "潜在过程失效模式及后果分析" if "潜在过程失效模式" in request["user_prompt"] else "控制计划"
                owner.assertEqual(request["operation"], "document_metadata_extraction")
                return json.dumps({"customer": "测试客户", "model": None, "part": None, "part_number": None}, ensure_ascii=False)

        class Model:
            def __init__(self):
                self.input_tokens = 0
                self.output_tokens = 0
                self.plan_calls = {bad_rule: 0, good_rule: 0}
                self.audit_targets = []
                self.rules = {}

            def run(self, work, _prompt, schema, _deadline, *, reasoning_effort):
                owner.assertEqual(reasoning_effort, "xhigh")
                self.input_tokens += 10
                self.output_tokens += 2
                if schema is RulePlan:
                    source = json.loads((work / "rule-source.json").read_text())
                    clauses = json.loads((work / "clauses.json").read_text())
                    rule_id = source["rule_version_id"]
                    rule = self.rules[rule_id]
                    self.plan_calls[rule_id] += 1
                    planned = [
                        {
                            "id": clause["id"],
                            "description": clause["rule_to_check"],
                            "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                            "scope": "数据工作表第 2 行",
                            "predicate": {"kind": "semantic", "question": clause["rule_to_check"]},
                            "missing_evidence": "UNABLE",
                            "ambiguities": [],
                            "empty_scope_status": "UNABLE",
                            "empty_scope_basis": "规则未规定空正文的结论。",
                        }
                        for clause in clauses
                    ]
                    digest = rule.sha256
                    if rule_id == bad_rule:
                        if self.plan_calls[rule_id] == 1:
                            digest = "0" * 64
                        else:
                            planned.pop()
                    return RulePlan.model_validate({"source_sha256": digest, "plan_revision": rule.plan_revision, "rules": planned})
                owner.assertIs(schema, AuditExecution)
                targets = json.loads((work / "targets.json").read_text())
                self.audit_targets.append(targets)
                owner.assertEqual(targets, {good_target: good_rule})
                checks, proofs = [], []
                for clause_id, cell, status, reason in [("G1", "A2", "PASS", "正文频次为每批。"), ("G2", "B2", "NG", "正文样本数为 1，小于 2。")]:
                    evidence = [{"document_id": good_target, "locator": {"kind": "xlsx_cell", "sheet": "数据", "cell": cell}}]
                    checks.append({"audit_id": good_target, "rule_id": clause_id, "status": status, "reason": reason, "total": 1, "checked": 1, "unknown": 0, "evidence": evidence})
                    proofs.append(
                        {
                            "kind": "semantic",
                            "audit_id": good_target,
                            "rule_id": clause_id,
                            "scope_basis": "数据工作表仅第 2 行正文",
                            "records": [{"record_id": "row-2", "role": "applicable", "applicability_basis": "已读取正文记录", "status": status, "reason": reason, "evidence": evidence}],
                        }
                    )
                return AuditExecution.model_validate({"report": {"checks": checks}, "trace": {"proofs": proofs}})

        model = Model()
        frozen_revision = self.settings.deep_execution_revision()

        class InProcessGuest:
            calls = 0
            submission = None

            def engine_revision(self):
                return frozen_revision

            def run(self, submission, inputs, work, deadline, cancelled, *, on_progress):
                owner.assertFalse(cancelled.is_set())
                manifest = Manifest.model_validate_json((inputs / "manifest.json").read_bytes())
                owner.assertEqual(manifest.spec, submission)
                self.calls += 1
                self.submission = submission.model_dump(mode="json")
                model.rules = {rule.rule_id: rule for rule in submission.rules}
                execute_guest(inputs, work, model, deadline)

        guest = InProcessGuest()
        self.settings.deep_controller_socket = self._start_controller(runtime=SemanticRuntime(guest, self.store.objects))
        created = self.client.post("/audit-tasks", json={"request_id": str(uuid.uuid4()), "analysis_mode": "deep", "customer_preference": None, "files": selected})
        self.assertEqual(created.status_code, 202, created.text)
        task_path = "/audit-tasks/" + created.json()["id"]
        for _ in uploads:
            job = worker.claim_job("partial-recognition")
            self.assertIsNotNone(job)
            self.assertEqual(job.kind, "file_analysis")
            process_claimed(worker, job, "partial-recognition", rule_ai=MetadataModel())
        matched = self.client.get(task_path).json()
        self.assertEqual([row["match"]["state"] for row in matched["files"]], ["matched", "matched"])
        self.assertEqual([row["match"]["rule"]["id"] for row in matched["files"]], [bad_rule, good_rule])
        confirmed = self.client.post(task_path + "/confirm", json={"request_id": str(uuid.uuid4()), "expected_revision": matched["input_revision"]})
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        round_path = "/rounds/" + confirmed.json()["round_id"]
        frozen = self.client.get(round_path, params={"read_view": "true"}).json()
        self.assertEqual({row["file_version_id"]: row["rule_version_id"] for row in frozen["inputs"]}, {bad_target: bad_rule, good_target: good_rule})
        self.assertEqual(
            {row["file"]["id"]: row["file"]["sha256"] for row in frozen["read_view"]["files"]},
            {row["id"]: hashlib.sha256(original_bytes[row["id"]]).hexdigest() for row in uploads},
        )
        self.assertEqual([row["status"] for row in frozen["inputs"]], ["ready", "ready"])
        job = worker.claim_job("partial-audit")
        self.assertIsNotNone(job)
        self.assertEqual(job.kind, "audit")
        self.assertEqual(job.round_id, frozen["id"])
        result = process_claimed(worker, job, "partial-audit")
        self.assertIsNone(result.get("failure"))
        self.assertEqual(result["usage"]["input_tokens"], 40)
        self.assertEqual(result["usage"]["output_tokens"], 8)

        loaded = self.client.get(round_path, params={"read_view": "true"}).json()
        TypeAdapter(wc.Round).validate_python(loaded)
        self.assertEqual(loaded["execution"], "completed")
        self.assertEqual(loaded["inputs"], frozen["inputs"])
        self.assertEqual(loaded["executor_revision"], frozen_revision)
        self.assertEqual(loaded["result_job_id"], job.id)
        self.assertEqual(loaded["result_attempt"], job.attempt)
        self.assertIsNone(loaded["failure"])
        self.assertEqual(
            {(row["locator"]["file_version_id"], row["locator"]["rule_item_id"]): row["status"] for row in loaded["findings"]},
            {(bad_target, "B1"): "ERR", (bad_target, "B2"): "ERR", (good_target, "G1"): "PASS", (good_target, "G2"): "NG"},
        )
        for finding in loaded["findings"]:
            if finding["locator"]["file_version_id"] == good_target:
                semantic = finding["locator"]["semantic"]
                self.assertEqual(semantic["rule_version_id"], good_rule)
                self.assertEqual(semantic["sources"][0]["excerpt"], "每批" if finding["locator"]["rule_item_id"] == "G1" else "1")
        self.assertEqual(
            {(row["file_version_id"], row["id"]): row["coverage"] for row in loaded["coverage"]["rule_items"]},
            {(bad_target, "B1"): "execution_error", (bad_target, "B2"): "execution_error", (good_target, "G1"): "executed", (good_target, "G2"): "executed"},
        )
        self.assertEqual(loaded["read_view"]["unassigned_findings"], [])
        current = self.client.get(task_path).json()
        TypeAdapter(wc.AuditTask).validate_python(current)
        by_version = {row["file"]["id"]: row["audit"] for row in current["files"]}
        failed, healthy = by_version[bad_target], by_version[good_target]
        self.assertEqual(failed["execution"], "review_failed")
        self.assertEqual([row["rule_item"]["id"] for row in failed["checks"]], ["B1", "B2"])
        self.assertEqual([row["finding"]["status"] for row in failed["checks"]], ["ERR", "ERR"])
        self.assertEqual(failed["report"]["state"], "not_eligible")
        self.assertEqual(healthy["execution"], "completed")
        self.assertEqual([row["rule_item"]["id"] for row in healthy["checks"]], ["G1", "G2"])
        self.assertEqual(healthy["conclusion"], "NG")
        self.assertEqual(healthy["counts"], {"pass_count": 1, "ng_count": 1, "unknown_count": 0, "not_covered_count": 0, "error_count": 0})
        self.assertEqual([row["finding"]["status"] for row in healthy["checks"]], ["PASS", "NG"])
        # Only an eligible frozen result obtains a real queued PDF report.
        self.assertEqual(healthy["report"]["state"], "pending")
        self.assertIsNotNone(healthy["report"]["report_id"])
        historical = {row["file"]["id"]: row["audit"] for row in loaded["read_view"]["files"]}
        for version in (bad_target, good_target):
            for field in ("rule", "execution", "conclusion", "counts", "checks"):
                self.assertEqual(historical[version][field], by_version[version][field])
        for upload, published in zip(uploads, rules):
            downloaded = self.client.get(f"{round_path}/files/{upload['file_id']}/versions/{upload['id']}/download")
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.content, original_bytes[upload["id"]])
            self.assertEqual(self.client.get("/rules/" + published["id"]).json()["body"], published["body"])
        self.assertEqual(guest.calls, 1)
        self.assertEqual(model.plan_calls, {bad_rule: 2, good_rule: 1})
        self.assertEqual(model.audit_targets, [{good_target: good_rule}])
        submission = guest.submission
        self.assertEqual(submission["targets"], {bad_target: bad_rule, good_target: good_rule})
        self.assertEqual(submission["semantic_engine_revision"], frozen_revision)
        for _ in range(2):
            self.assertEqual(self.client.get(round_path, params={"read_view": "true"}).json(), loaded)
            self.assertEqual(self.client.get(task_path).json(), current)
        self.assertEqual(guest.calls, 1)
        self.assertEqual(guest.submission, submission)
        self.assertEqual(model.plan_calls, {bad_rule: 2, good_rule: 1})
        self.assertEqual(model.audit_targets, [{good_target: good_rule}])

    def test_runtime_unavailable_does_not_fall_back_to_standard(self):
        self._login()
        target = self._upload("report.xlsx", b"target-bytes")
        round_id = self._accept_deep(target, key="deep-unavailable")
        with (
            patch("ppap_worker.evaluate_round", side_effect=AssertionError("standard path")),
            patch("ppap_worker.engine_runner", side_effect=AssertionError("standard engine")),
            patch("subprocess.Popen", side_effect=AssertionError("local runtime")) as start_process,
        ):
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
        reference = self._upload("context.pdf", b"reference-bytes")
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
        frozen = self.settings.deep_execution_revision()
        self.settings.deep_executor_revision = "ppap-deep-v999"
        with patch("ppap_worker.evaluate_round", side_effect=AssertionError("standard path")):
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
        self.settings.llm_model = "changed-model-with-the-same-version-label"
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
