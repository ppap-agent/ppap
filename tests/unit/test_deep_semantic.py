"""Small deterministic guards for the migrated semantic core."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

from tests import pathsetup  # noqa: F401

from ppap_deep.semantic.document_tools import snapshot
from ppap_deep.semantic.checker_tools import CheckRequest, check_fields
from ppap_deep.semantic.evidence import EvidenceBudget
from ppap_deep.semantic.guest import _compile_prompt, audit_prompt, validate_plan
from ppap_deep.semantic.guest_model import GrokRunner, ModelGrant, ModelProtocolError, sandbox_profile
from ppap_deep.semantic.models import AuditReport, RulePlan
from ppap_deep.semantic.protocol import RuleRef
from ppap_deep.semantic.trace import AuditTrace, validate_trace
from ppap_deep.semantic.validation import validate_report


REVISION = "a" * 64


def published_source() -> tuple[RuleRef, dict]:
    clause = {"id": "R1", "rule_to_check": "Every record has a frequency."}
    payload = {
        "kind": "published_rule",
        "rule_version_id": "version-1",
        "rule_body": {"kind": "engine", "rules": [clause]},
        "clauses": [clause],
    }
    raw = json.dumps(payload, ensure_ascii=False).encode()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "rule.json"
        path.write_bytes(raw)
        source = snapshot(path)
    rule = RuleRef(
        rule_id="rule-1", sha256=hashlib.sha256(raw).hexdigest(), format=".json",
        rule_version_id="version-1", plan_revision=REVISION, clauses=[clause],
    )
    return rule, source


def plan(locator_id: str = "R1") -> RulePlan:
    rule, _ = published_source()
    return RulePlan.model_validate({
        "source_sha256": rule.sha256,
        "plan_revision": REVISION,
        "rules": [{
            "id": "R1", "description": "Every record has a frequency.", "priority": "P1",
            "source_locator": {"kind": "published_clause", "clause_id": locator_id},
            "scope": "all body rows", "predicate": {"kind": "semantic", "question": "Is frequency present?"},
            "missing_evidence": "UNABLE", "ambiguities": [],
            "empty_scope_status": "UNABLE", "empty_scope_basis": "The clause does not define an empty-range result.",
        }],
    })


DOCUMENT = {
    "source": {"file": "target.xlsx", "sha256": "b" * 64, "format": ".xlsx"},
    "tables": [{"id": "Sheet1", "rows": 1, "cols": 1, "cells": {"A1": {"value": "f", "state": "known"}}, "merges": []}],
    "limitations": [],
}
CITATION = {"document_id": "target-1", "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}}


def field_check_request() -> dict:
    return {
        "binding": {
            "id": "data", "document_id": "target-1", "source_sha256": "b" * 64,
            "table": "Sheet1", "header_rows": [1], "first_row": 2, "last_row": 4,
            "columns": {"frequency": "A", "measurement": "B"}, "excluded_rows": [],
            "mapping_basis": "observed headers", "boundary_basis": "observed body rows",
        },
        "checks": [
            {"id": "presence", "kind": "field_nonempty", "field": "frequency"},
            {"id": "minimum", "kind": "numeric_at_least", "field": "measurement", "minimum": 2},
        ],
    }


FIELD_DOCUMENT = {
    "source": {"file": "target.xlsx", "sha256": "b" * 64, "format": ".xlsx"},
    "tables": [{
        "id": "Sheet1", "rows": 4, "cols": 2, "merges": [],
        "cells": {
            "A1": {"value": "Frequency", "state": "known"},
            "B1": {"value": "Measurement", "state": "known"},
            "A2": {"value": "daily", "state": "known"},
            "B2": {"value": 1, "state": "known"},
            "B3": {"value": "5", "state": "known"},
            "A4": {"value": None, "state": "unknown_formula_cache"},
            "B4": {"value": None, "state": "unknown_formula_cache"},
        },
    }],
    "limitations": [],
}


class SemanticBoundaryTests(unittest.TestCase):
    def test_compile_review_and_audit_preserve_explicit_else_branch(self) -> None:
        from ppap_deep.semantic.guest import _compile_review_prompt

        rule, _ = published_source()
        prompts = (
            _compile_prompt(rule, ["R1"]),
            _compile_review_prompt([]),
            audit_prompt({"rule-1": plan()}, {"target-1": "rule-1"}),
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt[:40]):
                self.assertIn("A=false, B=true", prompt)
                self.assertIn("A=true, B=false", prompt)
                self.assertIn("explicit otherwise", prompt)
                self.assertIn("genuine applicability condition", prompt)
                self.assertIn("known blank", prompt)
                self.assertIn("unknown symbol mapping", prompt)

    def test_audit_prompt_requires_decisive_public_and_private_evidence(self) -> None:
        prompt = audit_prompt({"rule-1": plan()}, {"target-1": "rule-1"})
        self.assertIn("report.checks[].evidence", prompt)
        self.assertIn("decisive row or field", prompt)
        self.assertIn("trace", prompt)
        self.assertIn("header-only", prompt)

    def test_excel_temporal_values_are_json_safe_without_losing_precision(self) -> None:
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temporal.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append([time(9, 15, 30, 125000), date(2026, 9, 26), datetime(2026, 9, 26, 9, 15)])
            workbook.save(path)
            workbook.close()
            result = snapshot(path)

        cells = json.loads(json.dumps(result))["tables"][0]["cells"]
        self.assertEqual(cells["A1"], {"value": "09:15:30.125000", "state": "known"})
        self.assertEqual(cells["B1"]["value"], "2026-09-26T00:00:00")
        self.assertEqual(cells["C1"]["value"], "2026-09-26T09:15:00")

    def test_prerequisite_only_pass_cannot_hide_empty_applicable_scope(self) -> None:
        proof = AuditTrace.model_validate({"proofs": [{
            "kind": "semantic", "audit_id": "target-1", "rule_id": "R1", "scope_basis": "No triggering body rows",
            "records": [{"record_id": "headers", "role": "prerequisite", "status": "PASS",
                         "reason": "Required columns exist", "evidence": [CITATION]}],
        }]})
        report = AuditReport.model_validate({"checks": [{
            "audit_id": "target-1", "rule_id": "R1", "status": "PASS", "reason": "No trigger, so passed",
            "total": 1, "checked": 1, "unknown": 0, "evidence": [CITATION],
        }]})
        validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})
        with self.assertRaisesRegex(ValueError, "contradicts replayed"):
            validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": plan()})
        self.assertEqual(report.checks[0].reason, "No trigger, so passed")

        report.checks[0].status = "UNABLE"
        report.checks[0].reason = "The rule does not define the empty trigger set"
        report.checks[0].diagnostic = "rule_ambiguity"
        report.checks[0].evidence = []
        validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})
        validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": plan()})

    def test_excluded_candidates_and_passed_prerequisites_do_not_make_scope_nonempty(self) -> None:
        proof = AuditTrace.model_validate({"proofs": [{
            "kind": "semantic", "audit_id": "target-1", "rule_id": "R1",
            "scope_basis": "The only candidate does not meet the trigger",
            "records": [
                {"record_id": "headers", "role": "prerequisite", "status": "PASS",
                 "reason": "Columns exist", "evidence": [CITATION]},
                {"record_id": "row-1", "role": "excluded", "exclusion_basis": "Trigger is absent",
                 "status": "NA", "reason": "Not triggered", "evidence": [CITATION]},
            ],
        }]})
        report = AuditReport.model_validate({"checks": [{
            "audit_id": "target-1", "rule_id": "R1", "status": "PASS",
            "reason": "Nothing triggered, so passed", "total": 2, "checked": 2, "unknown": 0,
            "evidence": [CITATION],
        }]})
        with self.assertRaisesRegex(ValueError, "contradicts replayed"):
            validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": plan()})
        report.checks[0].status = "UNABLE"
        report.checks[0].reason = "The rule does not define an empty trigger set"
        report.checks[0].diagnostic = "rule_ambiguity"
        report.checks[0].evidence = []
        validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})
        validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": plan()})

    def test_explicit_empty_scope_policy_and_missing_prerequisite(self) -> None:
        for empty_policy, record_status, role, expected_status in (
            ("PASS", "PASS", "prerequisite", "PASS"),
            ("PASS", "NA", "excluded", "PASS"),
            ("NA", "PASS", "prerequisite", "NA"),
            ("NA", "NA", "excluded", "NA"),
            ("UNABLE", "NG", "prerequisite", "NG"),
        ):
            with self.subTest(empty_policy=empty_policy, record_status=record_status):
                rule_plan = plan().model_dump(mode="json")
                rule_plan["rules"][0]["empty_scope_status"] = empty_policy
                rule_plan["rules"][0]["empty_scope_basis"] = "The effective clause explicitly defines this empty-range branch"
                selected_plan = RulePlan.model_validate(rule_plan)
                proof = AuditTrace.model_validate({"proofs": [{
                    "kind": "semantic", "audit_id": "target-1", "rule_id": "R1", "scope_basis": "No applicable body rows",
                    "records": [{"record_id": "observation", "role": role, "status": record_status,
                                 "exclusion_basis": "Trigger confirmed absent" if role == "excluded" else None,
                                 "reason": "Observed rule scope", "evidence": [CITATION]}],
                }]})
                report = AuditReport.model_validate({"checks": [{
                    "audit_id": "target-1", "rule_id": "R1", "status": expected_status, "reason": "Observed outcome",
                    "total": 1, "checked": 1, "unknown": 0, "evidence": [CITATION],
                }]})
                validate_report(report, {"target-1": selected_plan}, {"target-1": DOCUMENT})
                validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": selected_plan})

    def test_applicable_record_requires_trigger_basis(self) -> None:
        record = {"record_id": "row-1", "role": "applicable", "status": "PASS",
                  "reason": "Reviewed", "evidence": [CITATION]}
        with self.assertRaisesRegex(ValueError, "trigger basis"):
            AuditTrace.model_validate({"proofs": [{"kind": "semantic", "audit_id": "target-1", "rule_id": "R1",
                                                 "scope_basis": "one row", "records": [record]}]})
        record["applicability_basis"] = "This row meets the rule's stated condition"
        no_source = {**record, "evidence": []}
        with self.assertRaisesRegex(ValueError, "source evidence"):
            AuditTrace.model_validate({"proofs": [{"kind": "semantic", "audit_id": "target-1", "rule_id": "R1",
                                                 "scope_basis": "one row", "records": [no_source]}]})
        proof = AuditTrace.model_validate({"proofs": [{"kind": "semantic", "audit_id": "target-1", "rule_id": "R1",
                                             "scope_basis": "one row", "records": [record]}]})
        report = AuditReport.model_validate({"checks": [{"audit_id": "target-1", "rule_id": "R1", "status": "PASS",
                                               "reason": "Reviewed", "total": 1, "checked": 1, "unknown": 0,
                                               "evidence": [CITATION]}]})
        validate_trace(proof, report, {"target-1": DOCUMENT}, {"target-1": plan()})

    def test_old_plan_cannot_silently_inherit_empty_scope_policy(self) -> None:
        prior = plan().model_dump(mode="json")
        del prior["rules"][0]["empty_scope_status"]
        with self.assertRaisesRegex(ValueError, "empty_scope_status"):
            RulePlan.model_validate(prior)

    def test_receipt_without_artifact_is_a_model_protocol_failure(self) -> None:
        runner = GrokRunner({"base_url": "https://example.invalid/v1", "token": "x" * 32,
                             "model": "test", "max_turns": 2, "wall_seconds": 10})
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "_run", return_value={"result": '{"completed":true,"artifact":"execution.json"}'}) as run:
                with self.assertRaisesRegex(ModelProtocolError, "Missing or invalid audit artifact"):
                    runner.run_artifact(Path(directory), "audit", AuditReport, 9999999999.0, reasoning_effort="xhigh")
                self.assertIsNone(run.call_args.args[2])

    def test_narrow_field_checks_keep_typed_source_and_unknowns(self) -> None:
        result = check_fields(FIELD_DOCUMENT, CheckRequest.model_validate(field_check_request()))
        self.assertEqual(result.source["sha256"], "b" * 64)
        self.assertEqual(result.limitation, "local_condition_only; mapping_and_rule_applicability_not_verified")
        presence, minimum = result.checks
        self.assertEqual((presence.local_condition_status, presence.total, presence.checked, presence.unknown, presence.violations_count), ("NG", 3, 2, 1, 1))
        self.assertEqual(presence.violations[0].citation.model_dump()["locator"], {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A3"})
        self.assertEqual(presence.unknown_examples[0].citation.model_dump()["locator"]["cell"], "A4")
        self.assertEqual((minimum.local_condition_status, minimum.checked, minimum.unknown, minimum.violations_count), ("NG", 1, 2, 1))
        self.assertEqual((minimum.violation_rows, minimum.unknown_rows), ([2], [3, 4]))
        self.assertEqual(minimum.violations[0].citation.model_dump()["locator"]["cell"], "B2")
        self.assertEqual([item.citation.model_dump()["locator"]["cell"] for item in minimum.unknown_examples], ["B3", "B4"])

    def test_narrow_field_checks_reject_wrong_source_and_unmapped_field(self) -> None:
        request = field_check_request()
        request["binding"]["source_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "source version"):
            check_fields(FIELD_DOCUMENT, CheckRequest.model_validate(request))
        request = field_check_request()
        request["checks"][0]["field"] = "missing"
        with self.assertRaisesRegex(ValueError, "absent from reviewed binding"):
            check_fields(FIELD_DOCUMENT, CheckRequest.model_validate(request))

    def test_narrow_field_check_empty_scope_is_unable(self) -> None:
        request = field_check_request()
        request["checks"] = request["checks"][:1]
        request["binding"]["excluded_rows"] = [
            {"row": row, "reason_type": "non_record", "reason": "independently reviewed non-record",
             "evidence_locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": f"A{row}"}}
            for row in (2, 3, 4)
        ]
        result = check_fields(FIELD_DOCUMENT, CheckRequest.model_validate(request))
        self.assertEqual((result.checks[0].local_condition_status, result.checks[0].total), ("UNABLE", 0))

    def test_narrow_field_check_keeps_late_violation_rows_after_sample_cap(self) -> None:
        source = json.loads(json.dumps(FIELD_DOCUMENT))
        source["tables"][0]["rows"] = 90
        request = field_check_request()
        request["binding"]["last_row"] = 90
        request["checks"] = request["checks"][:1]
        result = check_fields(source, CheckRequest.model_validate(request)).checks[0]
        self.assertEqual(result.violation_rows[-1], 90)
        self.assertEqual(len(result.violations), 64)

    def test_narrow_field_checker_command_writes_complete_result(self) -> None:
        tool = Path(__file__).resolve().parents[2] / "packages/ppap_deep/semantic/checker_tools.py"
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            source, request, output = (location / name for name in ("snapshot.json", "request.json", "result.json"))
            source.write_text(json.dumps(FIELD_DOCUMENT))
            request.write_text(json.dumps(field_check_request()))
            completed = subprocess.run(
                [sys.executable, str(tool), str(source), "--request", str(request), "--out", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["checks"][0]["violations"][0]["citation"]["locator"]["cell"], "A3")

    def test_runner_counts_only_stream_tool_pairs_and_compact_boundaries(self) -> None:
        grant = {
            "base_url": "http://127.0.0.1:12345/v1", "token": "x" * 32, "model": "test",
            "max_turns": 2, "wall_seconds": 10,
        }
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "call-2", "name": "read_file", "input": {}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "ok", "is_error": False},
                {"type": "tool_result", "tool_use_id": "orphan", "content": "?", "is_error": False},
                {"type": "tool_result", "tool_use_id": "call-1", "content": "duplicate", "is_error": False},
            ]}},
            {"type": "system", "subtype": "compact_boundary"},
            {"type": "result", "stop_reason": "end_turn", "is_error": False,
             "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]

        def launch(*_args: object, **kwargs: object) -> object:
            stdout = kwargs["stdout"]
            for event in events:
                stdout.write((json.dumps(event) + "\n").encode())
            stdout.flush()

            class FinishedProcess:
                returncode = 0

                def poll(self) -> int:
                    return 0

                def wait(self) -> int:
                    return 0

            return FinishedProcess()

        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=10001):
                runner = GrokRunner(grant)
                with tempfile.TemporaryDirectory() as directory:
                    with patch("ppap_deep.semantic.guest_model.subprocess.Popen", side_effect=launch):
                        for index in range(2):
                            stage = Path(directory) / str(index)
                            stage.mkdir()
                            runner._run(stage, "test", {"type": "object"}, 9999999999.0, reasoning_effort="low")
                            config = tomllib.loads((stage / "grok-home/config.toml").read_text())
                            self.assertNotIn("session", config)
                            self.assertNotIn("context_window", config["model"]["test"])
        self.assertEqual((runner.tool_calls, runner.completed_tool_calls, runner.compactions), (4, 2, 2))
        self.assertEqual((runner.input_tokens, runner.output_tokens), (20, 10))

    def test_loopback_http_gateway_requires_explicit_low_privilege_container_mode(self) -> None:
        grant = {"base_url": "http://127.0.0.1:12345/v1", "token": "x" * 32, "model": "test", "max_turns": 2, "wall_seconds": 10}
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=10001):
                GrokRunner(grant)
                GrokRunner({**grant, "base_url": "http://localhost:12345/v1"})
                with self.assertRaises(ValueError):
                    GrokRunner({**grant, "base_url": "http://example.test:12345/v1"})
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=0):
                with self.assertRaises(ValueError):
                    GrokRunner(grant)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                GrokRunner(grant)
            GrokRunner({**grant, "base_url": "https://example.test/v1"})

    def test_container_runner_does_not_start_or_kill_its_own_process_group(self) -> None:
        grant = {"base_url": "http://127.0.0.1:12345/v1", "token": "x" * 32, "model": "test", "max_turns": 2, "wall_seconds": 10}
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=10001):
                runner = GrokRunner(grant)
                with tempfile.TemporaryDirectory() as directory:
                    with patch("ppap_deep.semantic.guest_model.subprocess.Popen") as popen:
                        popen.return_value.poll.return_value = 0
                        popen.return_value.returncode = 0
                        with patch("ppap_deep.semantic.guest_model.os.killpg") as killpg:
                            with self.assertRaises(ModelProtocolError):
                                runner._run(Path(directory), "test", {"type": "object"}, 9999999999.0, reasoning_effort="low")
                            self.assertFalse(popen.call_args.kwargs["start_new_session"])
                            killpg.assert_not_called()

    def test_container_only_requires_explicit_profile_and_guest_uid(self) -> None:
        with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=10001):
            for profile in ("off", "ppap"):
                with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": profile}, clear=True):
                    self.assertEqual(sandbox_profile(), profile)
            for environment in (
                {},
                {"PPAP_GUEST_ISOLATION": "smolvm", "PPAP_GUEST_SANDBOX": "ppap"},
                {"PPAP_GUEST_ISOLATION": "container-only"},
                {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "strict"},
            ):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ModelProtocolError):
                        sandbox_profile()
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=0):
                with self.assertRaises(ModelProtocolError):
                    sandbox_profile()

    def test_gvisor_still_requires_kernel_marker_and_guest_uid(self) -> None:
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "gvisor"}, clear=True):
            with patch("ppap_deep.semantic.guest_model.os.getuid", return_value=10001):
                with patch("ppap_deep.semantic.guest_model.Path.is_file", return_value=True):
                    self.assertEqual(sandbox_profile(), "off")
                with patch("ppap_deep.semantic.guest_model.Path.is_file", return_value=False):
                    with self.assertRaises(ModelProtocolError):
                        sandbox_profile()

    def test_published_locator_resolves_effective_clause(self) -> None:
        rule, source = published_source()
        validate_plan(plan(), rule, source, EvidenceBudget())

    def test_wrong_published_clause_is_rejected(self) -> None:
        rule, source = published_source()
        with self.assertRaisesRegex(ValueError, "Published clause locator"):
            validate_plan(plan("R2"), rule, source, EvidenceBudget())

    def test_published_source_cannot_omit_effective_clause(self) -> None:
        clause = {"id": "R1", "rule_to_check": "First"}
        omitted = {"id": "R2", "rule_to_check": "Second"}
        payload = {"kind": "published_rule", "rule_version_id": "v1", "rule_body": {"rules": [clause, omitted]}, "clauses": [clause]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rule.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "clauses differ"):
                snapshot(path)

    def test_missing_clause_is_rejected(self) -> None:
        report = AuditReport.model_validate({"checks": [{
            "audit_id": "target-1", "rule_id": "other", "status": "UNABLE", "reason": "unknown",
            "total": 1, "checked": 0, "unknown": 1, "evidence": [], "diagnostic": "unsupported_structure",
        }]})
        with self.assertRaisesRegex(ValueError, "缺项"):
            validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})

    def test_unknown_scope_cannot_pass(self) -> None:
        report = AuditReport.model_validate({"checks": [{
            "audit_id": "target-1", "rule_id": "R1", "status": "PASS", "reason": "looks okay",
            "total": 2, "checked": 1, "unknown": 1, "evidence": [CITATION],
        }]})
        with self.assertRaisesRegex(ValueError, "PASS"):
            validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})

    def test_bad_document_reference_is_rejected(self) -> None:
        report = AuditReport.model_validate({"checks": [{
            "audit_id": "target-1", "rule_id": "R1", "status": "PASS", "reason": "checked",
            "total": 1, "checked": 1, "unknown": 0,
            "evidence": [{"document_id": "another-version", "locator": CITATION["locator"]}],
        }]})
        with self.assertRaisesRegex(ValueError, "证据来源"):
            validate_report(report, {"target-1": plan()}, {"target-1": DOCUMENT})

    def test_artifact_without_completion_receipt_is_rejected(self) -> None:
        runner = GrokRunner({
            "base_url": "https://example.invalid", "token": "x" * 32, "model": "test",
            "max_turns": 2, "wall_seconds": 10,
        })
        runner._run = lambda *_args, **_kwargs: {"structured_output": {"completed": False, "artifact": "execution.json"}}  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "completion receipt"):
                runner.run_artifact(Path(directory), "audit", AuditReport, 9999999999.0, reasoning_effort="low")


if __name__ == "__main__":
    unittest.main()
