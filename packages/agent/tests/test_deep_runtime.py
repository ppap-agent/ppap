"""Deep product adapter contracts. These tests do not measure model judgment."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from agent.auditor import SemanticRuntime, product_response, semantic_inputs
from agent.client import DEEP_REQUEST_TIMEOUT, DeepClient, DeepError
from agent.execution.budget import DEEP_TOTAL_SECONDS
from agent.execution.gateway import Grants, local_gateway
from agent.execution.harness import runtime_from_env
from agent.protocol import ExecuteRequest, FrozenFile
from agent.run import ExecutionContext
from agent.source import authorize_execution
from audit.audits import DEEP_PROTOCOL_REVISION
from audit.models import RulePlan
from audit.protocol import CompileBundle, CompileSpec, ResultBundle
from audit.storage_errors import ObjectMissing, ObjectTooLarge
from audit.trace import AuditTrace, derive_report, evaluate_audit

ENGINE = "a" * 64
ROUND = "00000000-0000-4000-8000-000000000099"
TARGET = "00000000-0000-4000-8000-000000000001"
REFERENCE = "00000000-0000-4000-8000-000000000002"
RULE = "00000000-0000-4000-8000-000000000003"


def _clauses() -> list[dict]:
    return [
        {"id": "R1", "category": "控制计划", "priority": "P1", "rule_to_check": "每项频次明确"},
        {"id": "R2", "category": "控制计划", "priority": "P0", "rule_to_check": "频次与流程一致"},
        {"id": "R3", "category": "控制计划", "priority": "P1", "rule_to_check": "视觉标记可确认"},
        {"id": "R4", "category": "控制计划", "priority": "P2", "rule_to_check": "附加要求适用"},
    ]


def _spec(*, title: str = "客户规则", tenant: str = "tenant-a", target_bytes: bytes = b"target", clauses: list[dict] | None = None) -> ExecuteRequest:
    target = FrozenFile(
        file_version_id=TARGET,
        file_id="file-target",
        role="target",
        filename="plan.xlsx",
        sha256=hashlib.sha256(target_bytes).hexdigest(),
        rule_version_id=RULE,
        rule_title=title,
        rule_body={"kind": "engine", "rules": clauses if clauses is not None else _clauses()},
        rule_source_file_version_id="original-rule-version",
    )
    reference = FrozenFile(
        file_version_id=REFERENCE,
        file_id="file-reference",
        role="reference",
        filename="flow.pdf",
        sha256=hashlib.sha256(b"reference").hexdigest(),
    )
    return ExecuteRequest(
        protocol_revision=DEEP_PROTOCOL_REVISION,
        round_id=ROUND,
        tenant_id=tenant,
        job_id="job-one",
        executor="ppap-deep",
        executor_revision=ENGINE,
        files=[target, reference],
    )


def _bundle(spec: ExecuteRequest, submission) -> ResultBundle:
    rule = submission.rules[0]
    target, reference = spec.files
    cells = {
        "A1": {"value": "observed-1", "state": "known"},
        "A2": {"value": "流程关系说明", "state": "known"},
        "A3": {"value": "频次", "state": "known"},
    }
    documents = {
        TARGET: {
            "source": {"file": "document-0.xlsx", "sha256": target.sha256, "format": ".xlsx"},
            "tables": [{"id": "Sheet1", "rows": 3, "cols": 1, "cells": cells, "merges": []}],
            "limitations": [],
        },
        REFERENCE: {"source": {"file": "document-1.pdf", "sha256": reference.sha256, "format": ".pdf"}, "tables": [], "pages": [{"page": 1, "blocks": [{"text": "flow relation"}]}], "limitations": []},
    }
    source = {
        "source": {"file": "rule-0.json", "sha256": rule.sha256, "format": ".json"},
        "kind": "published_rule",
        "rule_version_id": RULE,
        "rule_body": target.rule_body,
        "clauses": target.rule_body["rules"],
        "origin": {"file_version_id": "original-rule-version"},
        "tables": [],
        "limitations": [],
    }
    plan = {
        "source_sha256": rule.sha256,
        "plan_revision": rule.plan_revision,
        "ontology": {"entities": [{"id": "records", "grain": "one frozen frequency record", "attributes": {"frequency": "string"}, "identity": ["frequency"]}]},
        "rules": [
            {
                "id": clause["id"],
                "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                "program": (
                    {
                        "kind": "records",
                        "source": "records",
                        "quantifier": "forall",
                        "require": {"kind": "judge", "question": clause["rule_to_check"]},
                        **({"when": {"kind": "judge", "question": "Does the optional clause apply?"}} if clause["id"] == "R4" else {}),
                    }
                    if clause["id"] in {"R1", "R4"}
                    else {"kind": "document", "require": {"kind": "judge", "question": clause["rule_to_check"]}}
                ),
                "empty_scope_status": "NA" if clause["id"] == "R4" else "UNABLE",
                "empty_scope_basis": "附加要求不适用" if clause["id"] == "R4" else "规则未规定空范围通过",
            }
            for clause in target.rule_body["rules"]
        ],
    }

    def cell(address):
        return {"document_id": TARGET, "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": address}}

    pdf = {"document_id": REFERENCE, "locator": {"kind": "pdf_block", "page": 1, "block": 1}}
    examples = [
        ("PASS", [cell("A1")], None, 1, 0),
        ("NG", [cell("A2"), pdf], None, 1, 0),
        ("UNABLE", [], "unresolved_relation", 0, 1),
        ("NA", [cell("A3")], None, 1, 0),
    ]
    bindings = [
        {
            "id": "frequency",
            "document_id": TARGET,
            "source_sha256": target.sha256,
            "table": "Sheet1",
            "header_rows": [3],
            "first_row": 1,
            "last_row": 1,
            "columns": {"frequency": "A"},
            "mapping_basis": "A3 names the frequency field in column A.",
            "boundary_basis": "A1 is the sole frequency record; A2 is relation metadata and A3 is the header.",
        }
    ]
    uses = [
        {"audit_id": TARGET, "rule_id": clause["id"], "inputs": {"records": {"kind": "bound", "scopes": [{"binding_id": "frequency"}]}} if clause["id"] in {"R1", "R4"} else {}}
        for clause in target.rule_body["rules"]
    ]
    trace = AuditTrace.model_validate({"bindings": bindings, "uses": uses})
    compiled = RulePlan.model_validate(plan)
    initial = evaluate_audit(trace, documents, {TARGET: compiled})
    outcomes = {clause["id"]: example for clause, example in zip(target.rule_body["rules"], examples, strict=True)}
    answers = []
    for need in initial.needs:
        status, evidence, diagnostic, _, _ = outcomes[need.rule_id]
        value = "false" if status in {"NG", "NA"} else "unknown" if status == "UNABLE" else "true"
        answers.append({"need_id": need.id, "value": value, "reason": "record reviewed", "evidence": evidence, "diagnostic": diagnostic})
    trace = AuditTrace.model_validate({**trace.model_dump(mode="json"), "answers": answers})
    report = derive_report(trace, documents, {TARGET: compiled})
    return ResultBundle(
        execution_id=submission.execution_id,
        semantic_engine_revision=ENGINE,
        documents=documents,
        rule_sources={RULE: source},
        plans={RULE: compiled},
        report=report,
        trace=trace,
        input_tokens=120,
        output_tokens=30,
    )


class SemanticProductContractTests(unittest.TestCase):
    def test_controller_authorization_initializes_without_external_io(self) -> None:
        calls = []
        authorized = authorize_execution(object(), connect=lambda *_a, **_k: calls.append("connect"), open_store=lambda _settings: calls.append("store") or object())
        self.assertTrue(callable(authorized))
        self.assertEqual(calls, ["store"])

    def test_deep_execution_and_transport_time_limits_match(self) -> None:
        self.assertEqual(DEEP_TOTAL_SECONDS, 40 * 60)
        self.assertEqual(SemanticRuntime(None, None).timeout, DEEP_TOTAL_SECONDS)
        self.assertEqual(Grants("test-model").ttl, DEEP_TOTAL_SECONDS)
        self.assertEqual(DeepClient("unused").timeout, DEEP_TOTAL_SECONDS + 30)
        self.assertEqual(DEEP_REQUEST_TIMEOUT, DEEP_TOTAL_SECONDS + 30)
        with tempfile.TemporaryDirectory() as temporary:
            with local_gateway(Path(temporary) / "gateway.sock", upstream="https://provider.example/v1", model="test-model", provider_key="test-provider-key") as gateway:
                self.assertEqual(gateway.http.grants.ttl, DEEP_TOTAL_SECONDS)

    def test_runtime_requires_explicit_supported_isolation_before_startup(self) -> None:
        for runtime, profile, message in (
            ("", "off", "container-only"),
            ("process", "off", "container-only"),
            ("grok", "off", "container-only"),
            ("auto", "off", "container-only"),
            ("container-only", "", "off or ppap"),
            ("container-only", "gvisor", "off or ppap"),
        ):
            with self.subTest(runtime=runtime, profile=profile):
                with patch.dict("os.environ", {"PPAP_DEEP_RUNTIME": runtime, "PPAP_DEEP_SANDBOX_PROFILE": profile}):
                    with self.assertRaisesRegex(ValueError, message):
                        with runtime_from_env(None, None):
                            self.fail("Unsupported runtime must not start")

    def test_effective_published_rule_source_and_title_independence(self) -> None:
        first, packets = semantic_inputs(_spec(), ENGINE)
        renamed, renamed_packets = semantic_inputs(_spec(title="完全改名的显示标题"), ENGINE)
        self.assertEqual(first.execution_id, renamed.execution_id)
        self.assertEqual(packets, renamed_packets)
        rule = first.rules[0]
        self.assertEqual(rule.rule_version_id, RULE)
        self.assertEqual(rule.clauses, _clauses())
        self.assertEqual(hashlib.sha256(packets[RULE]).hexdigest(), rule.sha256)
        source = json.loads(packets[RULE])
        self.assertEqual(source["rule_body"]["rules"], source["clauses"])
        self.assertEqual(source["origin"], {"file_version_id": "original-rule-version"})
        changed = _clauses()
        changed[0] = {**changed[0], "rule_to_check": "每项频次至少每日一次"}
        new_rule, new_packets = semantic_inputs(_spec(clauses=changed), ENGINE)
        self.assertNotEqual(new_packets[RULE], packets[RULE])
        self.assertNotEqual(new_rule.rules[0].plan_revision, rule.plan_revision)

    def test_all_clauses_and_cross_file_typed_evidence_reach_product(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        response = product_response(spec, _bundle(spec, submission), submission)
        self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
        self.assertEqual([row["rule_item_id"] for row in response.coverage.rule_items], ["R1", "R2", "R3", "R4"])
        self.assertEqual(
            response.coverage.verification.model_dump(),
            {"method": "host_source_trace_v1", "round_id": ROUND, "job_id": "job-one", "attempt": 1, "executor_revision": ENGINE, "semantic_engine_revision": ENGINE},
        )
        self.assertEqual([row["proof_kind"] for row in response.coverage.rule_items], ["semantic"] * 4)
        self.assertEqual(response.usage, {"input_tokens": 120, "output_tokens": 30, "tool_calls": 0, "completed_tool_calls": 0, "compactions": 0})
        semantic = response.findings[1].locator["semantic"]
        self.assertEqual(semantic["rule_text"], "频次与流程一致")
        self.assertEqual(semantic["rule_source_file_version_id"], "original-rule-version")
        self.assertEqual(semantic["scope"], {"total": 1, "checked": 1, "unknown": 0})
        self.assertEqual(semantic["sources"][0]["file_version_id"], TARGET)
        self.assertEqual(
            semantic["sources"][1],
            {
                "file_version_id": REFERENCE,
                "filename": "flow.pdf",
                "role": "reference",
                "locator": {"kind": "pdf_block", "page": 1, "block": 1},
                "excerpt": "flow relation",
            },
        )
        self.assertTrue(response.coverage.locator_gap)  # Explicit UNABLE has no source.

    def test_frozen_context_keeps_each_target_rule_and_explicit_reference(self) -> None:
        spec = _spec()
        second_id = "00000000-0000-4000-8000-000000000004"
        second_rule = "00000000-0000-4000-8000-000000000005"
        second = spec.files[0].model_copy(deep=True, update={"file_version_id": second_id, "file_id": "file-second", "filename": "pfmea.xlsx", "rule_version_id": second_rule})
        second.rule_body["rules"] = [{"id": "F1", "rule_to_check": "过程与控制计划一致"}]
        spec.files.append(second)
        submission, packets = semantic_inputs(spec, ENGINE)

        self.assertEqual(submission.targets, {TARGET: RULE, second_id: second_rule})
        self.assertEqual([(item.audit_id, item.role) for item in submission.documents], [(TARGET, "target"), (REFERENCE, "context"), (second_id, "target")])
        self.assertEqual({item.rule_id: item.clauses for item in submission.rules}, {RULE: _clauses(), second_rule: [{"id": "F1", "rule_to_check": "过程与控制计划一致"}]})
        self.assertEqual(set(packets), {RULE, second_rule})
        original = submission.model_dump_json()
        spec.files[0].rule_body["rules"][0]["rule_to_check"] = "已改变的要求"
        spec.files[1].sha256 = "c" * 64
        second.rule_body["rules"][0]["id"] = "changed"
        self.assertEqual(submission.model_dump_json(), original)
        self.assertEqual(json.loads(packets[RULE])["clauses"], _clauses())
        self.assertEqual(json.loads(packets[second_rule])["clauses"], [{"id": "F1", "rule_to_check": "过程与控制计划一致"}])

    def test_shared_rule_version_requires_identical_effective_source(self) -> None:
        spec = _spec()
        second_id = "00000000-0000-4000-8000-000000000004"
        second = spec.files[0].model_copy(deep=True, update={"file_version_id": second_id, "file_id": "file-second", "rule_title": "同一版本的另一显示标题"})
        spec.files.append(second)
        submission, packets = semantic_inputs(spec, ENGINE)
        self.assertEqual(submission.targets, {TARGET: RULE, second_id: RULE})
        self.assertEqual(len(submission.rules), 1)
        self.assertEqual(set(packets), {RULE})

        for changed_field in ("rule_body", "rule_source_file_version_id"):
            with self.subTest(changed_field=changed_field):
                changed = spec.model_copy(deep=True)
                if changed_field == "rule_body":
                    changed.files[-1].rule_body["rules"][0]["rule_to_check"] = "另一份要求"
                else:
                    changed.files[-1].rule_source_file_version_id = "another-rule-source-version"
                with self.assertRaises(DeepError) as error:
                    semantic_inputs(changed, ENGINE)
                self.assertEqual(error.exception.code, "deep_integrity_mismatch")

    def test_reference_requires_a_valid_frozen_source_digest(self) -> None:
        for digest in (None, "", "not-a-sha256"):
            with self.subTest(digest=digest):
                payload = _spec().model_dump(mode="json")
                if digest is None:
                    del payload["files"][1]["sha256"]
                else:
                    payload["files"][1]["sha256"] = digest
                with self.assertRaises(ValueError):
                    semantic_inputs(ExecuteRequest.model_validate(payload), ENGINE)

    def test_missing_clause_and_wrong_evidence_version_are_rejected(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        missing = _bundle(spec, submission)
        missing.report.checks.pop()
        with self.assertRaisesRegex(ValueError, "缺项"):
            product_response(spec, missing, submission)
        bad_reference = _bundle(spec, submission)
        bad_reference.report.checks[1].evidence[1].document_id = "another-version"
        with self.assertRaisesRegex(ValueError, "证据来源"):
            product_response(spec, bad_reference, submission)

    def test_business_results_keep_their_status_when_optional_citations_are_missing(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        payload = _bundle(spec, submission).model_dump(mode="json")
        for check in payload["report"]["checks"]:
            check["evidence"] = []
        for record in payload["trace"]["answers"]:
            record["evidence"] = []
        bundle = ResultBundle.model_validate(payload)
        original = bundle.model_dump_json()

        response = product_response(spec, bundle, submission)

        self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
        self.assertEqual([row.suggestions for row in response.findings], ["完整声明范围内的适用记录满足规则要求", "record reviewed", "record reviewed", "附加要求不适用"])
        self.assertTrue(response.coverage.locator_gap)
        self.assertIsNone(response.coverage.verification)
        for row in response.findings:
            self.assertEqual(row.locator["semantic"]["sources"], [])
        self.assertEqual(bundle.model_dump_json(), original)

    def test_unavailable_optional_locations_are_not_promoted_to_source_evidence(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        for defect in ("outside", "format", "unknown", "oversized", "budget"):
            with self.subTest(defect=defect):
                payload = _bundle(spec, submission).model_dump(mode="json")
                if defect == "outside":
                    locator = {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A999"}
                elif defect == "format":
                    locator = {"kind": "pdf_block", "page": 1, "block": 1}
                elif defect == "oversized":
                    locator = {"kind": "xlsx_range", "sheet": "Sheet1", "start": "A1", "end": "XFD9999999"}
                elif defect == "budget":
                    locator = {"kind": "xlsx_range", "sheet": "Sheet1", "start": "A1", "end": "A250"}
                    table = payload["documents"][TARGET]["tables"][0]
                    table.update(rows=8192, cols=4000, merges=[[1, column, 8192, column] for column in range(1, 4001)])
                else:
                    locator = {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}
                    payload["documents"][TARGET]["tables"][0]["cells"]["A1"].update(value=None, state="unknown_formula_cache")
                for check in payload["report"]["checks"][:2]:
                    check["evidence"][0]["locator"] = locator
                for record in payload["trace"]["answers"][:2]:
                    record["evidence"][0]["locator"] = locator
                bundle = ResultBundle.model_validate(payload)
                original = bundle.model_dump_json()

                response = product_response(spec, bundle, submission)

                self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
                self.assertEqual(response.findings[0].locator["semantic"]["sources"], [])
                known_sources = response.findings[1].locator["semantic"]["sources"]
                self.assertEqual([source["excerpt"] for source in known_sources], [] if defect == "budget" else ["flow relation"])
                self.assertTrue(response.coverage.locator_gap)
                self.assertIsNone(response.coverage.verification)
                self.assertTrue(response.findings[0].locator["semantic"]["evidence_incomplete"])
                self.assertEqual(bundle.model_dump_json(), original)

    def test_omitted_optional_evidence_fields_and_coordinates_keep_business_results(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        for omitted in ("check_evidence", "record_evidence", "locator"):
            with self.subTest(omitted=omitted):
                payload = _bundle(spec, submission).model_dump(mode="json")
                if omitted == "check_evidence":
                    for check in payload["report"]["checks"]:
                        del check["evidence"]
                for record in payload["trace"]["answers"]:
                    if omitted == "record_evidence":
                        del record["evidence"]
                    elif omitted == "locator":
                        for citation in record["evidence"]:
                            del citation["locator"]
                if omitted == "locator":
                    for check in payload["report"]["checks"]:
                        for citation in check["evidence"]:
                            del citation["locator"]
                bundle = ResultBundle.model_validate(payload)
                response = product_response(spec, bundle, submission)
                self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
                self.assertEqual([row.suggestions for row in response.findings], ["完整声明范围内的适用记录满足规则要求", "record reviewed", "record reviewed", "附加要求不适用"])
                self.assertTrue(response.coverage.locator_gap)
                self.assertIsNone(response.coverage.verification)

    def test_replayed_query_ng_keeps_its_result_without_a_public_counterexample_location(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        payload = _bundle(spec, submission).model_dump(mode="json")
        payload["plans"][RULE]["rules"][0]["program"] = {"kind": "records", "source": "records", "require": {"kind": "value_test", "field": "frequency", "op": "not_empty"}, "quantifier": "forall"}
        payload["documents"][TARGET]["tables"][0]["cells"]["A1"]["value"] = None
        payload["report"]["checks"][0].update(status="NG", reason="Frequency is empty", evidence=[])
        payload["trace"]["answers"].pop(0)
        bundle = ResultBundle.model_validate(payload)

        response = product_response(spec, bundle, submission)

        self.assertEqual(response.findings[0].status, "NG")
        self.assertEqual(response.findings[0].suggestions, "Frequency is empty")
        self.assertEqual(response.findings[0].locator["semantic"]["sources"], [])
        self.assertTrue(response.coverage.locator_gap)
        self.assertIsNone(response.coverage.verification)
        bundle.report.checks[0].status = "PASS"
        with self.assertRaisesRegex(ValueError, "differs from deterministic replay"):
            product_response(spec, bundle, submission)

    def test_query_product_keeps_frozen_rule_text_and_derived_conclusion(self) -> None:
        from audit.models import RecordsProgram
        from audit.trace import derive_report

        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        for value, status, conclusion in (("每班", "PASS", "完整声明范围内"), (None, "NG", "字段 frequency")):
            with self.subTest(status=status):
                bundle = _bundle(spec, submission)
                bundle.plans[RULE].rules[0].program = RecordsProgram(kind="records", source="records", require={"kind": "value_test", "field": "frequency", "op": "not_empty"}, quantifier="forall")
                bundle.documents[TARGET]["tables"][0]["cells"]["A1"]["value"] = value
                bundle.trace.answers.pop(0)
                bundle.report = derive_report(bundle.trace, bundle.documents, {TARGET: bundle.plans[RULE]})

                response = product_response(spec, bundle, submission)

                finding = response.findings[0]
                original = spec.files[0].rule_body["rules"][0]
                self.assertEqual((finding.status, finding.item, finding.priority), (status, original["rule_to_check"], original["priority"]))
                self.assertEqual(finding.locator["semantic"]["rule_text"], original["rule_to_check"])
                self.assertEqual(response.coverage.rule_items[0]["rule_to_check"], original["rule_to_check"])
                self.assertTrue(finding.suggestions.startswith(conclusion))
                self.assertEqual(finding.suggestions, bundle.report.checks[0].reason)

    def test_optional_evidence_does_not_relax_actual_execution_or_identity_validation(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        for defect in ("missing", "duplicate", "unknown", "empty", "quote", "scope", "wrong_document", "wrong_snapshot"):
            with self.subTest(defect=defect):
                payload = _bundle(spec, submission).model_dump(mode="json")
                check = payload["report"]["checks"][0]
                if defect == "missing":
                    payload["report"]["checks"].pop()
                elif defect == "duplicate":
                    payload["report"]["checks"].append(dict(check))
                elif defect == "unknown":
                    check.update(checked=0, unknown=1)
                elif defect == "empty":
                    check.update(total=0, checked=0)
                elif defect == "scope":
                    check["checked"] = 0
                elif defect == "quote":
                    check["evidence"][0]["quote"] = "invented text"
                elif defect == "wrong_document":
                    payload["trace"]["answers"][0]["evidence"][0]["document_id"] = "outside-submission"
                else:
                    payload["documents"][TARGET]["source"]["sha256"] = "f" * 64
                with self.assertRaises(ValueError):
                    product_response(spec, ResultBundle.model_validate(payload), submission)

    def test_a_valid_bundle_for_another_effective_rule_cannot_gain_a_host_validation_record(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        changed = spec.model_copy(deep=True)
        changed.files[0].rule_body["rules"][0]["rule_to_check"] = "已改变的有效客户要求"
        with self.assertRaisesRegex(ValueError, "frozen request"):
            product_response(changed, _bundle(spec, submission), submission)

    def test_an_internally_valid_bundle_cannot_change_the_frozen_execution_policy(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, "b" * 64)
        bundle = _bundle(spec, submission).model_copy(update={"semantic_engine_revision": "b" * 64})
        with self.assertRaisesRegex(ValueError, "frozen execution"):
            product_response(spec, bundle, submission)

    def test_renamed_rule_flows_through_semantic_adapter_without_fixed_checker(self) -> None:
        import fitz
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        for value in ("observed-1", "流程关系说明", "频次"):
            sheet.append([value])
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "flow relation")
            reference = pdf.tobytes()
        blobs = {TARGET: output.getvalue(), REFERENCE: reference}
        spec = _spec(title="标题不同但要求未变", target_bytes=blobs[TARGET])
        spec.files[1].sha256 = hashlib.sha256(reference).hexdigest()
        objects = _MemoryObjects()
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "audit.engine" or name.startswith("audit.engine."):
                raise AssertionError("fixed checker imported")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            response = SemanticRuntime(_BundleRuntime(spec), objects).execute(spec, blobs, context=ExecutionContext())
        self.assertEqual(response.note, "deep_semantic_audit")
        self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
        self.assertTrue(any(key.endswith("/bundle.json") for key in objects.writes))


class _StopAfterCache(Exception):
    pass


class _CacheObjects:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        self.reads.append(key)
        raise ObjectMissing()

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        raise AssertionError("No artifact should be written before validation")


class _MemoryObjects(_CacheObjects):
    def __init__(self) -> None:
        super().__init__()
        self.writes: dict[str, bytes] = {}

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        self.writes[key] = body


class _BundleRuntime:
    def __init__(self, spec: ExecuteRequest) -> None:
        self.spec = spec

    def engine_revision(self) -> str:
        return ENGINE

    def run(self, submission, inputs: Path, work: Path, _deadline: float, _cancelled, *, on_progress, prior_usage=None) -> None:
        result = work / "result"
        result.mkdir()
        bundle = _bundle(self.spec, submission)
        if isinstance(submission, CompileSpec):
            compiled = CompileBundle(
                execution_id=submission.execution_id,
                semantic_engine_revision=submission.semantic_engine_revision,
                rule_sources=bundle.rule_sources,
                plans=bundle.plans,
            )
            (result / "bundle.json").write_text(compiled.model_dump_json())
        else:
            from audit.document_tools import snapshot

            manifest = json.loads((inputs / "manifest.json").read_text())
            bundle.documents = {audit_id: snapshot(inputs / name) for audit_id, name in manifest["documents"].items()}
            bundle.report = derive_report(bundle.trace, bundle.documents, {TARGET: bundle.plans[RULE]})
            (result / "bundle.json").write_text(bundle.model_dump_json())


class _CacheRuntime:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def engine_revision(self) -> str:
        return self.revision

    def run(self, operation, inputs, work, *_args, **_kwargs) -> None:
        if isinstance(operation, CompileSpec):
            rule = operation.rules[0]
            result = work / "result"
            result.mkdir()
            source = json.loads((inputs / "rule.json").read_bytes())
            source["source"] = {"sha256": rule.sha256, "format": rule.format}
            compiled = CompileBundle(
                execution_id=operation.execution_id,
                semantic_engine_revision=operation.semantic_engine_revision,
                rule_sources={rule.rule_id: source},
                plans={},
                failed_rules={
                    rule.rule_id: {
                        "rule_id": rule.rule_id,
                        "source_sha256": rule.sha256,
                        "plan_revision": rule.plan_revision,
                        "engine_revision": operation.semantic_engine_revision,
                        "stage": "compile_repair_validation",
                        "reason": "plan_schema_invalid",
                    }
                },
            )
            (result / "bundle.json").write_text(compiled.model_dump_json())
            return
        raise _StopAfterCache()


class SemanticCacheContractTests(unittest.TestCase):
    def test_frozen_policy_change_is_rejected_before_cache_or_model_work(self) -> None:
        objects = _CacheObjects()
        runtime = SemanticRuntime(_CacheRuntime("b" * 64), objects)
        spec = _spec().model_copy(update={"executor_revision": ENGINE})

        with self.assertRaisesRegex(DeepError, "executor_revision_mismatch"):
            runtime.execute(spec, {TARGET: b"target", REFERENCE: b"reference"}, context=ExecutionContext())

        self.assertEqual(objects.reads, [])

    def _keys(self, spec: ExecuteRequest, *, revision: str = ENGINE, target_bytes: bytes = b"target") -> tuple[str, str]:
        spec = spec.model_copy(update={"executor_revision": revision})
        objects = _CacheObjects()
        runtime = SemanticRuntime(_CacheRuntime(revision), objects)
        with self.assertRaises(_StopAfterCache):
            runtime.execute(spec, {TARGET: target_bytes, REFERENCE: b"reference"}, context=ExecutionContext())
        snapshot = next(key for key in objects.reads if "/snapshots/" in key)
        plan = next(key for key in objects.reads if "/plans/" in key)
        return snapshot, plan

    def test_cache_is_scoped_to_tenant_rule_document_and_engine_versions(self) -> None:
        snapshot, plan = self._keys(_spec())
        self.assertEqual((snapshot, plan), self._keys(_spec(title="标题改名")))
        other_tenant = self._keys(_spec(tenant="tenant-b"))
        self.assertNotEqual((snapshot, plan), other_tenant)
        other_document = self._keys(_spec(target_bytes=b"changed"), target_bytes=b"changed")
        self.assertNotEqual(snapshot, other_document[0])
        self.assertEqual(plan, other_document[1])
        changed = _clauses()
        changed[0] = {**changed[0], "rule_to_check": "每项频次至少每日一次"}
        self.assertNotEqual(plan, self._keys(_spec(clauses=changed))[1])
        self.assertNotEqual(plan, self._keys(_spec(), revision="b" * 64)[1])

    def test_plan_cache_changes_with_explicit_appendix_content_scope_and_rule_identity(self) -> None:
        initial = _spec()
        original_snapshot, original_plan = self._keys(initial)
        confirmed = initial.model_copy(deep=True)
        confirmed.files[0].rule_body["appendix"] = [
            {"id": "confirmation", "question_id": "frequency-meaning", "kind": "interpretation", "content": "频次按每日的次数理解", "scope": {"document_ids": [TARGET], "rule_ids": ["R1"]}}
        ]
        snapshot, plan = self._keys(confirmed)
        self.assertEqual(snapshot, original_snapshot)
        self.assertNotEqual(plan, original_plan)
        same = confirmed.model_copy(deep=True)
        same.files[0].rule_title = "仅修改显示标题"
        self.assertEqual(self._keys(same), (snapshot, plan))
        changed_content = confirmed.model_copy(deep=True)
        changed_content.files[0].rule_body["appendix"][0]["content"] = "频次按每批的次数理解"
        changed_scope = confirmed.model_copy(deep=True)
        changed_scope.files[0].rule_body["appendix"][0]["scope"]["document_ids"] = [REFERENCE]
        changed_identity = confirmed.model_copy(deep=True)
        changed_identity.files[0].rule_version_id = "another-rule-version"
        for changed in (changed_content, changed_scope, changed_identity):
            current_snapshot, current_plan = self._keys(changed)
            self.assertEqual(current_snapshot, snapshot)
            self.assertNotEqual(current_plan, plan)

    def test_oversized_cache_is_a_bounded_miss_before_runtime(self) -> None:
        bodies = []

        def response(**_kwargs):
            body = io.BytesIO(b"oversized")
            bodies.append(body)
            return {"Body": body, "ContentLength": 9}

        spec = _spec().model_copy(update={"executor_revision": ENGINE})

        class Objects:
            def get(self, key, *, max_bytes=None):
                body = io.BytesIO(b"oversized")
                bodies.append(body)
                try:
                    raise ObjectTooLarge()
                finally:
                    body.close()

        runtime = SemanticRuntime(_CacheRuntime(ENGINE), Objects())
        with patch("agent.auditor.MAX_BUNDLE_BYTES", 8), patch.object(runtime.runtime, "run", side_effect=_StopAfterCache), self.assertRaises(_StopAfterCache):
            runtime.execute(spec, {TARGET: b"target", REFERENCE: b"reference"}, context=ExecutionContext())
        self.assertTrue(bodies)
        self.assertTrue(all(body.closed for body in bodies))


if __name__ == "__main__":
    unittest.main()


def scoped_rule_variants():
    spec = _spec()
    second = spec.files[0].model_copy(deep=True, update={"file_version_id": "target-two", "file_id": "file-two", "filename": "second.xlsx"})
    second.rule_body["appendix"] = [
        {
            "id": "confirmation-two",
            "kind": "interpretation",
            "content": "Test input: this explanation applies only to the second target.",
            "scope": {"document_ids": [second.file_version_id], "rule_ids": ["R1"], "rule_targets": [{"document_id": second.file_version_id, "rule_id": "R1"}]},
        }
    ]
    spec.files.insert(1, second)
    return spec


def test_same_published_version_has_distinct_stable_scoped_packet_identities():
    single, _ = semantic_inputs(_spec(), ENGINE)
    spec = scoped_rule_variants()
    third = spec.files[0].model_copy(deep=True, update={"file_version_id": "target-three", "filename": "third.xlsx"})
    spec.files.insert(2, third)
    submission, packets = semantic_inputs(spec, ENGINE)
    assert len(submission.rules) == len(packets) == 2
    assert {rule.rule_version_id for rule in submission.rules} == {RULE}
    assert submission.targets[TARGET] == submission.targets[third.file_version_id]
    assert submission.targets[TARGET] != submission.targets["target-two"]
    assert all(rule.rule_id != RULE and len(rule.rule_id) <= 256 and "/" not in rule.rule_id for rule in submission.rules)
    by_id = {rule.rule_id: rule for rule in submission.rules}
    assert by_id[submission.targets[TARGET]].plan_revision == single.rules[0].plan_revision
    assert by_id[submission.targets[TARGET]].plan_revision != by_id[submission.targets["target-two"]].plan_revision
    assert all(json.loads(raw)["rule_version_id"] == RULE for raw in packets.values())
    assert "appendix" not in json.loads(packets[submission.targets[TARGET]])["rule_body"]
    assert json.loads(packets[submission.targets["target-two"]])["rule_body"]["appendix"][0]["scope"]["document_ids"] == ["target-two"]
    reordered, reordered_packets = semantic_inputs(spec.model_copy(update={"files": list(reversed(spec.files))}), ENGINE)
    assert reordered.targets == submission.targets and reordered_packets == packets
    assert {rule.rule_id: rule.plan_revision for rule in reordered.rules} == {rule.rule_id: rule.plan_revision for rule in submission.rules}
    # Removing an unrelated scope variant restores the ordinary public ID,
    # while the unchanged effective packet keeps its reusable plan revision.
    only_confirmed = spec.model_copy(update={"files": [file for file in spec.files if file.role == "reference" or file.file_version_id == "target-two"]})
    confirmed, _ = semantic_inputs(only_confirmed, ENGINE)
    assert confirmed.rules[0].rule_id == RULE
    assert confirmed.rules[0].plan_revision == by_id[submission.targets["target-two"]].plan_revision


@pytest.mark.parametrize("defect", ["body", "metadata", "origin"])
def test_scoped_appendix_variants_do_not_allow_a_published_base_to_change(defect):
    spec = scoped_rule_variants()
    if defect == "body":
        spec.files[1].rule_body["rules"][0]["rule_to_check"] = "A substituted base clause is not an appendix confirmation."
    elif defect == "metadata":
        spec.files[1].rule_body["customers"] = ["different customer"]
    else:
        spec.files[1].rule_source_file_version_id = "different-published-original"
    with pytest.raises(DeepError, match="deep_integrity_mismatch"):
        semantic_inputs(spec, ENGINE)


def test_equal_packets_of_one_published_version_still_share_the_original_rule_id():
    spec = _spec()
    second = spec.files[0].model_copy(deep=True, update={"file_version_id": "target-two", "filename": "second.xlsx"})
    spec.files.insert(1, second)
    submission, packets = semantic_inputs(spec, ENGINE)
    assert len(submission.rules) == 1 and set(packets) == {RULE}
    assert submission.targets == {TARGET: RULE, "target-two": RULE}
