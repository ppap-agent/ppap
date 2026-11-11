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

        from audit.suite_migration import migrated_builtin_suite

        body = json.loads(migrated_builtin_suite("v3", "控制计划").files["rules.json"])
        observed = TypeAdapter(wc.EngineRuleBody).validate_python(body)
        self.assertEqual(observed["rules"], body["rules"])
        self.assertEqual(observed["checker_binding"], body["checker_binding"])
        self.assertNotIn("rule_profile", observed)
        self.assertNotIn("customers", observed)

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
        schema = app.openapi()["components"]["schemas"]["PdfCellLocator"]
        self.assertEqual(set(schema["required"]), set(locator))
        self.assertFalse(schema["additionalProperties"])
        for field in ("page", "row", "column"):
            self.assertEqual(schema["properties"][field]["minimum"], 1)
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
        schema = app.openapi()["components"]["schemas"]["RuleCoverageItem"]
        self.assertEqual(set(schema["properties"]["coverage"]["enum"]), {"mapped", "unsupported", "executed", "execution_error"})

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
        schema = app.openapi()["components"]["schemas"]["AuditClauseEvaluation"]
        self.assertIn("execution_error", schema["properties"]["coverage"]["enum"])

    def test_native_preparation_reuses_draft_and_promotes_one_explicit_snapshot(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        self.assertEqual(schemas["Draft"]["properties"]["upload_preparation"]["anyOf"][0]["$ref"], "#/components/schemas/AuditUploadPreparation")
        self.assertEqual(set(schemas["AuditUploadPreparation"]["required"]), {"version", "analysis_mode", "customer_preference", "predecessor_id", "uploads", "files"})
        self.assertEqual(schemas["AuditPreparedUpload"]["properties"]["removed"]["type"], "boolean")
        self.assertEqual(schemas["Draft"]["properties"]["task_id"]["anyOf"][0]["type"], "string")
        create = _request_schema(spec["paths"]["/drafts"]["post"])
        save = _request_schema(spec["paths"]["/drafts/{draft_id}"]["put"])
        self.assertIn("upload_preparation", create["properties"])
        self.assertEqual(save["properties"]["expected_revision"]["type"], "integer")
        recognize = _request_schema(spec["paths"]["/audit-tasks"]["post"])
        self.assertEqual(recognize["properties"]["source_draft_id"]["type"], "string")
        self.assertEqual(recognize["properties"]["expected_draft_revision"]["type"], "integer")

    def test_file_report_recovery_binds_the_same_frozen_reference_and_original_request(self):
        paths = app.openapi()["paths"]
        path = "/audit-tasks/{task_id}/reports/{report_id}/regenerations"
        request = _request_schema(paths[path]["post"])
        self.assertEqual(set(request["required"]), {"request_id", "reference"})
        self.assertEqual(set(request["properties"]["reference"]["required"]), {"task_id", "report_id", "round_id", "file_version_id", "result_attempt"})
        self.assertEqual(request["properties"]["reference"]["properties"]["result_attempt"]["type"], "integer")
        read = paths[path + "/{request_id}"]["get"]
        self.assertEqual({item["name"] for item in read["parameters"] if item.get("required")}, {"task_id", "report_id", "request_id", "round_id", "file_version_id", "result_attempt"})
        self.assertEqual(_response_schema(read)["anyOf"][0]["$ref"], "#/components/schemas/AuditReportUpdate")

    def test_report_package_has_one_request_bound_submission_read_and_binary_download(self):
        paths = app.openapi()["paths"]
        create = paths["/audit-report-packages"]["post"]
        self.assertEqual(set(_request_schema(create)["required"]), {"request_id", "reports"})
        retry = paths["/audit-report-packages/{source_request_id}/retries"]["post"]
        self.assertEqual(set(_request_schema(retry)["required"]), {"request_id", "package_id", "expected_attempt"})
        self.assertEqual(_request_schema(retry)["properties"]["expected_attempt"]["type"], "integer")
        self.assertEqual(_response_schema(paths["/audit-report-packages/{request_id}"]["get"])["anyOf"][0]["$ref"], "#/components/schemas/AuditReportPackage")
        download = paths["/audit-report-packages/{request_id}/zip"]["get"]
        self.assertEqual({item["name"] for item in download["parameters"] if item.get("required")}, {"request_id", "package_id", "artifact_id"})
        self.assertEqual(download["responses"]["200"]["content"]["application/zip"]["schema"], {"type": "string", "format": "binary"})

    def test_file_report_download_binds_the_frozen_reference_and_returns_pdf_bytes(self):
        operation = app.openapi()["paths"]["/audit-tasks/{task_id}/reports/{report_id}/pdf"]["get"]
        required = {parameter["name"] for parameter in operation["parameters"] if parameter.get("required")}
        self.assertEqual(required, {"task_id", "report_id", "round_id", "file_version_id", "result_attempt", "artifact_id"})
        self.assertEqual(operation["responses"]["200"]["content"]["application/pdf"]["schema"], {"type": "string", "format": "binary"})

    def test_task_runtime_has_one_explicit_revision_bound_confirmation_contract(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        task = schemas["AuditTask"]
        self.assertNotIn("x-contract-stage", task)
        self.assertEqual(task["properties"]["files"]["items"]["$ref"], "#/components/schemas/AuditTaskFile")
        self.assertIn("match", schemas["AuditTaskFile"]["properties"])
        self.assertIn("acceptance", task["properties"])
        confirmation = spec["paths"]["/audit-tasks/{task_id}/confirm"]["post"]
        self.assertEqual(_response_schema(confirmation)["$ref"], "#/components/schemas/AuditTask")
        self.assertIn("confirmation_basis_digest", task["required"])
        self.assertEqual(task["properties"]["confirmation_basis_digest"]["anyOf"], [{"type": "string"}, {"type": "null"}])
        self.assertIn("confirmation_scope", task["required"])
        self.assertEqual(task["properties"]["confirmation_scope"]["anyOf"], [{"$ref": "#/components/schemas/AuditTaskConfirmationScope"}, {"type": "null"}])
        self.assertEqual(set(schemas["AuditTaskConfirmationScope"]["required"]), {"target_file_version_ids", "reference_file_version_ids", "excluded_files"})
        self.assertEqual(set(schemas["AuditTaskConfirmationExclusion"]["required"]), {"file_version_id", "reason"})
        self.assertEqual(set(_request_schema(confirmation)["required"]), {"request_id", "expected_revision", "expected_confirmation_basis_digest"})
        self.assertEqual(_request_schema(confirmation)["properties"]["expected_confirmation_basis_digest"]["type"], "string")
        request = _request_schema(app.openapi()["paths"]["/audit-tasks"]["post"])
        self.assertEqual(set(request["required"]), {"request_id", "analysis_mode", "customer_preference", "files"})
        self.assertEqual(set(request["properties"]["files"]["items"]["required"]), {"file_version_id", "validation_id", "selection", "removal_reason"})

    def test_file_validation_is_a_runtime_contract_separate_from_matching_and_audit(self):
        schemas = app.openapi()["components"]["schemas"]
        job = schemas["FileValidationJob"]
        self.assertEqual(set(job["required"]), {"id", "source_file_id", "source_file_version_id", "status", "result"})
        self.assertIn({"$ref": "#/components/schemas/AuditFileValidation"}, job["properties"]["result"]["anyOf"])
        self.assertNotIn("x-contract-stage", schemas["AuditFileValidation"])
        self.assertNotIn("x-contract-stage", schemas["AuditTask"])

    def test_ui_zip_outcomes_bind_preparation_results_to_the_fixed_report_reference(self):
        schemas = app.openapi()["components"]["schemas"]
        item = schemas["AuditReportPackageOutcome"]
        self.assertEqual(set(item["required"]), {"reference", "state", "reason"})
        self.assertEqual(item["properties"]["state"]["enum"], ["pending", "prepared", "failed"])
        self.assertEqual(item["properties"]["reference"]["$ref"], "#/components/schemas/AuditReportReference")
        package = schemas["AuditReportPackage"]
        self.assertIn("outcomes", package["properties"])
        self.assertNotIn("outcomes", package["required"])

    def test_display_corrections_are_separate_from_frozen_metadata_and_bind_the_original_request(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        values = schemas["AuditFileDisplayValues"]
        self.assertEqual(set(values["required"]), {"customer", "model", "part_number", "part"})
        # Receipts saved before project existed have no project key.
        self.assertEqual(set(values["properties"]), {*values["required"], "project"})
        reference = schemas["AuditFileDisplayReference"]
        self.assertEqual(set(reference["required"]), {"task_id", "round_id", "file_version_id"})
        request = schemas["AuditFileDisplayRequest"]
        self.assertEqual(set(request["required"]), {"request_id", "reference", "expected_revision", "values"})
        receipt = schemas["AuditFileDisplayReceipt"]
        self.assertEqual(receipt["properties"]["state"]["enum"], ["pending", "saved", "rejected", "conflict", "unknown"])
        self.assertEqual(set(receipt["required"]), {"request", "state", "info", "reason"})
        self.assertNotIn("x-contract-stage", receipt)
        file = schemas["AuditTaskFile"]
        self.assertIn("display_info", file["properties"])
        self.assertNotIn("display_info", file["required"])
        self.assertIn("display_edit_access", file["properties"])
        self.assertNotIn("display_info", schemas["AuditRuleMatch"]["properties"])
        self.assertIn("/audit-tasks/{task_id}/display-info", spec["paths"])
        self.assertIn("/audit-tasks/{task_id}/display-info/{request_id}", spec["paths"])

    def test_ui_completion_estimate_is_an_optional_service_interval_separate_from_counts(self):
        schemas = app.openapi()["components"]["schemas"]
        estimate = schemas["AuditCompletionEstimate"]
        self.assertEqual(set(estimate["required"]), {"observed_at", "earliest_at", "latest_at"})
        task = schemas["AuditTask"]
        self.assertNotIn("completion_estimate", task["required"])
        self.assertIn("completion_estimate", task["properties"])
        self.assertEqual(set(schemas["AuditProgress"]["properties"]), {"completed", "total"})
        metadata = schemas["Metadata"]
        self.assertNotIn("part_number", metadata["required"])
        self.assertIn("part_number", metadata["properties"])

    def test_usage_is_a_read_projection_per_file_audit_not_a_pool_allocation(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        page = schemas["AuditUsagePage"]
        response = spec["paths"]["/audit-usage"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response["$ref"], "#/components/schemas/AuditUsagePage")
        self.assertEqual(set(page["required"]), {"organization_id", "organization_name", "query", "summary", "entries", "total", "from_date", "through_date", "notices"})
        entry = schemas["AuditUsageEntry"]["properties"]
        for field in ("usage_id", "reference", "document_id", "audit_access", "orders", "allocation", "settlement", "initiator", "occurred_at"):
            self.assertIn(field, entry)
        self.assertEqual(entry["reference"]["$ref"], "#/components/schemas/AuditResultReference")
        self.assertEqual(entry["points"]["const"], 500)
        metric = schemas["AuditUsageMetric"]["properties"]
        self.assertTrue(any(option["type"] == "null" for option in metric["value"]["anyOf"]))
        self.assertEqual(schemas["AuditUsageQuery"]["properties"]["page_size"]["const"], 10)

    def test_support_has_a_real_read_only_api_and_version_bound_image(self):
        from fastapi.testclient import TestClient

        spec = app.openapi()
        self.assertEqual(spec["paths"]["/support"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AuditSupportInfo")
        self.assertNotIn("x-contract-stage", spec["components"]["schemas"]["AuditSupportInfo"])
        image = spec["paths"]["/support/qr"]["get"]
        self.assertTrue(next(parameter for parameter in image["parameters"] if parameter["name"] == "config_version")["required"])
        self.assertEqual(set(image["responses"]["200"]["content"]), {"image/png", "image/jpeg", "image/webp"})
        self.assertEqual(TestClient(app).get("/support").status_code, 401)

    def test_archive_separates_provider_document_history_and_access_boundaries(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        reference = schemas["AuditResultReference"]
        self.assertEqual(set(reference["required"]), {"task_id", "round_id", "file_version_id", "result_attempt"})
        document = schemas["AuditArchiveDocument"]["properties"]
        self.assertIn("document_id", document)
        self.assertNotIn("object_ref", document)
        self.assertNotIn("task", document)
        self.assertIn("latest_completed", document)
        detail = schemas["AuditHistoryDetail"]["properties"]
        self.assertIn("frozen_file_version_ids", detail)
        self.assertIn("originals", detail)
        self.assertIn("source_task_access", detail)
        self.assertIn("report_access", detail)
        self.assertEqual(schemas["AuditArchiveQuery"]["properties"]["page_size"]["const"], 10)
        response = spec["paths"]["/audit-archive/{document_id}/history/result"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response["$ref"], "#/components/schemas/AuditHistoryDetail")

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
        self.assertEqual(schemas["AuditReportShare"]["properties"]["receipt"]["$ref"], "#/components/schemas/AuditAcceptance")

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
        self.assertIn("reports", package["required"])
        self.assertIn("receipt", package["required"])

    def test_upload_receipt_distinguishes_unknown_from_a_retryable_terminal_failure(self):
        spec = app.openapi()
        upload = spec["components"]["schemas"]["AuditUploadItem"]
        self.assertEqual(_response_schema(spec["paths"]["/file-uploads/{request_id}"]["get"]), {"$ref": "#/components/schemas/AuditUploadItem"})
        self.assertEqual(upload["properties"]["state"]["enum"], ["uploading", "ready", "failed", "unknown"])
        self.assertIn("request_id", upload["required"])
        self.assertIn("retry_allowed", upload["required"])
        self.assertEqual(upload["properties"]["resume_allowed"]["type"], "boolean")
        self.assertIn({"type": "null"}, upload["properties"]["file"]["anyOf"])

    def test_runtime_review_keeps_execution_evidence_and_report_lifecycles_distinct(self):
        schemas = app.openapi()["components"]["schemas"]
        for name in ("AuditFileReview", "AuditClauseEvaluation", "AuditEvidenceReference", "AuditFileReport", "AuditAcceptance"):
            self.assertNotIn("x-contract-stage", schemas[name])
        self.assertEqual(schemas["AuditFileReview"]["properties"]["execution"]["enum"], ["queued", "running", "completed", "review_failed"])
        self.assertIn("result_attempt", schemas["AuditFileReview"]["properties"])
        self.assertIn({"type": "null"}, schemas["AuditEvidenceReference"]["properties"]["locator"]["anyOf"])
        self.assertIn("location_gap", schemas["AuditEvidenceReference"]["required"])
        self.assertEqual(schemas["AuditAcceptance"]["properties"]["state"]["enum"], ["pending", "accepted", "rejected"])
        self.assertIn("not_eligible", schemas["AuditFileReport"]["properties"]["state"]["enum"])

    def test_task_contract_is_shared_by_creation_readback_and_confirmation(self):
        spec = app.openapi()
        task = spec["components"]["schemas"]["AuditTask"]
        self.assertNotIn("x-contract-stage", task)
        self.assertEqual(task["properties"]["state"]["enum"], ["matching", "pending_confirm", "reviewing", "completed"])
        self.assertIn("completion_reason", task["properties"])
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
