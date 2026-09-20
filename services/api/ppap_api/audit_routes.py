from __future__ import annotations

from fastapi import FastAPI, Request
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
        rule = body.get("rule_version_id") if "rule_version_id" in body else None
        try:
            draft = store.save_draft(principal, draft_id, str(rule) if rule else None, items)
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(draft)

    @app.post("/drafts/{draft_id}/accept")
    def drafts_accept(draft_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, draft_id)
        if denied:
            return denied
        try:
            round_body = store.accept_draft(principal, draft_id, body.get("idempotency_key"))
        except (KeyError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(round_body, status_code=201)

    @app.get("/rounds")
    def rounds_list(request: Request, limit: int = 50) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse({"rounds": store.list_rounds(principal, limit)})

    @app.get("/usage")
    def usage_list(request: Request, limit: int = 50) -> JSONResponse:
        principal, denied = require_principal(request)
        if denied:
            return denied
        return JSONResponse({"events": store.list_usage_events(principal, limit)})

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

    @app.post("/rounds/{round_id}/resubmit")
    def rounds_resubmit(round_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_object(request, round_id)
        if denied:
            return denied
        items = body.get("items") if "items" in body else None
        rule = body.get("rule_version_id") if "rule_version_id" in body else None
        try:
            created = store.resubmit_round(
                principal,
                round_id,
                body.get("idempotency_key"),
                str(rule) if rule else None,
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
