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
    ("GET", "/file-uploads/{request_id}"): False,
    ("PUT", "/files/{file_id}/versions/{version_id}/blob"): False,
    ("GET", "/files/{file_id}/versions/{version_id}/preview"): False,
    ("GET", "/integrations/feishu/files"): False,
    ("GET", "/integrations/feishu/docs/links"): False,
    ("POST", "/integrations/feishu/files/import"): True,
    ("GET", "/integrations/feishu/usage/quota"): False,
    ("POST", "/integrations/feishu/rounds/{round_id}/writeback"): False,
    ("GET", "/rules"): False,
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
    def test_report_package_has_one_request_bound_submission_read_and_binary_download(self):
        paths = app.openapi()["paths"]
        create = paths["/audit-report-packages"]["post"]
        self.assertEqual(set(_request_schema(create)["required"]), {"request_id", "reports"})
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
        self.assertEqual(set(_request_schema(confirmation)["required"]), {"request_id", "expected_revision"})
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
        self.assertEqual(set(values["properties"]), set(values["required"]))
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

    def test_ui_usage_is_a_read_projection_per_file_audit_not_a_pool_allocation(self):
        schemas = app.openapi()["components"]["schemas"]
        page = schemas["AuditUsagePage"]
        self.assertEqual(page["x-contract-stage"], "ui-draft")
        self.assertEqual(set(page["required"]), {"organization_id", "organization_name", "query", "summary", "entries", "total", "from_date", "through_date", "notices"})
        entry = schemas["AuditUsageEntry"]["properties"]
        for field in ("usage_id", "reference", "document_id", "audit_access", "orders", "allocation", "settlement", "initiator", "occurred_at"):
            self.assertIn(field, entry)
        self.assertEqual(entry["reference"]["$ref"], "#/components/schemas/AuditResultReference")
        self.assertEqual(entry["points"]["const"], 500)
        metric = schemas["AuditUsageMetric"]["properties"]
        self.assertTrue(any(option["type"] == "null" for option in metric["value"]["anyOf"]))
        self.assertEqual(schemas["AuditUsageQuery"]["properties"]["page_size"]["const"], 10)

    def test_ui_template_permission_change_has_its_own_revision_and_original_request_receipt(self):
        schemas = app.openapi()["components"]["schemas"]
        snapshot = schemas["AuditTemplatePermissions"]["properties"]
        for field in ("folder", "permission_revision", "manage_access", "members", "actions", "query"):
            self.assertIn(field, snapshot)
        folder = schemas["AuditTemplatePermissionFolder"]
        self.assertEqual(set(folder["required"]), {"customer_id", "folder_id"})
        request = schemas["AuditTemplatePermissionRequest"]
        self.assertEqual(set(request["required"]), {"request_id", "folder", "permission_revision", "action"})
        receipt = schemas["AuditTemplatePermissionReceipt"]
        self.assertEqual(receipt["properties"]["state"]["enum"], ["pending", "applied", "rejected", "conflict", "unknown"])
        self.assertIn("result_revision", receipt["required"])
        self.assertEqual(receipt["x-contract-stage"], "ui-draft")

    def test_ui_template_share_freezes_customer_folder_and_template_versions_not_reports(self):
        schemas = app.openapi()["components"]["schemas"]
        scope = schemas["AuditTemplateShareScope"]["properties"]
        for field in ("customer_id", "customer_name", "folder_id", "folder_revision", "templates", "link_access", "zip_access"):
            self.assertIn(field, scope)
        request = schemas["AuditTemplateShareRequest"]
        self.assertEqual(set(request["required"]), {"request_id", "organization_id", "scope", "targets", "content"})
        self.assertNotIn("reports", request["properties"])
        self.assertEqual(schemas["AuditTemplateShareContent"]["properties"]["kind"]["enum"], ["link", "zip"])
        job = schemas["AuditTemplateShare"]
        self.assertEqual(job["properties"]["deliveries"]["items"]["$ref"], "#/components/schemas/AuditShareDelivery")
        self.assertEqual(job["x-contract-stage"], "ui-draft")

    def test_ui_template_feedback_separates_attachments_submission_and_attempt_receipts(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        request = schemas["AuditTemplateFeedbackRequest"]
        self.assertEqual(set(request["required"]), {"request_id", "file_version_ids"})
        self.assertNotIn("customer", request["properties"])
        self.assertNotIn("phone", request["properties"])
        self.assertNotIn("email", request["properties"])
        receipt = schemas["AuditTemplateFeedbackReceipt"]
        self.assertEqual(receipt["properties"]["state"]["enum"], ["pending", "received", "rejected", "failed", "unknown"])
        for field in ("request", "attempt_id", "submission_id", "received_file_version_ids", "received_at", "retry_allowed"):
            self.assertIn(field, receipt["required"])
        self.assertEqual(receipt["x-contract-stage"], "ui-draft")
        self.assertNotIn("/template-feedback", spec["paths"])

    def test_ui_templates_keep_versions_coverage_and_support_outside_audit_rules(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        reference = schemas["AuditTemplateReference"]
        self.assertEqual(set(reference["required"]), {"customer_id", "template_id", "file_version_id"})
        catalog = schemas["AuditTemplateCatalog"]["properties"]
        self.assertIn("coverage_universe", catalog)
        customer = schemas["AuditTemplateCustomer"]["properties"]
        self.assertIn("effective_rule_types", customer)
        item = schemas["AuditTemplateItem"]["properties"]
        for field in ("reference", "file", "preview_access", "download_access", "share_access"):
            self.assertIn(field, item)
        self.assertNotIn("rule", item)
        self.assertNotIn("report", item)
        self.assertEqual(schemas["AuditTemplateQuery"]["properties"]["page_size"]["const"], 10)
        self.assertIn("complete", schemas["AuditTemplateZip"]["required"])
        self.assertEqual(set(schemas["AuditTemplateZipEntry"]["required"]), {"reference", "filename", "byte_size"})
        self.assertEqual(schemas["AuditSupportInfo"]["properties"]["state"]["enum"], ["configured", "unavailable"])
        for name in ("AuditTemplateCatalog", "AuditTemplatePage", "AuditTemplateZip", "AuditSupportInfo"):
            self.assertEqual(schemas[name]["x-contract-stage"], "ui-draft")
        self.assertNotIn("/templates", spec["paths"])

    def test_ui_archive_separates_provider_document_history_and_access_boundaries(self):
        schemas = app.openapi()["components"]["schemas"]
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
        self.assertEqual(schemas["AuditHistoryDetail"]["x-contract-stage"], "ui-draft")

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
