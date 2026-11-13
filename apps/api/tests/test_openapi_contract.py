"""OpenAPI contracts consumed by the Web application."""

from __future__ import annotations

import types
import typing
import unittest
from typing import get_type_hints

from api import app
from api import web_contract as wc
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response


def _response_schema(operation: dict) -> dict | None:
    for status in sorted(operation.get("responses") or {}):
        if not str(status).startswith("2"):
            continue
        content = operation["responses"][status].get("content") or {}
        media = content.get("application/json")
        if media and media.get("schema"):
            return media["schema"]
    return None


def _is_response(annotation) -> bool:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return all(_is_response(arg) for arg in typing.get_args(annotation))
    return isinstance(annotation, type) and issubclass(annotation, Response)


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

    def test_finding_locator_accepts_file_version_and_bare_version_rows(self):
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

    def test_every_serialized_json_route_has_explicit_contracts(self):
        """Routes FastAPI serializes declare their response and JSON body; manual Responses are exempt."""
        spec = app.openapi()
        failures: list[str] = []
        for route in app.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            returned = get_type_hints(route.endpoint).get("return")
            if not isinstance(route.response_class, DefaultPlaceholder) or _is_response(returned):
                continue
            for method in route.methods:
                operation = spec["paths"][route.path][method.lower()]
                label = f"{method} {route.path}"
                if _response_schema(operation) in (None, {}, {"type": "object"}):
                    failures.append(f"missing explicit 2xx JSON response schema: {label}")
                if "requestBody" in operation and _request_schema(operation) in (None, {}, {"type": "object"}):
                    failures.append(f"missing explicit JSON request schema: {label}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
