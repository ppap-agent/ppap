"""Small deterministic guards for the migrated semantic core."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

from agent.execution.guest_model import ModelProtocolError, PiRunner, sandbox_profile
from audit.checker_tools import CheckRequest, check_fields
from audit.document_tools import snapshot


def field_check_request() -> dict:
    return {
        "binding": {
            "id": "data",
            "document_id": "target-1",
            "source_sha256": "b" * 64,
            "table": "Sheet1",
            "header_rows": [1],
            "first_row": 2,
            "last_row": 4,
            "columns": {"frequency": "A", "measurement": "B"},
            "mapping_basis": "observed headers",
            "boundary_basis": "observed body rows",
        },
        "checks": [
            {"id": "presence", "kind": "field_nonempty", "field": "frequency"},
            {"id": "minimum", "kind": "numeric_at_least", "field": "measurement", "minimum": 2},
        ],
    }


FIELD_DOCUMENT = {
    "source": {"file": "target.xlsx", "sha256": "b" * 64, "format": ".xlsx"},
    "tables": [
        {
            "id": "Sheet1",
            "rows": 4,
            "cols": 2,
            "merges": [],
            "cells": {
                "A1": {"value": "Frequency", "state": "known"},
                "B1": {"value": "Measurement", "state": "known"},
                "A2": {"value": "daily", "state": "known"},
                "B2": {"value": 1, "state": "known"},
                "B3": {"value": "5", "state": "known"},
                "A4": {"value": None, "state": "unknown_formula_cache"},
                "B4": {"value": None, "state": "unknown_formula_cache"},
            },
        }
    ],
    "limitations": [],
}


class SemanticBoundaryTests(unittest.TestCase):
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

    def test_checker_program_files_survive_a_missing_receipt(self) -> None:
        from agent.execution.checker_guest import CheckerDraft

        runner = PiRunner({"base_url": "https://example.invalid/v1", "token": "x" * 32, "model": "test", "wall_seconds": 10})
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)

            def finish_file(work, prompt, deadline, **kwargs):
                del work, deadline, kwargs
                self.assertEqual(prompt, "develop")
                self.assertNotIn("execution.json", prompt)
                self.assertNotIn("available tools", prompt)
                (Path(directory) / "checker.py").write_text("def create_audit(audit_file, rule, group_context=None):\n    return None\n", encoding="utf-8")
                (Path(directory) / "resources.py").write_text("RESOURCE = None\n", encoding="utf-8")
                raise ModelProtocolError("Model stage failed")

            with patch.object(runner, "_run", side_effect=finish_file):
                draft = runner.run(work, "develop", CheckerDraft, 9999999999.0, reasoning_effort="medium")
        self.assertEqual(draft.entrypoint, "checker:create_audit")
        self.assertIn("create_audit", draft.files["checker.py"])
        self.assertEqual(draft.files["resources.py"], "RESOURCE = None\n")
        self.assertEqual(draft.clauses, [])

    def test_checker_receipt_can_name_a_partial_trial(self) -> None:
        from agent.execution.checker_guest import CheckerDraft

        runner = PiRunner({"base_url": "https://example.invalid/v1", "token": "x" * 32, "model": "test", "wall_seconds": 10})
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)

            def finish_file(*_args, **_kwargs):
                (work / "checker.py").write_text("def create_audit(audit_file, rule, group_context=None):\n    return None\n", encoding="utf-8")
                return {"result": '{"completed":true,"artifact":"checker.py","clauses":[" R4 "]}'}

            with patch.object(runner, "_run", side_effect=finish_file):
                draft = runner.run(work, "develop", CheckerDraft, 9999999999.0, reasoning_effort="medium")
        self.assertEqual(draft.clauses, ["R4"])

    def test_checker_receipt_rejects_a_malformed_clause_list(self) -> None:
        from agent.execution.checker_guest import CheckerDraft

        runner = PiRunner({"base_url": "https://example.invalid/v1", "token": "x" * 32, "model": "test", "wall_seconds": 10})
        for receipt in (
            '{"completed":true,"artifact":"checker.py","clauses":[]}',
            '{"completed":true,"artifact":"checker.py","clauses":[1]}',
            '{"completed":true,"artifact":"checker.py","clauses":["R4"],"extra":true}',
        ):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)

                def finish_file(*_args, **_kwargs):
                    (work / "checker.py").write_text("def create_audit(audit_file, rule, group_context=None):\n    return None\n", encoding="utf-8")
                    return {"result": receipt}

                with self.subTest(receipt=receipt), patch.object(runner, "_run", side_effect=finish_file):
                    with self.assertRaisesRegex(ModelProtocolError, "trial clauses|completion receipt"):
                        runner.run(work, "develop", CheckerDraft, 9999999999.0, reasoning_effort="medium")

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
        source = json.loads(json.dumps(FIELD_DOCUMENT))
        source["tables"][0]["cells"]["A4"] = {"value": "confirmed footer", "state": "known"}
        request = field_check_request()
        request["checks"] = request["checks"][:1]
        request["scope"] = {
            "binding_id": "data",
            "excluded_rows": [
                {"row": row, "reason_type": "non_record", "reason": "independently reviewed non-record", "evidence_locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": f"A{row}"}}
                for row in (2, 3, 4)
            ],
        }
        result = check_fields(source, CheckRequest.model_validate(request))
        self.assertEqual((result.checks[0].local_condition_status, result.checks[0].total), ("UNABLE", 3))

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
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            source, request, output = (location / name for name in ("snapshot.json", "request.json", "result.json"))
            source.write_text(json.dumps(FIELD_DOCUMENT))
            request.write_text(json.dumps(field_check_request()))
            completed = subprocess.run(
                [sys.executable, "-m", "audit.checker_tools", str(source), "--request", str(request), "--out", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["checks"][0]["violations"][0]["citation"]["locator"]["cell"], "A3")

    def test_loopback_http_gateway_requires_explicit_low_privilege_container_mode(self) -> None:
        grant = {"base_url": "http://127.0.0.1:12345/v1", "token": "x" * 32, "model": "test", "wall_seconds": 10}
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("agent.execution.guest_model.os.getuid", return_value=10001):
                PiRunner(grant)
                PiRunner({**grant, "base_url": "http://localhost:12345/v1"})
                with self.assertRaises(ValueError):
                    PiRunner({**grant, "base_url": "http://example.test:12345/v1"})
            with patch("agent.execution.guest_model.os.getuid", return_value=0):
                with self.assertRaises(ValueError):
                    PiRunner(grant)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                PiRunner(grant)
            PiRunner({**grant, "base_url": "https://example.test/v1"})

    def test_container_runner_does_not_start_or_kill_its_own_process_group(self) -> None:
        grant = {"base_url": "http://127.0.0.1:12345/v1", "token": "x" * 32, "model": "test", "wall_seconds": 10}
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "container-only", "PPAP_GUEST_SANDBOX": "off"}, clear=True):
            with patch("agent.execution.guest_model.os.getuid", return_value=10001):
                runner = PiRunner(grant)
                with tempfile.TemporaryDirectory() as directory:
                    with patch("agent.execution.guest_model.subprocess.Popen") as popen:
                        popen.return_value.poll.return_value = 0
                        popen.return_value.returncode = 0
                        with patch("agent.execution.guest_model.os.killpg") as killpg:
                            with self.assertRaises(ModelProtocolError):
                                runner._run(Path(directory), "test", 9999999999.0, reasoning_effort="low")
                            self.assertFalse(popen.call_args.kwargs["start_new_session"])
                            killpg.assert_not_called()

    def test_container_only_requires_explicit_profile_and_guest_uid(self) -> None:
        with patch("agent.execution.guest_model.os.getuid", return_value=10001):
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
            with patch("agent.execution.guest_model.os.getuid", return_value=0):
                with self.assertRaises(ModelProtocolError):
                    sandbox_profile()

    def test_gvisor_still_requires_kernel_marker_and_guest_uid(self) -> None:
        with patch.dict(os.environ, {"PPAP_GUEST_ISOLATION": "gvisor"}, clear=True):
            with patch("agent.execution.guest_model.os.getuid", return_value=10001):
                with patch("agent.execution.guest_model.Path.is_file", return_value=True):
                    self.assertEqual(sandbox_profile(), "off")
                with patch("agent.execution.guest_model.Path.is_file", return_value=False):
                    with self.assertRaises(ModelProtocolError):
                        sandbox_profile()

    def test_published_source_cannot_omit_effective_clause(self) -> None:
        clause = {"id": "R1", "rule_to_check": "First"}
        omitted = {"id": "R2", "rule_to_check": "Second"}
        payload = {"kind": "published_rule", "rule_version_id": "v1", "rule_body": {"rules": [clause, omitted]}, "clauses": [clause]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rule.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "clauses differ"):
                snapshot(path)


if __name__ == "__main__":
    unittest.main()
