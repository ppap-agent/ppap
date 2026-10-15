"""OpenAPI contracts consumed by the Web application."""

from __future__ import annotations

import unittest

from ppap_api import app

REQUIRED_ENDPOINTS: dict[tuple[str, str], bool] = {
    ("GET", "/public-config"): False,
    ("POST", "/login/feishu/host"): True,
    ("GET", "/me"): False,
    ("POST", "/files"): True,
    ("POST", "/files/{file_id}/versions/{version_id}/finalize"): True,
    ("GET", "/files/{file_id}/versions/{version_id}"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/preview"): False,
    ("GET", "/integrations/feishu/files"): False,
    ("GET", "/integrations/feishu/docs/links"): False,
    ("POST", "/integrations/feishu/files/import"): True,
    ("GET", "/integrations/feishu/usage/quota"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"): False,
    ("GET", "/rules"): False,
    ("POST", "/drafts"): True,
    ("GET", "/drafts"): False,
    ("POST", "/file-analyses"): True,
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
    def test_ui_share_uses_organization_targets_and_per_target_recovery(self):
        schemas = app.openapi()["components"]["schemas"]
        target = schemas["AuditShareTarget"]["properties"]
        self.assertEqual(target["kind"]["enum"], ["user", "group"])
        self.assertEqual(target["eligibility"]["enum"], ["eligible", "blocked", "unknown"])
        self.assertIn("organization_id", target)
        request = schemas["AuditReportShareRequest"]
        self.assertEqual(set(request["required"]), {"request_id", "organization_id", "reports", "targets", "content"})
        delivery = schemas["AuditShareDelivery"]["properties"]
        self.assertEqual(delivery["state"]["enum"], ["pending", "sent", "failed", "unknown"])
        self.assertIn("attempt_id", delivery)
        self.assertIn("retry_allowed", delivery)
        read = schemas["AuditShareReadRequest"]
        self.assertEqual(set(read["required"]), {"request_id", "attempts"})
        self.assertEqual(set(schemas["AuditShareAttemptReference"]["required"]), {"target", "attempt_id"})
        self.assertEqual(schemas["AuditReportShare"]["x-contract-stage"], "ui-draft")

    def test_ui_reports_bind_pdf_and_packages_to_frozen_results(self):
        schemas = app.openapi()["components"]["schemas"]
        reference = schemas["AuditReportReference"]
        self.assertEqual(set(reference["required"]), {"task_id", "round_id", "file_version_id", "result_attempt", "report_id"})
        self.assertEqual(schemas["AuditReportArtifact"]["properties"]["content_type"]["enum"], ["application/pdf", "application/zip"])
        report = schemas["AuditFileReport"]["properties"]
        self.assertIn("generated_at", report)
        self.assertIn("artifact", report)
        self.assertIn("regenerate_allowed", report)
        self.assertIn("generation_request", report)
        package = schemas["AuditReportPackage"]
        self.assertEqual(package["x-contract-stage"], "ui-draft")
        self.assertIn("reports", package["required"])
        self.assertIn("receipt", package["required"])

    def test_ui_upload_distinguishes_unknown_from_a_retryable_terminal_failure(self):
        upload = app.openapi()["components"]["schemas"]["AuditUploadItem"]
        self.assertEqual(upload["x-contract-stage"], "ui-draft")
        self.assertEqual(upload["properties"]["state"]["enum"], ["uploading", "ready", "failed", "unknown"])
        self.assertIn("request_id", upload["required"])
        self.assertIn("retry_allowed", upload["required"])
        self.assertIn({"type": "null"}, upload["properties"]["file"]["anyOf"])

    def test_ui_review_keeps_execution_evidence_and_report_lifecycles_distinct(self):
        schemas = app.openapi()["components"]["schemas"]
        for name in ("AuditFileReview", "AuditClauseEvaluation", "AuditEvidenceReference", "AuditFileReport", "AuditAcceptance"):
            self.assertEqual(schemas[name]["x-contract-stage"], "ui-draft")
        self.assertEqual(schemas["AuditFileReview"]["properties"]["execution"]["enum"], ["queued", "running", "completed", "review_failed"])
        self.assertIn("result_attempt", schemas["AuditFileReview"]["properties"])
        self.assertIn({"type": "null"}, schemas["AuditEvidenceReference"]["properties"]["locator"]["anyOf"])
        self.assertIn("location_gap", schemas["AuditEvidenceReference"]["required"])
        self.assertEqual(schemas["AuditAcceptance"]["properties"]["state"]["enum"], ["pending", "accepted", "rejected"])
        self.assertIn("not_eligible", schemas["AuditFileReport"]["properties"]["state"]["enum"])

    def test_ui_task_contract_is_explicit_without_inventing_a_runtime_endpoint(self):
        spec = app.openapi()
        task = spec["components"]["schemas"]["AuditTask"]
        self.assertEqual(task["x-contract-stage"], "ui-draft")
        self.assertEqual(task["properties"]["state"]["enum"], ["matching", "pending_confirm", "reviewing", "completed"])
        self.assertIn("completion_reason", task["properties"])
        self.assertNotIn("/tasks", spec["paths"])
        self.assertNotIn("AuditTask", str(spec["paths"]))
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
