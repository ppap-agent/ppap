"""OpenAPI contracts consumed by the Web application."""

from __future__ import annotations

import unittest

from api import app
from api import web_contract as wc
from pydantic import TypeAdapter, ValidationError

REQUIRED_ENDPOINTS: dict[tuple[str, str], bool] = {
    ("GET", "/public-config"): False,
    ("POST", "/login/feishu/host"): True,
    ("GET", "/me"): False,
    ("POST", "/files"): True,
    ("POST", "/files/{file_id}/versions/{version_id}/finalize"): True,
    ("GET", "/files/{file_id}/versions/{version_id}"): False,
    ("GET", "/file-uploads/{request_id}"): False,
    ("PUT", "/files/{file_id}/versions/{version_id}/blob"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/preview"): False,
    ("GET", "/integrations/feishu/files"): False,
    ("GET", "/integrations/feishu/docs/links"): False,
    ("POST", "/integrations/feishu/files/import"): True,
    ("GET", "/integrations/feishu/usage/quota"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"): False,
    ("GET", "/rules"): False,
    ("GET", "/audit-customers"): False,
    ("POST", "/audit-tasks"): True,
    ("GET", "/audit-tasks"): False,
    ("GET", "/audit-tasks/{task_id}"): False,
    ("GET", "/audit-tasks/{task_id}/recognitions/{request_id}"): False,
    ("POST", "/audit-tasks/{task_id}/end"): True,
    ("POST", "/audit-tasks/{task_id}/confirm"): True,
    ("POST", "/drafts"): True,
    ("GET", "/drafts"): False,
    ("POST", "/file-analyses"): True,
    ("POST", "/file-validations"): True,
    ("GET", "/file-validations/{validation_id}"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/validation"): False,
    ("GET", "/file-analyses/{analysis_id}"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/analysis"): False,
    ("GET", "/drafts/{draft_id}"): False,
    ("PUT", "/drafts/{draft_id}"): True,
    ("POST", "/drafts/{draft_id}/accept"): True,
    ("GET", "/rounds"): False,
    ("GET", "/usage"): False,
    ("GET", "/rounds/{round_id}"): False,
    ("GET", "/rounds/{round_id}/files/{file_id}/versions/{version_id}/preview"): False,
    ("POST", "/rounds/{round_id}/resubmit"): True,
    ("PUT", "/rounds/{round_id}/reviews"): True,
    ("GET", "/rule-types"): False,
    ("POST", "/rules/parse"): True,
    ("GET", "/rules/parse/{parse_id}"): False,
    ("POST", "/rules"): True,
    ("GET", "/rules/{rule_version_id}"): False,
    ("GET", "/integrations/feishu/rounds/{round_id}/report"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/report"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/office"): False,
}


def _response_schema(operation: dict) -> dict | None:
    for status in sorted(operation.get("responses") or {}):
        if not str(status).startswith("2"):
            continue
        content = operation["responses"][status].get("content") or {}
        media = content.get("application/json")
        if media and media.get("schema"):
            return media["schema"]
    return None


def _request_schema(operation: dict) -> dict | None:
    return ((operation.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")


class OpenAPIContractTests(unittest.TestCase):
    def test_suite_rule_contract_does_not_require_historical_routing_metadata(self):
        import json

        from audit.standard_suites import predefined_directory_suites

        leaf = next(suite for suite in predefined_directory_suites() if suite.source_path == "nio/N03")
        body = {key: value for key, value in json.loads(leaf.files["rules.json"]).items() if key not in {"customers", "preset_key", "models", "parts"}}
        observed = TypeAdapter(wc.EngineRuleBody).validate_python(body)
        self.assertEqual(observed["rules"], body["rules"])
        self.assertEqual(observed["checker_binding"], body["checker_binding"])

    def test_core_finding_contract_preserves_execution_identity_and_evidence(self):
        value = {
            "suite_revision_id": "suite-revision",
            "checker_digest": "sha256:checker",
            "verification_status": "unverified",
            "semantic": {
                "rule_version_id": "rules",
                "clause_id": "C1",
                "rule_text": "Inspect the original cell",
                "checks_performed": 1,
                "empty_scope_basis": "",
                "sources": [
                    {
                        "file_version_id": "file",
                        "filename": "sample.xlsx",
                        "role": "target",
                        "kind": "structure",
                        "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"},
                        "excerpt": "confirmed",
                    }
                ],
            },
        }
        self.assertEqual(TypeAdapter(wc.FindingLocator).validate_python(value), value)

    def test_pdf_cell_contract_preserves_one_based_original_pdf_coordinates(self):
        locator = {"kind": "pdf_cell", "page": 3, "row": 12, "column": 4}
        adapter = TypeAdapter(wc.SemanticLocator)
        self.assertEqual(adapter.validate_python(locator), locator)
        for field in ("page", "row", "column"):
            for value in (None, True, "1", 1.5, 0, -1):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    adapter.validate_python({**locator, field: value})
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                adapter.validate_python({key: value for key, value in locator.items() if key != field})
        for extra in ({"table": 1}, {"bbox": [0, 0, 1, 1]}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                adapter.validate_python({**locator, **extra})

    def test_failed_rule_plans_have_a_public_coverage_state_without_a_fake_verdict(self):
        failed = {
            "id": "B1",
            "rule_to_check": "正文每行频次非空，否则不通过。",
            "coverage": "execution_error",
            "status": "ERR",
            "reason": "审核计划生成失败，本项未执行。",
        }
        self.assertEqual(TypeAdapter(wc.RuleCoverageItem).validate_python(failed), failed)

    def test_finding_contract_preserves_the_existing_file_version_locator_without_requiring_it_on_legacy_rows(self):
        for locator in (
            {"kind": "file_version", "file_id": "file-a", "file_version_id": "version-a", "filename": "控制计划.xlsx", "rule_item_id": "1", "evidence": None},
            {"file_version_id": "legacy-a"},
        ):
            with self.subTest(locator=locator):
                finding = {"item_key": "1", "status": "NG", "item": "必须填写零件号", "classification": "身份", "suggestions": "请核对零件号", "priority": "P1", "locator": locator, "review": None}
                self.assertEqual(TypeAdapter(wc.Finding).validate_python(finding), finding)

    def test_failed_clause_without_a_finding_preserves_execution_error_coverage(self):
        check = {
            "rule_item": {"id": "1", "category": "身份", "priority": None, "rule_to_check": "核对零件号", "type_of_rule": "通用"},
            "execution": "failed",
            "coverage": "execution_error",
            "finding": None,
            "evidence_verified": False,
            "scope": None,
            "scope_notice": "本条审核执行失败。",
            "evidence": [],
        }
        self.assertEqual(TypeAdapter(wc.AuditClauseEvaluation).validate_python(check), check)

    def test_task_contract_is_shared_by_creation_readback_and_confirmation(self):
        spec = app.openapi()
        for path, method in (("/audit-tasks", "post"), ("/audit-tasks/{task_id}", "get"), ("/audit-tasks/{task_id}/confirm", "post")):
            self.assertEqual(_response_schema(spec["paths"][path][method]), {"$ref": "#/components/schemas/AuditTask"})
        self.assertIs(app.openapi(), spec)

        def check_refs(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    name = value["$ref"].removeprefix("#/components/schemas/")
                    self.assertIn(name, spec["components"]["schemas"])
                for child in value.values():
                    check_refs(child)
            elif isinstance(value, list):
                for child in value:
                    check_refs(child)

        check_refs(spec["components"]["schemas"])

    def test_major_web_operations_have_explicit_json_contracts(self):
        spec = app.openapi()
        paths = spec.get("paths") or {}
        failures: list[str] = []

        for (method, path), needs_body in REQUIRED_ENDPOINTS.items():
            operation = (paths.get(path) or {}).get(method.lower())
            label = f"{method} {path}"
            if not operation:
                failures.append(f"missing OpenAPI operation: {label}")
                continue
            response = _response_schema(operation)
            if not response or response == {"type": "object"}:
                failures.append(f"missing explicit 2xx JSON response schema: {label}")
            if needs_body:
                request = _request_schema(operation)
                if not request or request == {"type": "object"}:
                    failures.append(f"missing explicit JSON request schema: {label}")

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
