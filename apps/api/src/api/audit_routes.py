from __future__ import annotations

from datetime import date
from typing import Any, cast

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from product.files import ObjectMissing, attachment_content_disposition, original_filename, safe_content_type
from product.identity import can_read, can_review
from product.store import AuthStore

from api import web_contract as wc


def register_audit_routes(app: FastAPI, store: AuthStore, principal_of, quota_for) -> None:
    def require_principal(request: Request):
        principal = principal_of(request)
        if not principal:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        return principal, None

    def require_object(request: Request, resource_id: str, review: bool = False):
        principal, denied = require_principal(request)
        if denied:
            return None, denied
        resource = store.resource(resource_id)
        grants = store.grants(resource_id, principal.user_id) if resource else set()
        if resource is None or not can_read(principal, resource, grants):
            return principal, JSONResponse({"error": "access_denied"}, status_code=403)
        if review and not can_review(principal, resource, grants):
            return principal, JSONResponse({"error": "access_denied"}, status_code=403)
        return principal, None

    def fail(exc: Exception) -> JSONResponse:
        if isinstance(exc, KeyError):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        code = str(exc)
        status = (
            409
            if code
            in {
                "idempotency_conflict",
                "draft_already_accepted",
                "draft_revision_conflict",
                "upload_preparation_identity_conflict",
                "upload_preparation_input_conflict",
                "file_not_ready",
                "review_conflict",
                "review_hint_response_conflict",
                "deep_not_available",
                "personal_model_configuration_changed",
                "task_input_locked",
                "validation_scope_mismatch",
                "customer_scope_conflict",
                "precheck_blocked",
                "precheck_not_ready",
                "task_not_ready",
                "task_not_zero_match",
                "task_revision_conflict",
                "task_confirmation_changed",
                "task_file_provenance_required",
                "precheck_required",
                "audit_task_charging_unavailable",
                "usage_quota_unavailable",
                "usage_ai_disabled",
                "usage_balance_empty",
                "task_not_ready",
                "target_required",
                "rule_candidate_expired",
                "reaudit_source_not_finished",
                "reaudit_scope_changed",
                "report_not_ready",
                "report_artifact_conflict",
                "report_package_too_large",
                "report_recovery_unavailable",
                "audit_report_charging_unavailable",
                "report_package_recovery_unavailable",
                "notification_revision_conflict",
                "notification_already_dispatched",
            }
            else 400
        )
        return JSONResponse({"error": code}, status_code=status)

    @app.get("/rules", response_model=wc.RulesList)
    def rules_list(request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse({"rules": store.list_rules(principal.tenant_id)})

    @app.get("/audit-archive", response_model=wc.AuditArchivePage)
    def audit_archive_list(
        request: Request,
        search: str = Query("", max_length=200),
        category_id: str | None = None,
        document_type: str | None = None,
        customer: str | None = None,
        initiator: str | None = None,
        from_date: date | None = None,
        through_date: date | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=10, le=10),
    ) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if from_date and through_date and from_date > through_date:
            return JSONResponse({"error": "invalid_archive_dates"}, status_code=400)
        query = {
            "search": search,
            "category_id": category_id,
            "document_type": document_type,
            "customer": customer,
            "initiator": initiator,
            "from_date": from_date.isoformat() if from_date else None,
            "through_date": through_date.isoformat() if through_date else None,
            "page": page,
            "page_size": page_size,
        }
        try:
            return JSONResponse(store.list_audit_archive(principal, query))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-archive/{document_id}/history", response_model=wc.AuditHistoryPage)
    def audit_history_list(document_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.read_audit_history(principal, document_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-archive/{document_id}/history/result", response_model=wc.AuditHistoryDetail)
    def audit_history_result(document_id: str, task_id: str, round_id: str, file_version_id: str, request: Request, result_attempt: int | None = Query(None, ge=1)) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.read_audit_history(principal, document_id, {"task_id": task_id, "round_id": round_id, "file_version_id": file_version_id, "result_attempt": result_attempt}))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks", response_model=wc.AuditTask, status_code=202, openapi_extra=wc.openapi_body(wc.AuditRecognitionRequest))
    def audit_tasks_create(body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.create_audit_task(principal, body, usage_policy=lambda actor: quota_for(request, actor)), status_code=202)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks", response_model=wc.AuditRecognitionTasks)
    def audit_tasks_list(request: Request, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0), q: str = Query("", max_length=200), state: str = "") -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            rows = store.list_audit_tasks(principal, limit + 1, offset, q, state)
            return JSONResponse({"tasks": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}", response_model=wc.AuditTask)
    def audit_tasks_get(task_id: str, request: Request, request_id: str | None = None) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_audit_task(principal, task_id, request_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks/{task_id}/prechecks", response_model=wc.PrecheckReceipt, status_code=202, openapi_extra=wc.openapi_body(wc.PrecheckRequest))
    def precheck_request(task_id: str, body: dict, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.request_task_precheck(principal, task_id, body.get("request_id"), body.get("expected_revision")), status_code=202)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/prechecks", response_model=wc.PrecheckHistory)
    def precheck_history(task_id: str, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.list_task_prechecks(principal, task_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/prechecks/{snapshot_id}", response_model=wc.PrecheckReceipt)
    def precheck_read(task_id: str, snapshot_id: str, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_task_precheck(principal, task_id, snapshot_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/prechecks/{snapshot_id}/report", response_class=Response)
    def precheck_report(task_id: str, snapshot_id: str, request: Request):
        from product.prechecks import render_precheck_record

        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            receipt = store.get_task_precheck(principal, task_id, snapshot_id)
            if receipt["status"] not in {"succeeded", "failed", "cancelled"}:
                raise ValueError("precheck_not_ready")
            return Response(
                render_precheck_record(receipt), media_type="text/html", headers={"Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/rounds/{round_id}/reaudit-inputs", response_model=wc.AuditReauditInputs)
    def reaudit_inputs(round_id: str, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        try:
            return JSONResponse(store.reaudit_inputs(principal, round_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/notification", response_model=wc.AuditNotificationPreference)
    def audit_notification_get(task_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_task_notification(principal, task_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.put("/audit-tasks/{task_id}/notification", response_model=wc.AuditNotificationPreference, openapi_extra=wc.openapi_body(wc.AuditNotificationPreferenceRequest))
    def audit_notification_save(task_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.save_task_notification(principal, task_id, body))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks/{task_id}/confirm", response_model=wc.AuditTask, status_code=201, openapi_extra=wc.openapi_body(wc.AuditTaskConfirmRequest))
    def audit_tasks_confirm(task_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            # A missing basis can only recover an already accepted historical
            # operation; the store rejects every new confirmation without it.
            if set(body) not in ({"request_id", "expected_revision"}, {"request_id", "expected_revision", "expected_confirmation_basis_digest"}):
                raise ValueError("invalid_task_confirmation")
            return JSONResponse(
                store.confirm_audit_task(
                    principal,
                    task_id,
                    body["request_id"],
                    body["expected_revision"],
                    usage_policy=lambda actor: quota_for(request, actor),
                    expected_confirmation_basis_digest=body.get("expected_confirmation_basis_digest"),
                    require_confirmation_basis=True,
                ),
                status_code=201,
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/recognitions/{request_id}", response_model=wc.AuditRecognitionRead)
    def audit_tasks_recognition_get(task_id: str, request_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_audit_task_recognition(principal, task_id, request_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks/{task_id}/end", response_model=wc.AuditTask, openapi_extra=wc.openapi_body(wc.AuditTaskEndRequest))
    def audit_tasks_end(task_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.end_unmatched_task(principal, task_id, cast(int, body.get("expected_revision")), body.get("removals", []), body.get("unselected_file_version_ids")))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks/{task_id}/display-info", response_model=wc.AuditFileDisplayReceipt, openapi_extra=wc.openapi_body(wc.AuditFileDisplayRequest))
    def audit_display_save(task_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.save_audit_display(principal, task_id, body))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/display-info/{request_id}", response_model=wc.AuditFileDisplayReceipt | None)
    def audit_display_read(task_id: str, request_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_audit_display(principal, task_id, request_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/reports/{report_id}/pdf", response_class=Response, responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def audit_report_pdf(
        task_id: str, report_id: str, request: Request, round_id: str, file_version_id: str, result_attempt: int = Query(ge=1), artifact_id: str = Query(min_length=1, max_length=200)
    ) -> Response:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            artifact, content = store.audit_report_pdf(
                principal, {"task_id": task_id, "report_id": report_id, "round_id": round_id, "file_version_id": file_version_id, "result_attempt": result_attempt}, artifact_id
            )
            return Response(content, media_type="application/pdf", headers={"Content-Disposition": attachment_content_disposition(artifact["filename"]), "Cache-Control": "private, no-store"})
        except ObjectMissing:
            return JSONResponse({"error": "report_artifact_unavailable"}, status_code=409)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-tasks/{task_id}/reports/{report_id}/regenerations", response_model=wc.AuditReportUpdate, status_code=201, openapi_extra=wc.openapi_body(wc.AuditReportRegenerationRequest))
    def audit_report_regenerate(task_id: str, report_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            reference = body.get("reference")
            if set(body) != {"request_id", "reference"} or not isinstance(reference, dict) or reference.get("task_id") != task_id or reference.get("report_id") != report_id:
                raise ValueError("invalid_report_regeneration")
            return JSONResponse(store.regenerate_audit_report(principal, reference, body["request_id"], usage_policy=lambda actor: quota_for(request, actor)), status_code=201)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-tasks/{task_id}/reports/{report_id}/regenerations/{request_id}", response_model=wc.AuditReportUpdate | None)
    def audit_report_regeneration_read(task_id: str, report_id: str, request_id: str, request: Request, round_id: str, file_version_id: str, result_attempt: int = Query(ge=1)) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            reference = {"task_id": task_id, "report_id": report_id, "round_id": round_id, "file_version_id": file_version_id, "result_attempt": result_attempt}
            return JSONResponse(store.read_audit_report_regeneration(principal, reference, request_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-report-packages", response_model=wc.AuditReportPackage, status_code=202, openapi_extra=wc.openapi_body(wc.AuditReportPackageRequest))
    def audit_report_package_create(body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.accept_report_package(principal, body), status_code=202)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/audit-report-packages/{source_request_id}/retries", response_model=wc.AuditReportPackage, status_code=202, openapi_extra=wc.openapi_body(wc.AuditReportPackageRetryRequest))
    def audit_report_package_retry(source_request_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.retry_report_package(principal, source_request_id, body, usage_policy=lambda actor: quota_for(request, actor)), status_code=202)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-report-packages/{request_id}", response_model=wc.AuditReportPackage | None)
    def audit_report_package_read(request_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_report_package(principal, request_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/audit-report-packages/{request_id}/zip", response_class=Response, responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
    def audit_report_package_download(request_id: str, request: Request, package_id: str, artifact_id: str = Query(min_length=1, max_length=200)) -> Response:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            artifact, content = store.report_package_zip(principal, request_id, package_id, artifact_id)
            return Response(content, media_type="application/zip", headers={"Content-Disposition": attachment_content_disposition(artifact["filename"]), "Cache-Control": "private, no-store"})
        except ObjectMissing:
            return JSONResponse({"error": "report_artifact_unavailable"}, status_code=409)
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.post("/drafts", response_model=wc.Draft, status_code=201, openapi_extra=wc.openapi_body(wc.DraftCreateRequest, required=False))
    def drafts_create(request: Request, body: dict | None = None) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            value = body or {}
            return JSONResponse(
                store.create_draft(
                    principal,
                    value.get("predecessor_id"),
                    request_id=value.get("request_id"),
                    items=value.get("items"),
                    case_context=value.get("case_context"),
                    upload_preparation=value.get("upload_preparation"),
                ),
                status_code=201,
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/drafts", response_model=wc.DraftList)
    def drafts_list(request: Request, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0), q: str = Query("", max_length=200)) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        rows = store.list_drafts(principal, limit + 1, offset, q)
        return JSONResponse({"drafts": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})

    @app.post("/file-analyses", response_model=wc.AnalysisJob, status_code=202, openapi_extra=wc.openapi_body(wc.FileAnalysisRequest))
    def files_analyze(body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            result = store.accept_file_analysis(principal, body.get("request_id"), body.get("source_file_id"), body.get("source_file_version_id"))
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(result, status_code=202)

    @app.post("/file-validations", response_model=wc.FileValidationJob, status_code=202, openapi_extra=wc.openapi_body(wc.FileValidationRequest))
    def files_validate(body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            result = store.accept_file_validation(principal, body.get("request_id"), body.get("source_file_id"), body.get("source_file_version_id"), scope=body.get("scope"))
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(result, status_code=202)

    @app.get("/file-validations/{validation_id}", response_model=wc.FileValidationJob)
    def files_validation_get(validation_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_file_validation(principal, validation_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/files/{file_id}/versions/{version_id}/validation", response_model=wc.LatestFileValidation)
    def files_latest_validation(file_id: str, version_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse({"validation": store.latest_file_validation(principal, file_id, version_id)})
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/file-analyses/{analysis_id}", response_model=wc.AnalysisJob)
    def files_analysis_get(analysis_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_file_analysis(principal, analysis_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/files/{file_id}/versions/{version_id}/analysis", response_model=wc.LatestAnalysis)
    def files_latest_analysis(file_id: str, version_id: str, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse({"analysis": store.latest_file_analysis(principal, file_id, version_id)})
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/drafts/{draft_id}", response_model=wc.Draft)
    def drafts_get(draft_id: str, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        try:
            draft = store.get_draft(draft_id, principal)
        except (KeyError, ValueError) as exc:
            return fail(exc)
        if draft is None or cast(Any, store.resource(draft_id)).tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(draft)

    @app.put("/drafts/{draft_id}", response_model=wc.Draft, openapi_extra=wc.openapi_body(wc.DraftSaveRequest))
    def drafts_save(draft_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        items = body.get("items") if "items" in body else None
        analysis_mode = body.get("analysis_mode") if "analysis_mode" in body else None
        writeback_target = body.get("writeback_target") if "writeback_target" in body else None
        if "rule_version_id" in body:
            return JSONResponse({"error": "per_target_rules_required"}, status_code=400)
        try:
            draft = store.save_draft(
                principal,
                draft_id,
                items,
                analysis_mode,
                writeback_target,
                body.get("case_context"),
                upload_preparation=body.get("upload_preparation"),
                expected_revision=body.get("expected_revision"),
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(draft)

    @app.get("/drafts/{draft_id}/round", response_model=wc.Round | None)
    def drafts_round(draft_id: str, request: Request):
        _principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        round_id = store.draft_round_id(draft_id)
        if round_id is None:
            return None
        _principal, denied = require_object(request, round_id)
        if denied:
            return denied
        return store.get_round(round_id)

    @app.post("/drafts/{draft_id}/accept", response_model=wc.Round, status_code=201, openapi_extra=wc.openapi_body(wc.DraftAcceptRequest))
    def drafts_accept(draft_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        try:
            model_consent = {}
            if "expected_model_revision" in body:
                expected = body["expected_model_revision"]
                if expected is not None and (not isinstance(expected, str) or not 1 <= len(expected) <= 64):
                    raise ValueError("personal_model_configuration_changed")
                model_consent["expected_model_revision"] = expected
            round_body = store.accept_draft(
                principal, draft_id, cast(str, body.get("idempotency_key")), body.get("predecessor_id"), usage_policy=lambda actor: quota_for(request, actor), **model_consent
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(round_body, status_code=201)

    @app.get("/rounds", response_model=wc.RoundList)
    def rounds_list(
        request: Request, limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0), q: str = Query("", max_length=200), execution: str = "", unreviewed: bool = False
    ) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if execution not in {"", "queued", "running", "completed", "failed"}:
            return JSONResponse({"error": "invalid_execution"}, status_code=400)
        rows = store.list_rounds(principal, limit + 1, offset, q, execution, unreviewed)
        return JSONResponse({"rounds": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})

    @app.get("/usage", response_model=wc.UsageList)
    def usage_list(request: Request, limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0), since: date | None = None, until: date | None = None) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if since and until and since > until:
            return JSONResponse({"error": "invalid_date_range"}, status_code=400)
        rows = store.list_usage_events(principal, limit + 1, offset, since, until)
        return JSONResponse({"events": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})

    @app.get("/audit-usage", response_model=wc.AuditUsagePage)
    def audit_usage(request: Request, range: str = "30d", search: str = Query("", max_length=200), page: int = Query(1, ge=1), page_size: int = Query(10, ge=10, le=10)) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if range not in {"7d", "30d", "90d", "all"}:
            return JSONResponse({"error": "invalid_usage_range"}, status_code=400)
        try:
            return JSONResponse(store.list_audit_usage(principal, {"range": range, "search": search, "page": page, "page_size": page_size}))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.put("/rounds/{round_id}/review-document-pairs", response_model=wc.ReviewDocumentPairs, openapi_extra=wc.openapi_body(wc.DocumentPair))
    def review_document_pair(round_id: str, body: dict, request: Request):
        from product.store.review_hints import declare_pair

        principal, denied = require_object(request, round_id, review=True)
        if denied:
            return denied
        try:
            return JSONResponse(declare_pair(store, principal, round_id, body.get("pdf_file_version_id"), body.get("excel_file_version_id")))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/review-hints/responses", response_model=wc.ReviewHintCounts)
    def review_hint_counts(request: Request):
        from product.store.review_hints import response_counts

        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse(response_counts(store, principal))

    @app.put("/rounds/{round_id}/review-hints", response_model=wc.ReviewHintReceipt, openapi_extra=wc.openapi_body(wc.ReviewHintResponse))
    def review_hint_response(round_id: str, body: dict, request: Request):
        from product.store.review_hints import respond

        principal, denied = require_object(request, round_id, review=True)
        if denied:
            return denied
        try:
            return JSONResponse(respond(store, principal, round_id, body.get("item_key"), body.get("action"), body.get("hint")))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/rounds/{round_id}", response_model=wc.Round)
    def rounds_get(round_id: str, request: Request, read_view: bool = False) -> JSONResponse:
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        body = store.get_round(round_id, read_view=True, principal=principal) if read_view else store.get_round(round_id, principal=principal)
        if body is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(body)

    @app.get("/rounds/{round_id}/files/{file_id}/versions/{version_id}/download")
    def rounds_download(round_id: str, file_id: str, version_id: str, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            version = store.read_file_version(principal, file_id, version_id, round_id=round_id)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        try:
            data = store.version_bytes(version)
        except (ValueError, ObjectMissing):
            return JSONResponse({"error": "not_ready"}, status_code=409)
        try:
            filename = original_filename(version.original_filename)
        except ValueError:
            filename = "download"
        return Response(
            content=data,
            media_type=safe_content_type(version.content_type),
            headers={
                "Content-Disposition": attachment_content_disposition(filename),
                "X-Content-SHA256": version.sha256 or "",
            },
        )

    @app.get("/rounds/{round_id}/bundle")
    def rounds_bundle(round_id: str, request: Request):
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        try:
            bundle = store.round_bundle(round_id)
        except ValueError as exc:
            code = str(exc)
            status = 413 if code == "bundle_too_large" else 409
            return JSONResponse({"error": code}, status_code=status)
        if bundle is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        filename, chunks = bundle
        return StreamingResponse(
            chunks,
            media_type="application/zip",
            headers={"Content-Disposition": attachment_content_disposition(filename)},
        )

    @app.get("/rounds/{round_id}/files/{file_id}/versions/{version_id}/preview", response_model=wc.PreviewResponse)
    def rounds_preview(round_id: str, file_id: str, version_id: str, request: Request, sheet: str = Query("", max_length=100), start_row: int = Query(1, ge=1, le=1048576)):
        from product.preview import MAX_PREVIEW_BYTES, document_preview

        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            version = store.read_file_version(principal, file_id, version_id, round_id=round_id)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if version.byte_size > MAX_PREVIEW_BYTES:
            return JSONResponse({"available": False, "notice": "文件过大，请下载原件。"})
        try:
            return JSONResponse(document_preview(store.version_bytes(version), version.original_filename, sheet, start_row))
        except Exception:
            return JSONResponse({"available": False, "notice": "无法预览，请下载原件。"})

    @app.post("/rounds/{round_id}/resubmit", response_model=wc.Round, status_code=201, openapi_extra=wc.openapi_body(wc.ResubmitRequest))
    def rounds_resubmit(round_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        items = body.get("items") if "items" in body else None
        if "rule_version_id" in body:
            return JSONResponse({"error": "per_target_rules_required"}, status_code=400)
        try:
            created = store.resubmit_round(
                principal,
                round_id,
                cast(str, body.get("idempotency_key")),
                items,
                usage_policy=lambda actor: quota_for(request, actor),
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(created, status_code=201)

    @app.put("/rounds/{round_id}/reviews", response_model=wc.Round, openapi_extra=wc.openapi_body(wc.ReviewRequest))
    def rounds_review(round_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id, review=True)
        if denied:
            return denied
        try:
            updated = store.save_review(
                principal,
                round_id,
                str(body.get("item_key") or ""),
                cast(str, body.get("judgment")),
                cast(str, body.get("rationale")),
                cast(str, body.get("next_step")),
                cast(int, body.get("version")),
                body.get("citations"),
                mcp_operation_id=request.scope.get("ppap.mcp_operation_id"),
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(updated)
