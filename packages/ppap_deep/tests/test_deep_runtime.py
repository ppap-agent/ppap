"""Deep product adapter contracts. These tests do not measure model judgment."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ppap_core.audits import DEEP_PROTOCOL_REVISION
from ppap_core.files import ObjectMissing
from ppap_core.settings import Settings
from ppap_deep.auditor import SemanticRuntime, product_response, semantic_inputs
from ppap_deep.client import DEEP_REQUEST_TIMEOUT, DeepClient, DeepError
from ppap_deep.controller import ExecutionContext
from ppap_deep.isolation.model_gateway import Grants, local_gateway
from ppap_deep.limits import DEEP_TOTAL_SECONDS
from ppap_deep.protocol import ExecuteRequest, FrozenFile
from ppap_deep.runtime import runtime_from_env
from ppap_deep.semantic.protocol import ResultBundle
from ppap_deep.source import authorize_execution

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
        executor_revision=DEEP_PROTOCOL_REVISION,
        files=[target, reference],
    )


def _bundle(spec: ExecuteRequest, submission) -> ResultBundle:
    rule = submission.rules[0]
    target, reference = spec.files
    cells = {f"A{index}": {"value": f"observed-{index}", "state": "known"} for index in range(1, 4)}
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
        "rules": [
            {
                "id": clause["id"],
                "description": clause["rule_to_check"],
                "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                "scope": "本轮资料",
                "predicate": {"kind": "semantic", "question": clause["rule_to_check"]},
                "missing_evidence": "UNABLE",
                "ambiguities": [],
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
    checks, proofs = [], []
    for clause, (status, evidence, diagnostic, checked, unknown) in zip(target.rule_body["rules"], examples, strict=True):
        key = {"audit_id": TARGET, "rule_id": clause["id"]}
        checks.append({**key, "status": status, "reason": "record reviewed", "total": 1, "checked": checked, "unknown": unknown, "evidence": evidence, "diagnostic": diagnostic})
        proofs.append(
            {
                "kind": "semantic",
                **key,
                "scope_basis": "one identified record",
                "records": [
                    {
                        "record_id": clause["id"],
                        "role": "prerequisite" if not evidence else "excluded" if status == "NA" else "applicable",
                        **({"exclusion_basis": "The optional clause is not triggered"} if status == "NA" else {"applicability_basis": "The clause checks this identified item"} if evidence else {}),
                        "status": status,
                        "reason": "record reviewed",
                        "evidence": evidence,
                    }
                ],
            }
        )
    return ResultBundle.model_validate(
        {
            "execution_id": str(submission.execution_id),
            "semantic_engine_revision": ENGINE,
            "documents": documents,
            "rule_sources": {RULE: source},
            "plans": {RULE: plan},
            "report": {"checks": checks},
            "trace": {"bindings": [], "proofs": proofs},
            "input_tokens": 120,
            "output_tokens": 30,
        }
    )


class SemanticProductContractTests(unittest.TestCase):
    def test_controller_authorization_initializes_without_external_io(self) -> None:
        with (
            patch("ppap_deep.source.connect", side_effect=AssertionError("startup must not connect to the database")),
            patch("ppap_core.storage.s3_client", side_effect=AssertionError("startup must not connect to object storage")),
        ):
            self.assertTrue(callable(authorize_execution(Settings())))

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
        settings = Settings(llm_base_url="https://provider.example/v1", llm_model="test-model", llm_api_key="test-provider-key")
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
                        with runtime_from_env(settings):
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
            {"method": "host_source_trace_v1", "round_id": ROUND, "job_id": "job-one", "attempt": 1, "executor_revision": DEEP_PROTOCOL_REVISION, "semantic_engine_revision": ENGINE},
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
        for proof in payload["trace"]["proofs"]:
            for record in proof["records"]:
                record["evidence"] = []
        bundle = ResultBundle.model_validate(payload)
        original = bundle.model_dump_json()

        response = product_response(spec, bundle, submission)

        self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
        self.assertEqual([row.suggestions for row in response.findings], ["record reviewed"] * 4)
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
                    table.update(rows=8192, merges=[[row, 1, row + 1, 1] for row in range(1, 8000, 2)])
                else:
                    locator = {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}
                    payload["documents"][TARGET]["tables"][0]["cells"]["A1"].update(value=None, state="unknown_formula_cache")
                for check in payload["report"]["checks"][:2]:
                    check["evidence"][0]["locator"] = locator
                for proof in payload["trace"]["proofs"][:2]:
                    proof["records"][0]["evidence"][0]["locator"] = locator
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
                for proof in payload["trace"]["proofs"]:
                    for record in proof["records"]:
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
                self.assertEqual([row.suggestions for row in response.findings], ["record reviewed"] * 4)
                self.assertTrue(response.coverage.locator_gap)
                self.assertIsNone(response.coverage.verification)

    def test_replayed_query_ng_keeps_its_result_without_a_public_counterexample_location(self) -> None:
        spec = _spec()
        submission, _ = semantic_inputs(spec, ENGINE)
        payload = _bundle(spec, submission).model_dump(mode="json")
        payload["plans"][RULE]["rules"][0]["predicate"] = {"kind": "query", "predicates": [{"field": "frequency", "op": "not_empty"}], "quantifier": "forall"}
        payload["documents"][TARGET]["tables"][0]["cells"]["A1"]["value"] = None
        payload["report"]["checks"][0].update(status="NG", reason="Frequency is empty", evidence=[])
        payload["trace"]["proofs"][0] = {"kind": "query", "audit_id": TARGET, "rule_id": "R1", "binding_id": "frequency"}
        payload["trace"]["bindings"] = [
            {
                "id": "frequency",
                "document_id": TARGET,
                "source_sha256": spec.files[0].sha256,
                "table": "Sheet1",
                "header_rows": [3],
                "first_row": 1,
                "last_row": 1,
                "columns": {"frequency": "A"},
                "mapping_basis": "Frequency field in column A",
                "boundary_basis": "One body record",
            }
        ]
        bundle = ResultBundle.model_validate(payload)

        response = product_response(spec, bundle, submission)

        self.assertEqual(response.findings[0].status, "NG")
        self.assertEqual(response.findings[0].suggestions, "Frequency is empty")
        self.assertEqual(response.findings[0].locator["semantic"]["sources"], [])
        self.assertTrue(response.coverage.locator_gap)
        self.assertIsNone(response.coverage.verification)
        bundle.report.checks[0].status = "PASS"
        with self.assertRaisesRegex(ValueError, "contradicts replayed"):
            product_response(spec, bundle, submission)

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
                    payload["trace"]["proofs"][0]["records"][0]["evidence"][0]["document_id"] = "outside-submission"
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

    def test_renamed_rule_flows_through_semantic_adapter_without_fixed_checker(self) -> None:
        spec = _spec(title="标题不同但要求未变")
        objects = _MemoryObjects()
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "ppap_excel_audit_engine" or name.startswith("ppap_excel_audit_engine."):
                raise AssertionError("fixed checker imported")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            response = SemanticRuntime(_BundleRuntime(spec), objects).execute(spec, {TARGET: b"target", REFERENCE: b"reference"}, context=ExecutionContext())
        self.assertEqual(response.note, "deep_semantic_audit")
        self.assertEqual([row.status for row in response.findings], ["PASS", "NG", "UNABLE", "NA"])
        self.assertTrue(any(key.endswith("/bundle.json") for key in objects.writes))


class _StopAfterCache(Exception):
    pass


class _CacheObjects:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get(self, key: str) -> bytes:
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

    def run(self, submission, _inputs: Path, work: Path, _deadline: float, _cancelled, *, on_progress) -> None:
        result = work / "result"
        result.mkdir()
        (result / "bundle.json").write_text(_bundle(self.spec, submission).model_dump_json())


class _CacheRuntime:
    def __init__(self, revision: str) -> None:
        self.revision = revision

    def engine_revision(self) -> str:
        return self.revision

    def run(self, *_args, **_kwargs) -> None:
        raise _StopAfterCache()


class SemanticCacheContractTests(unittest.TestCase):
    def _keys(self, spec: ExecuteRequest, *, revision: str = ENGINE, target_bytes: bytes = b"target") -> tuple[str, str]:
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
        self.assertNotEqual(plan, other_document[1])
        changed = _clauses()
        changed[0] = {**changed[0], "rule_to_check": "每项频次至少每日一次"}
        self.assertNotEqual(plan, self._keys(_spec(clauses=changed))[1])
        self.assertNotEqual(plan, self._keys(_spec(), revision="b" * 64)[1])


if __name__ == "__main__":
    unittest.main()
