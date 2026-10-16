"""Worker engine_runner coverage for every target and unused references."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ppap_core.audits import RoundInput, RoundWork
from ppap_core.contracts import AuditEvidence, AuditResultItem, EvidenceSource


class _EngineStore:
    def __init__(self) -> None:
        self._versions = {}
        self._bodies = {}

    def upload(self, name: str, body: bytes) -> dict:
        index = len(self._versions) + 1
        file_id = f"file-{index}"
        version_id = f"version-{index}"
        version = SimpleNamespace(
            file_id=file_id,
            id=version_id,
            original_filename=name,
        )
        self._versions[(file_id, version_id)] = version
        self._bodies[version_id] = body
        return {"file_id": file_id, "id": version_id}

    def get_version(self, file_id: str, version_id: str):
        return self._versions.get((file_id, version_id))

    def version_bytes(self, version) -> bytes:
        return self._bodies[version.id]


class EngineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.store = _EngineStore()

    def _upload(self, name: str, body: bytes) -> dict:
        return self.store.upload(name, body)

    def test_unreadable_frozen_context_is_an_execution_error_not_a_missing_document_ng(self):
        from io import BytesIO

        from openpyxl import Workbook
        from ppap_worker import engine_runner

        workbook = Workbook()
        workbook.active.append(["工序号", "工序名称"])
        workbook.active.append(["10", "成型"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        file = self._upload("控制计划.xlsx", output.getvalue())
        target = RoundInput(
            "target",
            file["file_id"],
            file["id"],
            "控制计划.xlsx",
            "ready",
            rule_version_id="rule",
            rule_body={
                "kind": "engine",
                "type": "控制计划",
                "rule_profile": "v3",
                "rules": [
                    {"id": "cross", "category": "关联工序", "rule_to_check": "过程流程图与控制计划列项及一致性审核", "type_of_rule": "text"},
                    {"id": "local", "category": "本文件列", "rule_to_check": "特殊特性分类与产品/过程列存在性审核", "type_of_rule": "text"},
                ],
            },
        )
        for filename in ("过程流程图.xlsx", "附件.xlsx"):
            with self.subTest(filename=filename):
                unavailable = RoundInput("reference", "missing-file", "missing-version", filename, "ready")
                failed = engine_runner(RoundWork("round-unreadable-reference", "tenant", [target, unavailable]), self.store)
                self.assertEqual([row["status"] for row in failed["findings"]], ["ERR", "NG"])
                self.assertNotIn("缺少关联文档", failed["findings"][0]["suggestions"])
        absent = engine_runner(RoundWork("round-no-reference", "tenant", [target]), self.store)
        self.assertEqual([row["status"] for row in absent["findings"]], ["NG", "NG"])
        self.assertIn("缺少关联文档", absent["findings"][0]["suggestions"])
        reference = self._upload("过程流程图.xlsx", output.getvalue())
        readable = RoundInput("reference", reference["file_id"], reference["id"], "过程流程图.xlsx", "ready")
        complete = engine_runner(RoundWork("round-readable-reference", "tenant", [target, readable]), self.store)
        self.assertEqual([row["status"] for row in complete["findings"]], ["PASS", "NG"])

    def test_all_unavailable_targets_keep_individual_errors_without_engine_execution(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        work = RoundWork(
            "round-missing",
            "tenant",
            [RoundInput("target", f"file-{index}", f"version-{index}", f"missing-{index}.xlsx", "ready", rule_version_id="rule", rule_body={"kind": "engine", "type": "控制计划"}) for index in (1, 2)],
        )
        with patch.object(app_runtime, "run_audit_engine", side_effect=AssertionError("missing files must not execute")):
            result = engine_runner(work, self.store)
        self.assertEqual([(row["item_key"], row["status"]) for row in result["findings"]], [("version-1:execution", "ERR"), ("version-2:execution", "ERR")])
        self.assertEqual(result["coverage"]["checked"], [])
        self.assertTrue(result["coverage"]["locator_gap"])

    def test_failed_temporary_write_is_removed_and_other_targets_continue(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        files = [self._upload(name, b"test") for name in ("broken.xlsx", "healthy.xlsx")]
        work = RoundWork(
            "round-write",
            "tenant",
            [
                RoundInput("target", file["file_id"], file["id"], name, "ready", rule_version_id="rule", rule_body={"kind": "engine", "type": "控制计划"})
                for file, name in zip(files, ("broken.xlsx", "healthy.xlsx"))
            ],
        )
        created = []
        create_file = tempfile.NamedTemporaryFile

        def create(*args, **kwargs):
            handle = create_file(*args, **kwargs)
            created.append(Path(handle.name))
            if len(created) == 1:

                def fail_write(data):
                    raise OSError("test write failure")

                handle.write = fail_write
            return handle

        try:
            with (
                patch.object(tempfile, "NamedTemporaryFile", create),
                patch.object(app_runtime, "run_audit_engine", return_value=[AuditResultItem(status="PASS", classification="规则", item="完成", priority="P0", suggestions="")]),
            ):
                result = engine_runner(work, self.store)
            self.assertEqual([row["status"] for row in result["findings"]], ["ERR", "PASS"])
            self.assertTrue(all(not path.exists() for path in created))
        finally:
            for path in created:
                path.unlink(missing_ok=True)

    def test_one_unreadable_target_does_not_discard_other_frozen_file_results(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        files = [self._upload(name, name.encode()) for name in ("broken.xlsx", "healthy.xlsx", "last.xlsx")]
        work = RoundWork(
            "round-files",
            "tenant",
            [
                RoundInput("target", file["file_id"], file["id"], name, "ready", rule_version_id="rule-1", rule_body={"kind": "engine", "type": "控制计划", "rule_profile": "v3"})
                for file, name in zip(files, ("broken.xlsx", "healthy.xlsx", "last.xlsx"))
            ],
        )
        calls = []
        original_read = self.store.version_bytes

        def read(version):
            if version.id == files[0]["id"]:
                raise OSError("private storage failure must not reach the product")
            return original_read(version)

        def run(audit, rule, group_context=None):
            calls.append(audit.audit_id)
            self.assertNotIn(files[0]["id"], group_context.by_audit_id)
            self.assertEqual(group_context.get_files_by_type("过程流程图"), [])
            with self.assertRaises(OSError):
                group_context.get_files_by_type("控制计划")
            return [AuditResultItem(status="PASS", classification="规则", item="健康文件检查", priority="P0", suggestions="")]

        with patch.object(self.store, "version_bytes", read), patch.object(app_runtime, "run_audit_engine", run):
            result = engine_runner(work, self.store)
        self.assertEqual(calls, [files[1]["id"], files[2]["id"]])
        failures = [row for row in result["findings"] if row["status"] == "ERR"]
        self.assertEqual([row["locator"]["file_version_id"] for row in failures], [files[0]["id"]])
        self.assertEqual(len([row for row in result["findings"] if row["status"] == "PASS"]), 2)
        self.assertNotIn("private storage failure", str(result))

    def test_rule_and_engine_failures_keep_prior_and_later_file_results_in_one_worker_commit(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import process_claimed

        names = ("first.xlsx", "unknown.xlsx", "engine-broken.xlsx", "last.xlsx")
        files = [self._upload(name, name.encode()) for name in names]
        inputs = [
            RoundInput(
                "target",
                file["file_id"],
                file["id"],
                name,
                "ready",
                rule_version_id=f"rule-{index}",
                rule_body={"kind": "engine", "type": "unknown" if index == 1 else "控制计划", "rule_profile": "v3"},
            )
            for index, (file, name) in enumerate(zip(files, names))
        ]
        work = RoundWork("round-partial", "tenant", inputs)
        completed = []
        self.store.round_work = lambda identity: work
        self.store.complete_job = lambda claimed, ident, result, outcome: completed.append((claimed.id, result, outcome)) or True

        def run(audit, rule, group_context=None):
            if audit.audit_id == files[2]["id"]:
                raise RuntimeError("private engine details")
            return [AuditResultItem(status="PASS", classification="规则", item="健康文件检查", priority="P0", suggestions="")]

        claimed = SimpleNamespace(id="job-partial", kind="audit", round_id=work.round_id, tenant_id=work.tenant_id, payload={}, attempt=1)
        with patch.object(app_runtime, "run_audit_engine", run):
            result = process_claimed(self.store, claimed, "test-worker")
        by_file = {row["locator"]["file_version_id"]: row["status"] for row in result["findings"]}
        self.assertEqual(by_file, {files[0]["id"]: "PASS", files[1]["id"]: "ERR", files[2]["id"]: "ERR", files[3]["id"]: "PASS"})
        self.assertEqual(completed, [(claimed.id, result, "succeeded")])
        self.assertNotIn("private engine details", str(result))

    def test_runs_each_target_with_its_bound_rule_and_keeps_references_in_context_only(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        first = self._upload("a.xlsx", b"one")
        second = self._upload("b.xlsx", b"two")
        reference = self._upload("context.pdf", b"ref")
        work = RoundWork(
            "round-multi",
            "t1",
            [
                RoundInput(
                    "target", first["file_id"], first["id"], "a.xlsx", "ready", rule_version_id="rule-1", rule_title="控制计划", rule_body={"kind": "engine", "rule_profile": "v3", "type": "控制计划"}
                ),  # noqa: E501
                RoundInput(
                    "target",
                    second["file_id"],
                    second["id"],
                    "b.xlsx",
                    "ready",
                    rule_version_id="rule-2",
                    rule_title="PFMEA",
                    rule_body={"kind": "engine", "rule_profile": "v2", "type": "过程失效模式及后果分析"},
                ),  # noqa: E501
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
                        required="structure",
                        used="structure",
                        source_format="xlsx",
                        prepared_format="xlsx",
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
                    "target",
                    first["file_id"],
                    first["id"],
                    "plan.xlsx",
                    "ready",
                    rule_version_id="rule-keep",
                    rule_title="控制计划",
                    rule_body=body,
                    rule_execution=execution,
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
                        required="structure",
                        used="structure",
                        source_format="xlsx",
                        prepared_format="xlsx",
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

    def test_cross_file_type_uses_standard_inference_before_rule_type(self):
        from ppap_excel_audit_engine import app_runtime
        from ppap_worker import engine_runner

        inferred = self._upload("控制计划.xlsx", b"plan")
        fallback = self._upload("a.xlsx", b"pfmea")
        unknown = self._upload("notes.pdf", b"ref")
        work = RoundWork(
            "round-types",
            "t1",
            [
                RoundInput(
                    "target",
                    inferred["file_id"],
                    inferred["id"],
                    "控制计划.xlsx",
                    "ready",
                    rule_version_id="rule-other",
                    rule_title="其他",
                    rule_body={"kind": "engine", "rule_profile": "v3", "type": "过程失效模式及后果分析"},
                ),
                RoundInput(
                    "target",
                    fallback["file_id"],
                    fallback["id"],
                    "a.xlsx",
                    "ready",
                    rule_version_id="rule-pfmea",
                    rule_title="PFMEA",
                    rule_body={"kind": "engine", "rule_profile": "v2", "type": "过程失效模式及后果分析"},
                ),
                RoundInput("reference", unknown["file_id"], unknown["id"], "notes.pdf", "ready"),
            ],
        )
        calls = []

        def fake_run(audit, rule, group_context=None):
            calls.append(group_context)
            return [
                AuditResultItem(
                    status="PASS",
                    classification="规则",
                    item=audit.file_metadata["original_filename"],
                    priority="P0",
                    suggestions="",
                    evidence=AuditEvidence(
                        required="structure",
                        used="structure",
                        source_format="xlsx",
                        prepared_format="xlsx",
                        sources=(EvidenceSource(kind="structure", locator="A1"),),
                    ),
                )
            ]

        with patch.object(app_runtime, "run_audit_engine", fake_run):
            engine_runner(work, self.store)
        context = calls[0]
        self.assertEqual([item.audit_id for item in context.by_doc_type["控制计划"]], [inferred["id"]])
        self.assertEqual(
            [item.audit_id for item in context.by_doc_type["潜在过程失效模式及后果分析"]],
            [fallback["id"]],
        )
        self.assertNotIn(unknown["id"], [item.audit_id for items in context.by_doc_type.values() for item in items])


if __name__ == "__main__":
    unittest.main()
