"""Feishu writeback through real PostgreSQL/Core, with controlled HTTP and guest OS boundaries."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from agent.core_result import core_response
from agent.protocol import ExecuteRequest, FrozenFile
from api import create_app
from audit.audits import RoundInput
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceDiagnostic, EvidenceSource
from fastapi.testclient import TestClient
from product.feishu import UserTokens
from product.feishu_writeback import FeishuWritebackError, comment_text, history_text, report_blocks, structured_blocks, writeback_state
from product.fixtures import RULE_A
from product.identity import SESSION_COOKIE
from product.settings import Settings
from product.writeback_targets import normalize_external_ref, public_target
from worker import process_claimed


def _clean_env(**extra: str):
    keep = {key: value for key, value in os.environ.items() if not key.startswith(("PPAP_", "FEISHU_"))}
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


def _core_projection(results, *, target):
    """Exercise the production Core projection, with explicit frozen rule identity."""
    rules = {"rules": [{"id": "signature", "category": "签字", "rule_to_check": "签字检查"}]}
    frozen = FrozenInput(target.file_version_id, "a" * 64, "target", target.filename, file_id=target.file_id)
    binding = RuleBinding("test:writeback", "b" * 64, {"signature": "checker:check"})
    selected = FrozenTarget(target.file_version_id, "suite-1", binding.suite_key, "c" * 64, rules, binding)
    snapshot = ExecutionSnapshot("tenant", "user", (frozen,), (selected,), "runtime", "library", {}, round_id="round-1")
    spec = ExecuteRequest(
        protocol_revision="test-projection",
        round_id=snapshot.round_id,
        executor="ppap-checker-core",
        executor_revision=snapshot.runtime_revision,
        checker_snapshot_digest=snapshot.input_digest,
        files=[FrozenFile(file_version_id=target.file_version_id, file_id=target.file_id, role="target", filename=target.filename, sha256=frozen.sha256, rule_version_id=RULE_A, rule_body=rules)],
    )
    result = {
        "input_digest": snapshot.input_digest,
        "findings": [{**row.model_dump(), "file_version_id": target.file_version_id} for row in results],
        "coverage": [{"file_version_id": target.file_version_id, "clause_id": "signature", "coverage": "executed"}],
    }
    return {
        **core_response(spec, snapshot, result).model_dump(),
        "inputs": [{"role": "target", "file_version_id": target.file_version_id, "rule_version_id": RULE_A, "original_filename": target.filename, "rule": {"body": rules}}],
    }


class HistorySemanticsTests(unittest.TestCase):
    def test_public_writeback_hides_object_ref(self):
        self.assertEqual(public_target(None), {"available": False})
        shown = public_target({"provider": "feishu", "object_kind": "docx", "object_ref": "secret-token"})
        self.assertEqual(shown, {"available": True, "provider": "feishu", "object_kind": "docx"})
        self.assertNotIn("object_ref", shown)
        self.assertEqual(
            normalize_external_ref({"provider": "feishu", "object_kind": "docx", "object_ref": " doc-1 "}),
            {"provider": "feishu", "object_kind": "docx", "object_ref": "doc-1"},
        )
        with self.assertRaisesRegex(ValueError, "invalid_writeback_target"):
            normalize_external_ref({"provider": "feishu", "object_kind": "docx"})

    def test_core_ng_keeps_its_file_and_all_evidence_in_the_report(self):
        target = RoundInput("target", "file-1", "version-1", "report.xlsx", "ready")
        evidence = AuditEvidence(
            required="structure_or_visual",
            used="both",
            source_format="xlsx",
            prepared_format="xlsx",
            sources=(
                EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"})),
                EvidenceSource(kind="visual", locator=json.dumps({"kind": "xlsx_range", "sheet": "Sheet1", "start": "B2", "end": "C3"})),
            ),
            diagnostics=(EvidenceDiagnostic(code="partial_structure", stage="structure", severity="warning", message="部分结构缺失"),),
        )
        result = AuditResultItem(
            classification="签字",
            item="签字检查",
            priority="P0",
            status="NG",
            suggestions="缺少签字",
            evidence=evidence,
            clause_id="signature",
            checks_performed=1,
        )
        mapped = _core_projection([result], target=target)
        body = {
            "execution": "completed",
            "inputs": mapped["inputs"],
            "findings": mapped["findings"],
        }
        text = history_text(body)
        self.assertNotIn("审核结果：PASS", text)
        self.assertIn("签字检查 : 缺少签字", text)
        self.assertEqual(comment_text(body), "总结：\n签字检查：缺少签字")
        locator = mapped["findings"][0]["locator"]
        self.assertEqual(locator["kind"], "file_version")
        self.assertEqual(locator["suite_revision_id"], "suite-1")
        self.assertEqual(locator["checker_digest"], "b" * 64)
        self.assertEqual(locator["verification_status"], "unverified")
        self.assertEqual(
            locator["semantic"]["sources"],
            [
                {"file_version_id": target.file_version_id, "filename": target.filename, "role": "target", "kind": source.kind, "locator": json.loads(source.locator), "excerpt": ""}
                for source in evidence.sources
            ],
        )
        self.assertEqual(locator["semantic"]["diagnostics"], [item.model_dump() for item in evidence.diagnostics])

    def test_same_name_versions_have_separate_machine_results(self):
        first = RoundInput("target", "file-1", "version-1", "report.xlsx", "ready")
        second = RoundInput("target", "file-2", "version-2", "report.xlsx", "ready")
        findings, inputs = [], []
        for target, status in [(first, "PASS"), (second, "NG")]:
            result = AuditResultItem(
                classification="签字",
                item="签字检查",
                priority="P0",
                status=status,
                suggestions="缺少签字" if status == "NG" else "",
                clause_id="signature",
                checks_performed=1,
                evidence=AuditEvidence(
                    required="structure",
                    used="structure",
                    source_format="xlsx",
                    prepared_format="xlsx",
                    sources=(EvidenceSource(kind="structure", locator=json.dumps({"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"})),),
                ),
            )
            projected = _core_projection([result], target=target)
            findings.extend(projected["findings"])
            inputs.extend(projected["inputs"])
        findings[1]["review"] = {"judgment": "PASS", "rationale": "人工接受", "next_step": "", "version": 1}
        body = {
            "execution": "completed",
            "inputs": inputs,
            "findings": findings,
        }
        text = history_text(body)
        self.assertIn("**审核内容1：report.xlsx**\n\n审核结果：PASS", text)
        self.assertIn("**审核内容2：report.xlsx**\n\n审核结果：NG\n总结：\n签字检查 : 缺少签字", text)
        self.assertEqual(text.count("审核结果：PASS"), 1)
        self.assertEqual(comment_text(body), "总结：\n签字检查：缺少签字")

    def test_unproven_target_results_cannot_generate_a_report(self):
        target = {"role": "target", "rule_version_id": RULE_A, "file_version_id": "target-version", "original_filename": "report.xlsx"}
        reference = {"role": "reference", "file_version_id": "reference-version", "original_filename": "report.xlsx"}
        passed = {"status": "PASS", "locator": {"file_version_id": "target-version"}}
        cases = [
            ("empty", [target], []),
            ("no_inputs", [], [passed]),
            ("unmapped", [target], [{"status": "PASS", "locator": {"filename": "report.xlsx"}}]),
            ("different_version", [target], [{"status": "PASS", "locator": {"file_version_id": "other-version"}}]),
            ("extra_unmapped", [target], [passed, {"status": "NG", "locator": None}]),
            ("unknown_status", [target], [{**passed, "status": "UNKNOWN"}]),
            ("malformed_finding", [target], [passed, None]),
            ("missing_target", [target, reference], [{"status": "PASS", "locator": {"file_version_id": "reference-version"}}]),
            ("only_reference", [reference], [{"status": "PASS", "locator": {"file_version_id": "reference-version"}}]),
        ]
        for name, inputs, findings in cases:
            with self.subTest(name=name):
                body = {"execution": "completed", "inputs": inputs, "findings": findings}
                self.assertEqual(writeback_state(body), "round_incomplete")
                with self.assertRaisesRegex(FeishuWritebackError, "round_incomplete"):
                    history_text(body)

    def test_missing_evidence_and_empty_or_unknown_core_results_remain_explicit(self):
        target = RoundInput("target", "file-1", "version-1", "report.xlsx", "ready")
        for status in ("NG", "UNKNOWN", None):
            with self.subTest(status=status):
                results = [] if status is None else [AuditResultItem(classification="签字", item="签字检查", priority="P0", status=status, suggestions="", clause_id="signature")]
                mapped = _core_projection(results, target=target)
                finding = mapped["findings"][0]
                self.assertEqual(finding["status"], "NG" if status == "NG" else "ERR")
                self.assertEqual(finding["locator"]["file_version_id"], "version-1")
                self.assertEqual(finding["locator"]["file_id"], "file-1")
                self.assertTrue(mapped["coverage"]["locator_gap"])
                body = {
                    "execution": "completed",
                    "inputs": mapped["inputs"],
                    "findings": mapped["findings"],
                }
                if status == "NG":
                    self.assertIn("审核结果：NG", history_text(body))
                else:
                    self.assertEqual(writeback_state(body), "round_has_errors")
                    with self.assertRaisesRegex(FeishuWritebackError, "round_has_errors"):
                        history_text(body)

    def test_pass_round_writes_result_and_return_url(self):
        body = {
            "id": "round-1",
            "execution": "completed",
            "created_at": "2026-09-19T12:00:00+00:00",
            "inputs": [{"role": "target", "rule_version_id": RULE_A, "file_version_id": "version-1", "original_filename": "report.xlsx"}],
            "findings": [
                {
                    "item": "封面",
                    "status": "PASS",
                    "suggestions": "",
                    "locator": {"file_version_id": "version-1", "filename": "report.xlsx"},
                }
            ],
        }
        text = history_text(body, "http://127.0.0.1:4173/?round=round-1")
        self.assertIn("审核时间：2026 年 09 月 19 日 12:00", text)
        self.assertIn("**审核内容1：report.xlsx**", text)
        self.assertIn("审核结果：PASS", text)
        self.assertIn("回访：http://127.0.0.1:4173/?round=round-1", text)
        self.assertEqual(writeback_state(body), "ready")
        self.assertEqual(comment_text(body), "")
        types = [block["block_type"] for block in structured_blocks(text)]
        self.assertEqual(types[:3], [4, 5, 2])

    def test_ng_round_writes_summary_and_comment(self):
        body = {
            "id": "round-2",
            "execution": "completed",
            "inputs": [{"role": "target", "rule_version_id": RULE_A, "file_version_id": "version-1", "original_filename": "plan.xlsx"}],
            "findings": [
                {
                    "item": "零件号",
                    "status": "NG",
                    "suggestions": "与封面不一致",
                    "locator": {"file_version_id": "version-1", "filename": "plan.xlsx"},
                }
            ],
        }
        text = history_text(body)
        self.assertIn("总结：", text)
        self.assertIn("零件号 : 与封面不一致", text)
        self.assertNotIn("审核结果：PASS", text)
        self.assertEqual(comment_text(body), "总结：\n零件号：与封面不一致")

    def test_ng_without_suggestions_still_states_the_machine_result(self):
        body = {
            "execution": "completed",
            "inputs": [{"role": "target", "rule_version_id": RULE_A, "file_version_id": "version-1", "original_filename": "plan.xlsx"}],
            "findings": [{"item": "零件号", "status": "NG", "locator": {"file_version_id": "version-1"}}],
        }
        self.assertIn("审核结果：NG", history_text(body))

    def test_err_and_incomplete_are_not_ready(self):
        incomplete = {"execution": "queued", "findings": [{"status": "PASS"}]}
        self.assertEqual(writeback_state(incomplete), "round_incomplete")
        errored = {
            "execution": "completed",
            "findings": [{"status": "ERR", "suggestions": "engine_error"}],
        }
        self.assertEqual(writeback_state(errored), "round_has_errors")

    def test_deep_unable_and_na_are_not_written_as_pass(self):
        body = {
            "executor": "ppap-deep",
            "execution": "completed",
            "inputs": [
                {
                    "role": "target",
                    "file_version_id": "target-v",
                    "original_filename": "plan.xlsx",
                    "rule_version_id": "rule-v",
                    "rule": {
                        "title": "客户规则",
                        "source_file_version_id": "original-v",
                        "body": {
                            "rules": [
                                {"id": "R1", "rule_to_check": "频次须明确"},
                                {"id": "R2", "rule_to_check": "参考资料适用性"},
                            ]
                        },
                    },
                },
                {"role": "reference", "file_version_id": "reference-v", "original_filename": "flow.pdf"},
            ],
            "findings": [
                {
                    "item": "频次须明确",
                    "status": "UNABLE",
                    "suggestions": "请提供图例",
                    "locator": {
                        "file_version_id": "target-v",
                        "semantic": {
                            "rule_version_id": "rule-v",
                            "clause_id": "R1",
                            "rule_text": "频次须明确",
                            "scope": {"total": 5, "checked": 3, "unknown": 2},
                            "sources": [
                                {"file_version_id": "target-v", "filename": "plan.xlsx", "role": "target", "locator": {"kind": "xlsx_cell", "sheet": "控制计划", "cell": "B2"}, "excerpt": "待确认"}
                            ],
                        },
                    },
                },
                {
                    "item": "参考资料适用性",
                    "status": "NA",
                    "suggestions": "此组不适用",
                    "locator": {
                        "file_version_id": "target-v",
                        "semantic": {
                            "rule_version_id": "rule-v",
                            "clause_id": "R2",
                            "rule_text": "参考资料适用性",
                            "scope": {"total": 1, "checked": 1, "unknown": 0},
                            "sources": [{"file_version_id": "reference-v", "filename": "flow.pdf", "role": "reference", "locator": {"kind": "pdf_block", "page": 2, "block": 3}, "excerpt": "不适用"}],
                        },
                    },
                },
            ],
        }
        self.assertEqual(writeback_state(body), "ready")
        summary = history_text(body)
        self.assertIn("审核结果：UNABLE、NA", summary)
        self.assertIn("条款核验记录（按条款累计）：共 6 条，已判断 4 条，无法判断 2 条", summary)
        report_text = "\n".join(element["text_run"]["content"] for block in report_blocks(body) for element in (block.get("text") or {}).get("elements", []))
        self.assertIn("条款核验记录（按条款累计）：共 6 条，已判断 4 条，无法判断 2 条", report_text)
        self.assertIn("本条核验记录：共 5 条，已判断 3 条，无法判断 2 条", report_text)
        self.assertNotIn("审核结果：PASS", summary)
        self.assertIn("频次须明确（UNABLE）：请提供图例", comment_text(body))
        self.assertIn("参考资料适用性（NA）：此组不适用", comment_text(body))

    def test_deep_global_failure_does_not_create_a_fake_finding(self):
        body = {
            "executor": "ppap-deep",
            "execution": "failed",
            "failure": {"code": "MODEL_TIMEOUT"},
            "inputs": [{"role": "target", "file_version_id": "v1", "original_filename": "plan.xlsx"}],
            "findings": [],
        }
        self.assertEqual(writeback_state(body), "round_incomplete")
        with self.assertRaises(FeishuWritebackError):
            history_text(body)


class _WritebackMock:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_insert = False
        self.fail_comment = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method.upper()
        payload = json.loads(request.content.decode()) if request.content else {}
        self.calls.append((method, url, payload))
        if request.headers.get("authorization") != "Bearer user-access":
            return httpx.Response(401, json={"code": 99991663, "msg": "unauthorized"})
        if method == "POST" and "/docx/v1/documents/" in url and url.endswith("/children"):
            if self.fail_insert:
                return httpx.Response(500, json={"code": 1, "msg": "insert failed"})
            if payload.get("index") != 0 or not payload.get("children"):
                return httpx.Response(400, json={"code": 1, "msg": "bad children"})
            return httpx.Response(200, json={"code": 0, "data": {}})
        if method == "POST" and "/drive/v1/files/" in url and "/comments" in url:
            if self.fail_comment:
                return httpx.Response(500, json={"code": 1, "msg": "comment failed"})
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"code": 1, "msg": "unmocked"})


class FeishuWritebackTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory, core_system):
        self.core_system = core_system
        self.auth_store = auth_store_factory

    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.core_system.install(self.store)
        self.core_system.adopt_presence(self.store)
        self.feishu = _WritebackMock()
        self.http = httpx.Client(transport=httpx.MockTransport(self.feishu.handler))
        self.client = TestClient(
            create_app(
                self.settings,
                lambda _s: {"state": "ok"},
                lambda _s: {"state": "ok"},
                self.store,
                self.http,
            )
        )

    def tearDown(self):
        self.http.close()
        self._env.stop()

    def _set_findings(self, round_id: str, status: str, suggestions: str | None = None, item: str | None = None) -> None:
        with self.store._conn() as conn:
            conn.execute(
                """
                UPDATE product.findings
                SET status = %s,
                    suggestions = COALESCE(%s, suggestions),
                    item = COALESCE(%s, item)
                WHERE round_id = %s
                """,
                (status, suggestions, item, round_id),
            )

    def _set_unmapped_locator(self, round_id: str) -> None:
        with self.store._conn() as conn:
            conn.execute(
                """UPDATE product.findings
                   SET locator = '{"filename":"report.xlsx"}'::jsonb
                   WHERE round_id = %s""",
                (round_id,),
            )

    def _login_fixture(self, subject: str) -> None:
        self.client.cookies.clear()
        response = self.client.post("/isolated-dev/sessions", json={"subject": subject})
        self.assertEqual(response.status_code, 200)

    def _login_feishu_user(self, subject: str = "alice") -> None:
        self.client.cookies.clear()
        principal = self.store.principal_for_fixture(subject)
        now = datetime.now(timezone.utc)
        tokens = UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7))
        raw = self.store.create_session(principal, tokens)
        self.client.cookies.set(SESSION_COOKIE, raw)

    def _upload(self, name: str, body: bytes) -> dict:
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = self.client.post("/files", json=meta)
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        return ready.json()

    def _bind_writeback(self, draft: str, token: str = "docx-token") -> None:
        saved = self.client.put(
            f"/drafts/{draft}",
            json={"writeback_target": {"provider": "feishu", "object_kind": "docx", "object_ref": token}},
        )
        self.assertEqual(saved.status_code, 200)
        body = saved.json()
        self.assertTrue(body["writeback"]["available"])
        self.assertEqual(body["writeback"]["provider"], "feishu")
        dumped = json.dumps(body)
        self.assertNotIn(token, dumped)
        self.assertNotIn("object_ref", dumped)

    def _complete_round(self, filename: str = "report.xlsx", *, writeback: bool = True) -> str:
        version = self._upload(filename, self.core_system.workbook())
        draft = self.client.post("/drafts").json()["id"]
        saved = self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        self.assertEqual(saved.status_code, 200)
        if writeback:
            self._bind_writeback(draft)
        accepted = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-writeback-1"})
        self.assertEqual(accepted.status_code, 201)
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        guest_calls = self.core_system.calls
        process_claimed(self.store, claimed, "test-worker")
        self.assertEqual(self.core_system.calls, guest_calls + 1)
        return accepted.json()["id"]

    def test_fixture_session_cannot_writeback_without_user_token(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        self._login_fixture("alice")
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "feishu_user_token_missing")

    def test_pass_round_inserts_history_and_return_url(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["inserted"])
        self.assertFalse(body["commented"])
        self.assertEqual(body["round_id"], round_id)
        # The return link opens the task the Round belongs to.
        task_id = self.client.get(f"/rounds/{round_id}").json()["source_draft_id"]
        self.assertEqual(body["return_url"], f"http://127.0.0.1:4173/tasks/{task_id}")
        insert = next(call for call in self.feishu.calls if call[0] == "POST" and call[1].endswith("/children"))
        self.assertIn("/documents/docx-token/blocks/docx-token/children", insert[1])
        self.assertEqual(insert[2]["index"], 0)
        dumped = json.dumps(insert[2]["children"], ensure_ascii=False)
        self.assertIn("审核结果：PASS", dumped)
        self.assertIn(body["return_url"], dumped)
        self.assertFalse(any("/comments" in call[1] for call in self.feishu.calls))

    def test_ng_round_comments_and_err_does_not_write(self):
        self._login_feishu_user()
        round_id = self._complete_round("plan.xlsx")
        self._set_findings(round_id, "NG", "缺项", "零件号")
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["commented"])
        comment = next(call for call in self.feishu.calls if "/comments" in call[1])
        self.assertIn("docx-token", comment[1])
        self.assertIn("缺项", json.dumps(comment[2], ensure_ascii=False))

        self.feishu.calls.clear()
        self._set_findings(round_id, "ERR")
        blocked = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "round_has_errors")
        self.assertEqual(self.feishu.calls, [])

    def test_unmapped_results_do_not_send_external_writes(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        self._set_unmapped_locator(round_id)
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "round_incomplete")
        self.assertEqual(self.feishu.calls, [])

    def test_incomplete_and_cross_tenant_and_insert_failure(self):
        self._login_feishu_user()
        version = self._upload("queued.xlsx", self.core_system.workbook())
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(
            f"/drafts/{draft}",
            json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        self._bind_writeback(draft)
        queued = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "feishu-writeback-queued"})
        queued_id = queued.json()["id"]
        incomplete = self.client.post(f"/integrations/feishu/rounds/{queued_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(incomplete.status_code, 409)
        self.assertEqual(incomplete.json()["error"], "round_incomplete")
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        guest_calls = self.core_system.calls
        process_claimed(self.store, claimed, "test-worker")
        self.assertEqual(self.core_system.calls, guest_calls + 1)

        round_id = self._complete_round()
        self.feishu.fail_insert = True
        failed = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["error"], "feishu_writeback_failed")

        self.feishu.fail_insert = False
        self._login_feishu_user("carol")
        denied = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(denied.status_code, 403)

    def test_writeback_uses_stored_target_and_ignores_caller_token(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        reloaded = self.client.get(f"/rounds/{round_id}").json()
        self.assertTrue(reloaded["writeback"]["available"])
        self.assertNotIn("object_ref", json.dumps(reloaded))
        self.assertNotIn("docx-token", json.dumps(reloaded))
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "other-token"})
        self.assertEqual(response.status_code, 200)
        insert = next(call for call in self.feishu.calls if call[0] == "POST" and call[1].endswith("/children"))
        self.assertIn("/documents/docx-token/blocks/docx-token/children", insert[1])
        self.assertNotIn("other-token", insert[1])

    def test_writeback_without_stored_target_is_conflict(self):
        self._login_feishu_user()
        round_id = self._complete_round(writeback=False)
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "writeback_target_missing")
        self.assertEqual(self.feishu.calls, [])

    def test_resubmit_keeps_predecessor_writeback_target(self):
        self._login_feishu_user()
        round_id = self._complete_round()
        again = self.client.post("/drafts", json={"predecessor_id": round_id})
        self.assertEqual(again.status_code, 201)
        draft = again.json()
        self.assertTrue(draft["writeback"]["available"])
        version = self._upload("again.xlsx", self.core_system.workbook("again"))
        saved = self.client.put(
            f"/drafts/{draft['id']}",
            json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]},
        )
        self.assertEqual(saved.status_code, 200)
        accepted = self.client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": "feishu-writeback-again", "predecessor_id": round_id})
        self.assertEqual(accepted.status_code, 201)
        claimed = self.store.claim_job("test-worker")
        self.assertIsNotNone(claimed)
        guest_calls = self.core_system.calls
        process_claimed(self.store, claimed, "test-worker")
        self.assertEqual(self.core_system.calls, guest_calls + 1)
        next_id = accepted.json()["id"]
        response = self.client.post(f"/integrations/feishu/rounds/{next_id}/writeback")
        self.assertEqual(response.status_code, 200)
        insert = next(call for call in self.feishu.calls if call[0] == "POST" and call[1].endswith("/children"))
        self.assertIn("/documents/docx-token/", insert[1])

    def test_comment_failure_still_inserts(self):
        self._login_feishu_user()
        round_id = self._complete_round("plan.xlsx")
        self._set_findings(round_id, "NG", "缺项", "零件号")
        self.feishu.fail_comment = True
        response = self.client.post(f"/integrations/feishu/rounds/{round_id}/writeback", json={"file_token": "docx-token"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["inserted"])
        self.assertFalse(response.json()["commented"])


if __name__ == "__main__":
    unittest.main()
