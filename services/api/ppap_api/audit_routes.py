from __future__ import annotations

from datetime import date
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response

from ppap_core.files import ObjectMissing, attachment_content_disposition, original_filename, safe_content_type
from ppap_core.identity import can_read, can_review
from ppap_api.store import AuthStore


def register_audit_routes(app: FastAPI, store: AuthStore, principal_of) -> None:
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
        status = 409 if code in {
            "idempotency_conflict",
            "draft_already_accepted",
            "file_not_ready",
            "review_conflict",
        } else 400
        return JSONResponse({"error": code}, status_code=status)

    @app.get("/rules")
    def rules_list(request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse({"rules": store.list_rules(principal.tenant_id)})

    @app.post("/drafts")
    def drafts_create(request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse(store.create_draft(principal), status_code=201)

    @app.post("/file-analyses")
    def files_analyze(body: dict, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            result = store.accept_file_analysis(principal, body.get("request_id"), body.get("source_file_id"), body.get("source_file_version_id"))
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(result, status_code=202)

    @app.get("/file-analyses/{analysis_id}")
    def files_analysis_get(analysis_id: str, request: Request) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse(store.get_file_analysis(principal, analysis_id))
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/files/{file_id}/versions/{version_id}/analysis")
    def files_latest_analysis(file_id: str, version_id: str, request: Request):
        principal, denied = require_principal(request)
        if denied:
            return denied
        try:
            return JSONResponse({"analysis": store.latest_file_analysis(principal, file_id, version_id)})
        except (KeyError, ValueError) as exc:
            return fail(exc)

    @app.get("/drafts/{draft_id}")
    def drafts_get(draft_id: str, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        draft = store.get_draft(draft_id)
        if draft is None or store.resource(draft_id).tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(draft)

    @app.put("/drafts/{draft_id}")
    def drafts_save(draft_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        items = body.get("items") if "items" in body else None
        if "rule_version_id" in body:
            return JSONResponse({"error": "per_target_rules_required"}, status_code=400)
        try:
            draft = store.save_draft(principal, draft_id, items)
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(draft)

    @app.post("/drafts/{draft_id}/accept")
    def drafts_accept(draft_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        try:
            round_body = store.accept_draft(principal, draft_id, body.get("idempotency_key"), body.get("predecessor_id"))
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(round_body, status_code=201)

    @app.get("/rounds")
    def rounds_list(request: Request, limit: int = Query(50, ge=1, le=50), offset: int = Query(0, ge=0),
                    q: str = Query("", max_length=200), execution: str = "") -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if execution not in {"", "queued", "running", "completed", "failed"}:
            return JSONResponse({"error": "invalid_execution"}, status_code=400)
        rows = store.list_rounds(principal, limit + 1, offset, q, execution)
        return JSONResponse({"rounds": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})

    @app.get("/usage")
    def usage_list(request: Request, limit: int = Query(50, ge=1, le=50),
                   offset: int = Query(0, ge=0), since: date | None = None,
                   until: date | None = None) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        if since and until and since > until:
            return JSONResponse({"error": "invalid_date_range"}, status_code=400)
        rows = store.list_usage_events(principal, limit + 1, offset, since, until)
        return JSONResponse({"events": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None})

    @app.get("/rounds/{round_id}")
    def rounds_get(round_id: str, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        body = store.get_round(round_id)
        if body is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(body)

    @app.get("/rounds/{round_id}/files/{file_id}/versions/{version_id}/download")
    def rounds_download(round_id: str, file_id: str, version_id: str, request: Request):
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        version = store.round_input_version(round_id, file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
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

    @app.get("/rounds/{round_id}/files/{file_id}/versions/{version_id}/preview")
    def rounds_preview(round_id: str, file_id: str, version_id: str, request: Request):
        from ppap_core.preview import MAX_PREVIEW_BYTES, document_preview
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        version = store.round_input_version(round_id, file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if version.byte_size > MAX_PREVIEW_BYTES:
            return JSONResponse({"available": False, "notice": "文件过大，请下载原件。"})
        try:
            return JSONResponse(document_preview(store.version_bytes(version), version.original_filename))
        except Exception:
            return JSONResponse({"available": False, "notice": "无法预览，请下载原件。"})

    @app.post("/rounds/{round_id}/resubmit")
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
                body.get("idempotency_key"),
                items,
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(created, status_code=201)

    @app.put("/rounds/{round_id}/reviews")
    def rounds_review(round_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id, review=True)
        if denied:
            return denied
        try:
            updated = store.save_review(
                principal,
                round_id,
                str(body.get("item_key") or ""),
                body.get("judgment"),
                body.get("rationale"),
                body.get("next_step"),
                body.get("version"),
            )
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(updated)
