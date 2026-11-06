"""Worker's single Core path preserves frozen bindings and partial results.

Transport, authorization callback and guest are controlled in-process. Controller
checks, manifest identity, source hashes, Core execution and product projection are real;
these tests do not claim UDS transport, container isolation or model quality.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.client import DeepError
from agent.core_program import CoreRuntime, run_audit_manifest
from agent.execution import audit_runtime
from agent.run import Controller, ExecutionContext
from audit.audits import RoundInput, RoundWork
from audit.checker_core import ExecutionSnapshot, FrozenInput, FrozenTarget, RuleBinding, checker_digest, runtime_revision
from audit.standard_suites import library_revision
from documents.model_access import DEEPSEEK_MODEL
from product.storage import ObjectMissing
from worker import process_claimed

CHECKER = b"""from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def result(context, status="PASS", suggestions=""):
    return AuditResultItem(
        status=status, classification="rule", item=context.clause_id, priority="P0",
        suggestions=suggestions, clause_id=context.clause_id, checks_performed=1,
        evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
            sources=(EvidenceSource(kind="structure", locator='{"kind":"xlsx_cell","sheet":"Sheet","cell":"A1"}'),)),
    )

def check(context):
    return result(context)
"""


class _ControlledGuest:
    def __init__(self, directory):
        self.directory = directory
        self.calls = 0
        self.manifests = []

    def run(self, job, inputs, work, deadline, cancelled):
        self.calls += 1
        self.manifests.append(json.loads((inputs / "manifest.json").read_text()))
        grant = self.directory / f"grant-{self.calls}"
        grant.mkdir()
        (grant / "model.json").write_text(
            json.dumps(
                {
                    "base_url": "http://127.0.0.1:1/v1",
                    "token": "controlled-local-capability-not-a-provider-key",
                    "model": DEEPSEEK_MODEL,
                    "wall_seconds": 100,
                    "max_turns": 20,
                }
            )
        )
        run_audit_manifest(inputs, work, grant)


class CoreRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bodies = {}
        self.inputs = []
        self.frozen_inputs = []
        self.targets = []
        self.suites = {}
        self.contexts = []
        self.events = []
        self.completed = []
        self.guest = _ControlledGuest(Path(self.temp.name))
        self.revision = runtime_revision("test-policy", library_revision())
        self.settings = SimpleNamespace(deep_controller_socket="/not-opened-controlled-transport", deep_execution_revision=lambda identity=None: "test-policy")

    def add_input(self, filename, *, role="target", document_type="控制计划", rules=None, code=CHECKER, missing=False, entrypoints=None):
        identity = f"version-{len(self.inputs) + 1}"
        body = filename.encode()
        digest = hashlib.sha256(body).hexdigest()
        rules = rules or {"type": document_type or "其他", "rules": [{"id": "check", "rule_to_check": "已冻结的客户要求"}]}
        revision = f"rule-{identity}" if role == "target" else None
        self.inputs.append(RoundInput(role, f"file-{identity}", identity, filename, "ready", rule_version_id=revision, rule_body=rules if role == "target" else None, sha256=digest))
        self.frozen_inputs.append(FrozenInput(identity, digest, role, filename, document_type, file_id=f"file-{identity}"))
        if not missing:
            self.bodies[identity] = body
        if role == "target":
            programs = {"checker.py": code}
            program_digest = checker_digest(programs)
            binding = RuleBinding(f"suite-{identity}", program_digest, entrypoints if entrypoints is not None else {row["id"]: "checker:check" for row in rules["rules"]})
            self.targets.append(FrozenTarget(identity, revision, binding.suite_key, "a" * 64, rules, binding))
            self.suites[revision] = SimpleNamespace(revision=SimpleNamespace(resolved_digest="a" * 64), files=programs)
        return identity

    def execute(self, *, source=None, authorize=None, payload=None):
        self.snapshot = ExecutionSnapshot(
            "tenant", "initiator", tuple(self.frozen_inputs), tuple(self.targets), self.revision, library_revision(), {"provider": "deepseek", "model": DEEPSEEK_MODEL}, round_id="round"
        )
        work = RoundWork("round", "tenant", self.inputs, initiating_user_id="initiator")
        claimed = SimpleNamespace(
            id="job",
            kind="audit",
            round_id=work.round_id,
            tenant_id=work.tenant_id,
            attempt=1,
            payload={
                "executor": "ppap-checker-core",
                "executor_revision": self.revision,
                "checker_snapshot_digest": self.snapshot.input_digest,
                "model_identity": dict(self.snapshot.model_identity),
                **(payload or {}),
            },
        )

        def complete(job, worker, result, outcome):
            self.completed.append((job.id, worker, result, outcome))
            return True

        def allowed(spec):
            self.events.append("authorize")
            self.assertEqual((spec.tenant_id, spec.round_id, spec.initiating_user_id, spec.job_id, spec.attempt), ("tenant", "round", "initiator", "job", 1))
            self.assertEqual(spec.checker_snapshot_digest, self.snapshot.input_digest)
            if authorize:
                authorize(spec)

        def read(identity, limit):
            self.assertIn("authorize", self.events)
            self.events.append(identity)
            if source:
                return source(identity, limit)
            if identity not in self.bodies:
                raise ObjectMissing("private object path must not reach the product")
            return self.bodies[identity]

        runtime = CoreRuntime(self.guest, lambda spec: self.snapshot, lambda revision, tenant: self.suites[revision])
        controller = Controller(runtime, read, executor_revision=self.revision, authorize=allowed)
        store = SimpleNamespace(settings=self.settings, round_work=lambda identity: work, complete_job=complete, audit_progress=lambda *args: True, model_user_for_job=lambda job: "initiator")

        def transport(spec, *, cancelled, on_progress):
            return controller.handle(spec, ExecutionContext(cancelled=cancelled, on_progress=on_progress))

        original_context = audit_runtime.CheckerContext

        def observe(*args, **kwargs):
            context = original_context(*args, **kwargs)
            self.contexts.append(context)
            return context

        with patch("agent.client.DeepClient") as client, patch.object(audit_runtime, "CheckerContext", side_effect=observe):
            client.return_value.execute.side_effect = transport
            result = process_claimed(store, claimed, "test-worker")
        self.assertEqual(len(self.completed), 1)
        self.assertIs(self.completed[0][2], result)
        self.assertNotIn("controlled-local-capability", str(result))
        return result

    def test_unreadable_frozen_reference_is_unknown_not_a_missing_document_ng(self):
        code = (
            CHECKER
            + rb"""
def check(context):
    if context.clause_id == "local":
        return result(context, "NG", "Local column is absent")
    peers = context.group_context.get_files_by_type("\u8fc7\u7a0b\u6d41\u7a0b\u56fe")
    return result(context, "PASS" if peers else "NG", "" if peers else "Missing associated document")
"""
        )
        rules = {"type": "控制计划", "rules": [{"id": "cross", "rule_to_check": "关联工序"}, {"id": "local", "rule_to_check": "本文件列"}]}
        self.add_input("控制计划.xlsx", rules=rules, code=code)
        absent = self.execute()
        self.assertEqual([row["status"] for row in absent["findings"]], ["NG", "NG"])
        self.assertIn("Missing associated document", absent["findings"][0]["suggestions"])
        # Reuse the same frozen target with an explicitly frozen, unavailable peer.
        self.completed.clear()
        reference = self.add_input("过程流程图.xlsx", role="reference", document_type="过程流程图", missing=True)
        for frozen_type in ("过程流程图", None):
            with self.subTest(frozen_type=frozen_type):
                self.completed.clear()
                self.frozen_inputs[-1] = replace(self.frozen_inputs[-1], document_type=frozen_type)
                failed = self.execute()
                self.assertEqual([row["status"] for row in failed["findings"]], ["UNABLE", "NG"])
                self.assertNotIn("Missing associated document", failed["findings"][0]["suggestions"])
                self.assertNotIn("private object path", str(failed))
        self.completed.clear()
        self.frozen_inputs[-1] = replace(self.frozen_inputs[-1], document_type="过程流程图")
        self.bodies[reference] = "过程流程图.xlsx".encode()
        complete = self.execute()
        self.assertEqual([row["status"] for row in complete["findings"]], ["PASS", "NG"])

    def test_all_unavailable_targets_keep_individual_errors_without_checker_import(self):
        for index in (1, 2):
            self.add_input(f"missing-{index}.xlsx", missing=True, code=b'raise AssertionError("Missing targets must not import checkers")')
        with patch.object(audit_runtime, "_load_program", side_effect=AssertionError("Missing targets must not execute")) as load:
            result = self.execute()
        self.assertEqual([(row["item_key"], row["status"]) for row in result["findings"]], [("version-1:rule:check", "ERR"), ("version-2:rule:check", "ERR")])
        load.assert_not_called()
        self.assertEqual(result["coverage"]["checked"], [])
        self.assertTrue(result["coverage"]["locator_gap"])

    def test_shared_manifest_write_failure_fails_closed_and_cleans_temporary_files(self):
        # Core stages one whole-audit manifest. A shared disk failure is not an
        # authorized missing object and cannot be downgraded to partial success.
        self.add_input("first.xlsx")
        self.add_input("last.xlsx")
        created = []
        write = Path.write_bytes

        def fail_write(path, data):
            if path.name == "source-0.bin":
                write(path, data[:1])
                created.append(path)
                raise OSError("private disk failure")
            return write(path, data)

        with patch.object(Path, "write_bytes", fail_write):
            result = self.execute()
        self.assertEqual(self.completed[0][3], "failed")
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["coverage"]["checked"], [])
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())
        self.assertFalse(created[0].parent.parent.exists())
        self.assertNotIn("private disk failure", str(result))

    def test_one_missing_target_does_not_discard_other_frozen_file_results(self):
        missing = self.add_input("broken.xlsx", missing=True)
        healthy = [self.add_input(name) for name in ("healthy.xlsx", "last.xlsx")]
        result = self.execute()
        self.assertEqual([context.audit_file.audit_id for context in self.contexts], healthy)
        for context in self.contexts:
            self.assertNotIn(missing, context.sources)
            self.assertNotIn(missing, context.paths)
            self.assertNotIn(missing, context.group_context.by_audit_id)
            self.assertEqual(context.group_context.get_files_by_type("过程流程图"), [])
            with self.assertRaises(OSError):
                context.group_context.get_files_by_type("控制计划")
        self.assertEqual({row["locator"]["file_version_id"]: row["status"] for row in result["findings"]}, {missing: "ERR", **dict.fromkeys(healthy, "PASS")})
        self.assertEqual(result["coverage"]["checked"], ["healthy.xlsx", "last.xlsx"])
        self.assertEqual(self.guest.manifests[0]["spec"]["unavailable_sources"], [missing])
        self.assertNotIn(missing, self.guest.manifests[0]["source_paths"])
        self.assertEqual(result["coverage"]["checker_execution"]["input_digest"], self.snapshot.input_digest)
        self.assertEqual(self.completed[0][3], "succeeded")
        self.assertEqual(self.guest.calls, 1)
        self.assertNotIn("private object path", str(result))

    def test_rule_and_checker_failures_keep_prior_and_later_results_in_one_worker_commit(self):
        first = self.add_input("first.xlsx")
        unknown = self.add_input("unknown.xlsx", code=CHECKER + b"\ndef check(context):\n    return None\n")
        broken = self.add_input("checker-broken.xlsx", code=CHECKER + b'\ndef check(context):\n    raise RuntimeError("private checker details")\n')
        last = self.add_input("last.xlsx")
        result = self.execute()
        self.assertEqual({row["locator"]["file_version_id"]: row["status"] for row in result["findings"]}, {first: "PASS", unknown: "ERR", broken: "ERR", last: "PASS"})
        self.assertEqual(self.completed, [("job", "test-worker", result, "succeeded")])
        self.assertEqual(self.guest.calls, 1)
        self.assertNotIn("private checker details", str(result))

    def test_runs_each_target_with_its_frozen_suite_and_references_in_context_only(self):
        first = self.add_input("a.xlsx")
        second = self.add_input("b.xlsx", document_type="过程失效模式及后果分析", rules={"type": "过程失效模式及后果分析", "rules": [{"id": "other", "rule_to_check": "第二套规则"}]})
        reference = self.add_input("context.pdf", role="reference", document_type=None)
        missing = self.add_input("gone.pdf", role="reference", document_type=None, missing=True)
        result = self.execute()
        self.assertEqual([context.audit_file.audit_id for context in self.contexts], [first, second])
        self.assertEqual([context.rule.rule_id for context in self.contexts], [f"rule-{first}", f"rule-{second}"])
        self.assertEqual([context.rule.type for context in self.contexts], ["控制计划", "过程失效模式及后果分析"])
        context = self.contexts[0].group_context
        self.assertEqual(context.task_group_id, "round")
        self.assertEqual(set(context.by_audit_id), {first, second, reference})
        self.assertEqual(context.unavailable_files, {missing: None})
        self.assertEqual(set(context.rules_by_id), {first, second})
        self.assertEqual([item.audit_id for item in context.by_doc_type["控制计划"]], [first])
        self.assertEqual({row["item_key"] for row in result["findings"]}, {f"{first}:rule:check", f"{second}:rule:other"})
        self.assertEqual({row["locator"]["file_version_id"] for row in result["findings"]}, {first, second})
        self.assertEqual({row["locator"]["suite_revision_id"] for row in result["findings"]}, {f"rule-{first}", f"rule-{second}"})
        self.assertEqual(result["coverage"]["checked"], ["a.xlsx", "b.xlsx"])
        self.assertTrue(any("context.pdf" in row for row in result["coverage"]["unchecked"]))
        self.assertTrue(any("gone.pdf" in row for row in result["coverage"]["unchecked"]))
        self.assertFalse(result["coverage"]["locator_gap"])
        self.assertEqual(result["coverage"]["checker_execution"]["input_digest"], self.snapshot.input_digest)

    def test_customer_rule_items_and_explicit_bindings_are_the_machine_checklist(self):
        rules = {
            "type": "控制计划",
            "rules": [
                {"id": "keep", "category": "customer", "priority": "P1", "rule_to_check": "First frozen requirement"},
                {"id": "extra", "rule_to_check": "Arbitrary customer requirement"},
                {"id": "unsupported", "rule_to_check": "No authorized checker"},
            ],
        }
        first = self.add_input("plan.xlsx", rules=rules, entrypoints={"extra": "checker:check", "keep": "checker:check"})
        result = self.execute()
        self.assertEqual([row["item_key"] for row in result["findings"]], [f"{first}:rule:keep", f"{first}:rule:extra"])
        self.assertEqual([row["item"] for row in result["findings"]], ["First frozen requirement", "Arbitrary customer requirement"])
        self.assertEqual(result["findings"][0]["priority"], "P1")
        self.assertEqual([row["locator"]["rule_item_id"] for row in result["findings"]], ["keep", "extra"])
        self.assertEqual([row["id"] for row in result["coverage"]["rule_items"]], ["keep", "extra", "unsupported"])
        self.assertEqual([row["coverage"] for row in result["coverage"]["rule_items"]], ["executed", "executed", "unsupported"])
        self.assertNotIn("status", result["coverage"]["rule_items"][-1])
        self.assertNotIn("item_key", result["coverage"]["rule_items"][-1])

    def test_cross_file_context_uses_frozen_type_not_live_filename_or_rule_inference(self):
        # Admission owns type selection. Worker/Core consume the accepted type,
        # rather than rerouting a historical round from its filename or rule body.
        frozen = self.add_input("控制计划.xlsx", document_type="过程流程图", rules={"type": "过程失效模式及后果分析", "rules": [{"id": "check", "rule_to_check": "Frozen rule"}]})
        fallback = self.add_input("a.xlsx", document_type="过程失效模式及后果分析")
        unknown = self.add_input("notes.pdf", role="reference", document_type=None)
        result = self.execute()
        self.assertEqual([row["status"] for row in result["findings"]], ["PASS", "PASS"])
        context = self.contexts[0].group_context
        self.assertEqual([item.audit_id for item in context.by_doc_type["过程流程图"]], [frozen])
        self.assertEqual([item.audit_id for item in context.by_doc_type["过程失效模式及后果分析"]], [fallback])
        self.assertNotIn("控制计划", context.by_doc_type)
        self.assertNotIn(unknown, [item.audit_id for rows in context.by_doc_type.values() for item in rows])

    def test_authorization_failure_prevents_source_reads_and_guest_execution(self):
        self.add_input("private.xlsx")

        def deny(spec):
            raise DeepError("deep_input_unauthorized")

        result = self.execute(authorize=deny)
        self.assertEqual(self.events, ["authorize"])
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(self.completed[0][3], "failed")
        self.assertEqual(result["failure"]["code"], "deep_input_unauthorized")
        self.assertEqual(result["findings"], [])

    def test_unknown_source_failure_is_not_downgraded_to_missing(self):
        self.add_input("first.xlsx")
        self.add_input("last.xlsx")

        def read(identity, limit):
            raise OSError("private storage outage")

        result = self.execute(source=read)
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(self.completed[0][3], "failed")
        self.assertEqual(result["findings"], [])
        self.assertNotIn("private storage outage", str(result))

    def test_changed_source_bytes_cannot_publish_healthy_partial_results(self):
        self.add_input("first.xlsx")
        changed = self.add_input("changed.xlsx")
        result = self.execute(source=lambda identity, limit: b"tampered" if identity == changed else self.bodies[identity])
        self.assertEqual(result["failure"]["code"], "deep_integrity_mismatch")
        self.assertEqual(result["findings"], [])
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(self.completed[0][3], "failed")

    def test_changed_frozen_checker_cannot_execute_or_publish_results(self):
        identity = self.add_input("plan.xlsx")
        self.suites[f"rule-{identity}"].files = {"checker.py": CHECKER + b"# Changed after acceptance"}
        result = self.execute()
        self.assertEqual(result["failure"]["code"], "deep_integrity_mismatch")
        self.assertEqual(result["findings"], [])
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(self.completed[0][3], "failed")

    def test_wrong_result_clause_cannot_publish_prior_healthy_results(self):
        self.add_input("first.xlsx")
        self.add_input("wrong-clause.xlsx", code=CHECKER + b'\ndef check(context):\n    return result(context).model_copy(update={"clause_id": "foreign"})\n')
        result = self.execute()
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["coverage"]["checked"], [])
        self.assertEqual(self.guest.calls, 1)
        self.assertEqual(self.completed[0][3], "failed")

    def test_job_without_core_snapshot_cannot_fall_back_to_the_old_engine(self):
        self.add_input("plan.xlsx")
        result = self.execute(payload={"checker_snapshot_digest": ""})
        self.assertEqual(self.events, [])
        self.assertEqual(self.guest.calls, 0)
        self.assertEqual(result["findings"], [])
        self.assertEqual(self.completed[0][3], "failed")


if __name__ == "__main__":
    unittest.main()
