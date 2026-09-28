"""Worker engine_runner coverage for every target and unused references."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


from fastapi.testclient import TestClient

from ppap_core.audits import RoundInput, RoundWork
from ppap_core.contracts import AuditEvidence, AuditResultItem, EvidenceSource
from ppap_core.settings import Settings
from ppap_api import create_app


def _clean_env(**extra: str):
    keep = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))
    }
    keep.update(extra)
    return patch.dict(os.environ, keep, clear=True)


class EngineRunnerTests(unittest.TestCase):
    def setUp(self):
        self._env = _clean_env(FEISHU_APP_ID="cli_test", FEISHU_APP_SECRET="secret", PPAP_BUILD_SHA="a" * 40)
        self._env.start()
        self.settings = Settings()
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(
            create_app(self.settings, lambda _s: {"state": "ok"}, lambda _s: {"state": "ok"}, self.store)
        )

    def tearDown(self):
        self._env.stop()

    def _login(self) -> None:
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": "alice"}).status_code, 200)

    def _upload(self, name: str, body: bytes) -> dict:
        meta = {"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)}
        created = self.client.post("/files", json=meta)
        self.assertEqual(created.status_code, 201)
        file_id, vid = created.json()["file_id"], created.json()["id"]
        self.assertEqual(self.client.put(f"/files/{file_id}/versions/{vid}/blob", content=body).status_code, 200)
        ready = self.client.post(f"/files/{file_id}/versions/{vid}/finalize")
        self.assertEqual(ready.status_code, 200)
        return ready.json()

    def test_runs_each_target_with_its_bound_rule_and_keeps_references_in_context_only(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        self._login()
        first = self._upload("a.xlsx", b"one")
        second = self._upload("b.xlsx", b"two")
        reference = self._upload("context.pdf", b"ref")
        work = RoundWork(
            "round-multi",
            "t1",
            [
                RoundInput("target", first["file_id"], first["id"], "a.xlsx", "ready", rule_version_id="rule-1", rule_title="控制计划", rule_body={"kind": "engine", "rule_profile": "v3", "type": "控制计划"}),
                RoundInput("target", second["file_id"], second["id"], "b.xlsx", "ready", rule_version_id="rule-2", rule_title="PFMEA", rule_body={"kind": "engine", "rule_profile": "v2", "type": "过程失效模式及后果分析"}),
                RoundInput("reference", reference["file_id"], reference["id"], "context.pdf", "ready"),
                RoundInput("reference", "missing-file", "missing-version", "gone.pdf", "ready"),
            ],
        )
        calls = []

        def fake_run(audit, rule, group_context=None):
            calls.append((audit.audit_id, group_context, rule))
            return [
                AuditResultItem(
                    status="PASS",
                    classification="规则",
                    item=audit.file_metadata["original_filename"],
                    priority="P0",
                    suggestions="",
                    evidence=AuditEvidence(
                        required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                        sources=(EvidenceSource(kind="structure", locator="A1"),),
                    ),
                )
            ]

        with patch.object(app_runtime, "run_audit_engine", fake_run):
            result = engine_runner(work, self.store)
        self.assertEqual([call[0] for call in calls], [first["id"], second["id"]])
        self.assertEqual([call[2].rule_id for call in calls], ["rule-1", "rule-2"])
        self.assertEqual([call[2].rule_profile for call in calls], ["v3", "v2"])
        self.assertEqual([call[2].type for call in calls], ["控制计划", "过程失效模式及后果分析"])
        context = calls[0][1]
        self.assertEqual(context.task_group_id, "round-multi")
        self.assertIn(first["id"], context.by_audit_id)
        self.assertIn(second["id"], context.by_audit_id)
        self.assertIn(reference["id"], context.by_audit_id)
        self.assertNotIn("missing-version", context.by_audit_id)
        self.assertEqual(set(context.rules_by_id), {"rule-1", "rule-2"})
        self.assertEqual([item.audit_id for item in context.by_doc_type["控制计划"]], [first["id"]])
        self.assertEqual(
            {row["item_key"] for row in result["findings"]},
            {f"{first['id']}:engine:1", f"{second['id']}:engine:1"},
        )
        self.assertEqual(
            {row["locator"]["file_version_id"] for row in result["findings"]},
            {first["id"], second["id"]},
        )
        self.assertEqual(result["coverage"]["checked"], ["a.xlsx", "b.xlsx"])
        self.assertTrue(any("context.pdf" in row for row in result["coverage"]["unchecked"]))
        self.assertTrue(any("gone.pdf" in row and "无法加载" in row for row in result["coverage"]["unchecked"]))
        self.assertFalse(result["coverage"]["locator_gap"])

    def test_customer_rule_items_are_the_machine_checklist(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_excel_audit_engine.checkers.rule_declarations import declarations_for_target, freeze_target_rule_execution
        from ppap_worker import engine_runner

        self._login()
        first = self._upload("plan.xlsx", b"one")
        mapped_text = declarations_for_target("控制计划", "plan.xlsx", "v3")[0][1]
        body = {
            "kind": "engine",
            "rule_profile": "v3",
            "type": "控制计划",
            "rules": [
                {"id": "keep", "category": mapped_text, "priority": "P1", "rule_to_check": mapped_text, "type_of_rule": "text"},
                {"id": "extra", "category": "customer-only", "priority": None, "rule_to_check": "arbitrary customer requirement", "type_of_rule": "text"},
            ],
        }
        execution = freeze_target_rule_execution(body, "plan.xlsx")
        work = RoundWork(
            "round-items",
            "t1",
            [
                RoundInput(
                    "target", first["file_id"], first["id"], "plan.xlsx", "ready",
                    rule_version_id="rule-keep", rule_title="控制计划", rule_body=body, rule_execution=execution,
                ),
            ],
        )

        def fake_run(audit, rule, group_context=None):
            return [
                AuditResultItem(
                    status="PASS",
                    classification="规则",
                    item=description,
                    priority="P0",
                    suggestions="",
                    evidence=AuditEvidence(
                        required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                        sources=(EvidenceSource(kind="structure", locator="A1"),),
                    ),
                )
                for description in [row["rule_to_check"] for row in rule.rules]
            ]

        with patch.object(app_runtime, "run_audit_engine", fake_run):
            result = engine_runner(work, self.store)
        self.assertEqual([row["item_key"] for row in result["findings"]], [f"{first['id']}:rule:keep"])
        self.assertEqual(result["findings"][0]["item"], mapped_text)
        self.assertEqual(result["findings"][0]["locator"]["rule_item_id"], "keep")
        self.assertTrue(any("arbitrary customer requirement" in row for row in result["coverage"]["unchecked"]))
        self.assertEqual([row["id"] for row in result["coverage"]["rule_items"]], ["keep", "extra"])
        self.assertEqual(result["coverage"]["rule_items"][1]["coverage"], "unsupported")
        self.assertNotIn("customer-only", " ".join(row["item"] for row in result["findings"]))


if __name__ == "__main__":
    unittest.main()
