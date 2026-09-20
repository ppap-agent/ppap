from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response

from ppap_core.capabilities import report as capability_report
from ppap_core.db import postgres_ready
from ppap_core.feishu import (
    AppMismatch,
    AppTicket,
    Challenge,
    EncryptedEvent,
    FeishuError,
    UnauthenticatedEvent,
    UserTokens,
    accept_event,
    authorize_url,
    exchange_code,
    exchange_host_code,
    refresh_user_token,
    user_info,
)
from ppap_core.feishu_drive import (
    FeishuDocLinksError,
    FeishuExportError,
    FeishuMetaError,
    PermanentFailure,
    UnsupportedDocType,
    import_drive_material,
    list_doc_links,
)
from ppap_core.feishu_usage import (
    FeishuUsageError,
    quota_payload,
    quota_unavailable,
    query_tenant_ai_item,
    remaining_trial_points,
    apply_report_credential,
    report_usage_record,
    tenant_access_token,
)
from ppap_core.feishu_writeback import (
    FeishuWritebackError,
    round_return_url,
    write_round_history,
)
from ppap_core.files import (
    MAX_UPLOAD_BYTES,
    ObjectExists,
    ObjectMissing,
    UnsafeSource,
    attachment_content_disposition,
    fetch_remote,
    original_filename,
    safe_content_type,
)
from ppap_core.fixtures import SUBJECTS
from ppap_core.identity import SESSION_COOKIE, can_read, can_review, new_token, safe_return_to
from ppap_core.logs import QUIET_PATHS, bind, configure as configure_logs, emit, new_request_id, reset, round_id_from_path
from ppap_core.settings import Settings, load_settings
from ppap_core.storage import object_store_ready
from ppap_api.store import AuthStore
from ppap_api.audit_routes import register_audit_routes
from ppap_api.rule_routes import register_rule_routes

Probe = Callable[[Settings], dict[str, str]]


def _cookie_kwargs(settings: Settings) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.profile == "production",
        "max_age": 43200,
        "path": "/",
    }


def create_app(
    settings: Settings | None = None,
    postgres_probe: Probe | None = None,
    object_store_probe: Probe | None = None,
    auth_store: AuthStore | None = None,
    http: httpx.Client | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    postgres_probe = postgres_probe or postgres_ready
    object_store_probe = object_store_probe or object_store_ready
    store = auth_store or AuthStore(settings)
    http = http or httpx.Client()
    app = FastAPI(title="PPAP Product API", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Accept", "Content-Type"],
        expose_headers=["Content-Disposition", "X-Content-SHA256", "X-Request-ID"],
    )
    configure_logs(settings)

    def session_of(request: Request):
        raw = request.cookies.get(SESSION_COOKIE, "")
        if not raw:
            return None
        session = store.load_session(raw)
        if not session:
            return None
        now = datetime.now(timezone.utc)
        if session.needs_refresh(now):
            try:
                tokens = refresh_user_token(http, settings, session.refresh_token)
            except FeishuError:
                store.revoke_session(raw)
                return None
            store.update_provider_auth(raw, tokens)
            return store.load_session(raw)
        if session.provider_auth_dead(now):
            store.revoke_session(raw)
            return None
        return session

    def principal_of(request: Request):
        session = session_of(request)
        return session.principal if session else None

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        request_id = new_request_id(request.headers.get("x-request-id", ""))
        path = request.url.path
        tokens = bind(request_id=request_id, round_id=round_id_from_path(path))
        started = time.perf_counter()
        try:
            response = await call_next(request)
            principal = principal_of(request)
            if principal:
                tokens.extend(bind(tenant_id=principal.tenant_id))
            if path not in QUIET_PATHS:
                level = (
                    logging.INFO
                    if settings.profile == "production" or str(settings.LOG_FORMAT).strip().lower() == "json"
                    else logging.DEBUG
                )
                emit(
                    "http_request",
                    level=level,
                    method=request.method,
                    path=path,
                    status=response.status_code,
                    ms=int((time.perf_counter() - started) * 1000),
                )
            response.headers["x-request-id"] = request_id
            return response
        finally:
            reset(tokens)

    def require_file_read(request: Request, file_id: str):
        principal = principal_of(request)
        if not principal:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        resource = store.resource(file_id)
        grants = store.grants(file_id, principal.user_id) if resource else set()
        if resource is None or resource.kind != "file" or not can_read(principal, resource, grants):
            return principal, JSONResponse({"error": "access_denied"}, status_code=403)
        return principal, None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"git_sha": settings.git_sha}

    @app.get("/public-config")
    def public_config() -> dict[str, str]:
        cfg = settings.public_config()
        cfg["login"] = "feishu" if settings.feishu_app_id and settings.feishu_app_secret else "disabled"
        return cfg

    @app.get("/capabilities")
    def capabilities() -> dict[str, dict[str, str]]:
        body = capability_report(settings)
        if body["feishu"]["state"] == "configured":
            try:
                body["feishu"]["app_ticket"] = "received" if store.has_app_ticket(settings.feishu_app_id) else "missing"
            except Exception:
                body["feishu"]["app_ticket"] = "unproven"
        return body

    @app.get("/ready")
    def ready() -> JSONResponse:
        postgres = postgres_probe(settings)
        objects = object_store_probe(settings)
        ok = postgres.get("state") == "ok" and objects.get("state") == "ok"
        body = {
            "status": "ready" if ok else "not_ready",
            "git_sha": settings.git_sha,
            "postgres": postgres,
            "object_storage": objects,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.post("/integrations/feishu/events")
    def feishu_events(body: dict) -> JSONResponse:
        parsed = accept_event(settings, body)
        if isinstance(parsed, Challenge):
            return JSONResponse({"challenge": parsed.value})
        if isinstance(parsed, AppTicket):
            store.save_app_ticket(settings.feishu_app_id, parsed.value)
            return JSONResponse({"ok": True})
        if isinstance(parsed, UnauthenticatedEvent):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if isinstance(parsed, AppMismatch):
            return JSONResponse({"error": "app_mismatch"}, status_code=403)
        if isinstance(parsed, EncryptedEvent):
            return JSONResponse({"error": "encrypted_event_not_configured"}, status_code=400)
        return JSONResponse({"error": "ignored"}, status_code=400)

    @app.get("/login/feishu", response_model=None)
    def login_feishu(return_to: str = "") -> RedirectResponse | JSONResponse:
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return JSONResponse({"error": "feishu_disabled"}, status_code=409)
        state = new_token()
        store.save_state(state, safe_return_to(return_to, settings))
        return RedirectResponse(authorize_url(settings, state), status_code=302)

    @app.get("/login/feishu/callback")
    def login_feishu_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        dest = store.consume_state(state) if state else None
        fallback = settings.canonical_web_url() + "/"
        if error or not code or not dest:
            return RedirectResponse(fallback, status_code=303)
        try:
            tokens = exchange_code(http, settings, code)
            info = user_info(http, tokens.access_token)
            principal = store.upsert_feishu_identity(info["tenant_key"], info["subject"], info["display_name"])
            raw = store.create_session(principal, tokens)
        except FeishuError:
            return RedirectResponse(fallback, status_code=303)
        response = RedirectResponse(dest, status_code=303)
        response.set_cookie(SESSION_COOKIE, raw, **_cookie_kwargs(settings))
        return response

    @app.post("/login/feishu/host")
    def login_feishu_host(body: dict) -> JSONResponse:
        code = str(body.get("code") or "").strip()
        if not code:
            return JSONResponse({"error": "invalid_host_code"}, status_code=400)
        if code.startswith("isolated-dev:"):
            if settings.profile != "isolated-dev":
                return JSONResponse({"error": "invalid_host_code"}, status_code=400)
            name = code.split(":", 1)[1]
            if name not in SUBJECTS:
                return JSONResponse({"error": "unknown_fixture"}, status_code=400)
            store.seed_isolated_dev()
            principal = store.principal_for_fixture(name)
            now = datetime.now(timezone.utc)
            tokens = UserTokens(
                "user-access",
                "user-refresh",
                now + timedelta(hours=1),
                now + timedelta(days=7),
            )
            raw = store.create_session(principal, tokens)
            response = JSONResponse(principal.public())
            response.set_cookie(SESSION_COOKIE, raw, **_cookie_kwargs(settings))
            return response
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return JSONResponse({"error": "feishu_disabled"}, status_code=409)
        redirect_uri = str(body.get("redirect_uri") or "").strip() or None
        try:
            tokens = exchange_host_code(http, settings, code, redirect_uri)
            info = user_info(http, tokens.access_token)
            principal = store.upsert_feishu_identity(info["tenant_key"], info["subject"], info["display_name"])
            raw = store.create_session(principal, tokens)
        except FeishuError:
            return JSONResponse({"error": "feishu_host_login_failed"}, status_code=502)
        response = JSONResponse(principal.public())
        response.set_cookie(SESSION_COOKIE, raw, **_cookie_kwargs(settings))
        return response

    @app.post("/logout")
    def logout(request: Request) -> JSONResponse:
        raw = request.cookies.get(SESSION_COOKIE, "")
        if raw:
            store.revoke_session(raw)
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/me")
    def me(request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(principal.public())

    @app.get("/resources/{resource_id}")
    def resource_get(resource_id: str, request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        resource = store.resource(resource_id)
        grants = store.grants(resource_id, principal.user_id) if resource else set()
        if resource is None or not can_read(principal, resource, grants):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse({
            "id": resource.id,
            "tenant_id": resource.tenant_id,
            "kind": resource.kind,
            "review_allowed": can_review(principal, resource, grants),
        })

    @app.post("/files")
    def files_create(body: dict, request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            version = store.create_staging(
                principal,
                str(body.get("original_filename") or ""),
                str(body.get("content_type") or "application/octet-stream"),
                int(body.get("byte_size") or 0),
            )
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return JSONResponse(version.public(), status_code=201)

    @app.post("/files/{file_id}/versions")
    def files_new_version(file_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        try:
            version = store.create_staging(
                principal,
                str(body.get("original_filename") or ""),
                str(body.get("content_type") or "application/octet-stream"),
                int(body.get("byte_size") or 0),
                file_id=file_id,
            )
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return JSONResponse(version.public(), status_code=201)

    @app.put("/files/{file_id}/versions/{version_id}/blob")
    async def files_blob(file_id: str, version_id: str, request: Request):
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        version = store.get_version(file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                return JSONResponse({"error": "too_large"}, status_code=413)
            chunks.append(chunk)
        try:
            updated = store.put_blob(version, b"".join(chunks))
        except ObjectExists:
            return JSONResponse({"error": "object_exists"}, status_code=409)
        except ValueError:
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return JSONResponse(updated.public())

    @app.post("/files/{file_id}/versions/{version_id}/finalize")
    async def files_finalize(file_id: str, version_id: str, request: Request) -> JSONResponse:
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        version = store.get_version(file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        raw = await request.body()
        payload = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                payload = parsed
        expected = payload.get("sha256")
        expected = str(expected) if expected else None
        try:
            ready = store.finalize_version(version, expected)
        except ObjectMissing:
            return JSONResponse({"error": "missing_object"}, status_code=409)
        except ValueError as exc:
            code = str(exc)
            status = 409 if code in {"sha256_mismatch", "sha256_conflict", "finalize_conflict", "not_staging", "missing_object"} else 400
            return JSONResponse({"error": code}, status_code=status)
        return JSONResponse(ready.public())

    @app.get("/files/{file_id}/versions/{version_id}")
    def files_version_get(file_id: str, version_id: str, request: Request) -> JSONResponse:
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        version = store.get_version(file_id, version_id)
        if version is None or version.tenant_id != principal.tenant_id:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return JSONResponse(version.public())

    @app.get("/files/{file_id}/versions/{version_id}/download")
    def files_download(file_id: str, version_id: str, request: Request):
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        version = store.get_version(file_id, version_id)
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

    @app.post("/files/from-url")
    def files_from_url(body: dict, request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        url = str(body.get("url") or "")
        try:
            data, content_type, name = fetch_remote(url, http, MAX_UPLOAD_BYTES)
            version = store.create_staging(principal, name, content_type, len(data))
            store.put_blob(version, data)
            ready = store.finalize_version(version)
        except UnsafeSource as exc:
            return JSONResponse({"error": str(exc) or "unsafe_source"}, status_code=400)
        except (ValueError, ObjectExists, ObjectMissing):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        return JSONResponse(ready.public(), status_code=201)

    def persist_imported(principal, imported):
        version = store.create_staging(principal, imported.filename, imported.content_type, len(imported.data))
        store.put_blob(version, imported.data)
        return store.finalize_version(version)

    def feishu_material_error(exc: Exception) -> JSONResponse | None:
        if isinstance(exc, UnsupportedDocType):
            return JSONResponse({"error": "unsupported_doc_type"}, status_code=400)
        if isinstance(exc, FeishuMetaError):
            return JSONResponse({"error": "feishu_meta_failed"}, status_code=502)
        if isinstance(exc, PermanentFailure):
            return JSONResponse({"error": "feishu_download_failed", "message": str(exc)}, status_code=502)
        if isinstance(exc, FeishuExportError):
            return JSONResponse({"error": "feishu_export_failed"}, status_code=502)
        if isinstance(exc, ValueError):
            code = str(exc)
            if code == "too_large":
                return JSONResponse({"error": "too_large"}, status_code=413)
            if code == "invalid_file_token":
                return JSONResponse({"error": "invalid_file_token"}, status_code=400)
            if code in {
                "invalid_idempotency_key",
                "rule_required",
                "unknown_rule",
                "target_required",
                "invalid_role",
                "invalid_compare_file_tokens",
            }:
                return JSONResponse({"error": code}, status_code=400)
            if code in {"idempotency_conflict", "draft_already_accepted"}:
                return JSONResponse({"error": code}, status_code=409)
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        if isinstance(exc, (ObjectExists, ObjectMissing)):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        if isinstance(exc, KeyError):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        return None

    @app.post("/integrations/feishu/files/import")
    def feishu_files_import(body: dict, request: Request) -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        file_token = str(body.get("file_token") or "").strip()
        doc_type = str(body.get("doc_type") or "").strip() or None
        if not file_token:
            return JSONResponse({"error": "invalid_file_token"}, status_code=400)
        try:
            imported = import_drive_material(http, session.access_token, file_token, doc_type)
            ready = persist_imported(session.principal, imported)
        except Exception as exc:
            mapped = feishu_material_error(exc)
            if mapped:
                return mapped
            raise
        return JSONResponse(ready.public(), status_code=201)

    @app.get("/integrations/feishu/docs/links")
    def feishu_doc_links(request: Request, file_token: str = "") -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        try:
            links = list_doc_links(http, session.access_token, file_token)
        except ValueError as exc:
            if str(exc) == "invalid_file_token":
                return JSONResponse({"error": "invalid_file_token"}, status_code=400)
            raise
        except FeishuDocLinksError:
            return JSONResponse({"error": "feishu_doc_links_failed"}, status_code=502)
        return JSONResponse(
            {
                "doc_links": [
                    {
                        "token": item["token"],
                        "name": item["name"],
                        "doc_type": item["doc_type"],
                        "type": item["doc_type"],
                        "url": item["url"],
                    }
                    for item in links
                ]
            }
        )

    @app.get("/integrations/feishu/usage/quota")
    def feishu_usage_quota(request: Request, ai_item_id: str = "") -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        item_id = (ai_item_id or settings.feishu_ai_item_id or "").strip()
        if not item_id:
            return JSONResponse({"error": "feishu_ai_item_missing"}, status_code=409)
        consumed = store.trial_consumed_points(session.principal.tenant_id, item_id)
        remaining = remaining_trial_points(settings.feishu_free_trial_quota_points, consumed)
        try:
            item = query_tenant_ai_item(http, session.access_token, item_id)
        except FeishuUsageError:
            body = quota_unavailable("检查额度异常", "检查AI包额度时发生异常")
            body["remaining_trial_ai_points"] = remaining
            return JSONResponse(body)
        payload = quota_payload(item, remaining)
        return JSONResponse(payload)

    @app.post("/integrations/feishu/usage/events/{event_id}/report")
    def feishu_usage_report(event_id: str, request: Request) -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        row = store.usage_event(session.principal, event_id)
        if row is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        public = store.usage_event_public(row)
        if row["is_free_trial"]:
            public["skipped"] = True
            public["skip_reason"] = "消费在赠送额度内"
            return JSONResponse(public)
        if public["external_status"] == "confirmed":
            public["skipped"] = False
            return JSONResponse(public)
        if public["external_status"] != "pending":
            return JSONResponse(public, status_code=409)
        if not row.get("trace_id"):
            return JSONResponse({"error": "usage_trace_missing"}, status_code=409)
        ticket = store.app_ticket(settings.feishu_app_id)
        if not ticket:
            return JSONResponse({"error": "feishu_app_ticket_missing"}, status_code=409)
        # Preparation cannot report a charge. Failure leaves the event pending.
        try:
            tenant_token = tenant_access_token(
                http, settings.feishu_app_id, settings.feishu_app_secret, ticket,
                session.principal.tenant_key,
            )
            report_token = apply_report_credential(
                http, session.access_token, row["ai_item_id"], row["trace_id"]
            )
        except FeishuUsageError:
            return JSONResponse({"error": "feishu_usage_prepare_failed"}, status_code=502)
        claim = store.claim_usage_report(session.principal, event_id)
        if claim is None:
            current = store.usage_event(session.principal, event_id)
            return JSONResponse(store.usage_event_public(current), status_code=409)
        try:
            record_id = report_usage_record(
                http, tenant_token, report_token, row["ai_item_id"],
                row["trace_id"], row["consumed_points"],
            )
        except FeishuUsageError:
            store.settle_usage_external(session.principal, event_id, claim, "unknown")
            return JSONResponse({"error": "feishu_usage_report_unknown"}, status_code=502)
        settled = store.settle_usage_external(
            session.principal, event_id, claim, "confirmed", record_id
        )
        body = store.usage_event_public(settled)
        body["skipped"] = False
        return JSONResponse(body)

    @app.post("/integrations/feishu/rounds/{round_id}/writeback")
    def feishu_round_writeback(round_id: str, body: dict, request: Request) -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        resource = store.resource(round_id)
        grants = store.grants(round_id, session.principal.user_id) if resource else set()
        if resource is None or not can_read(session.principal, resource, grants):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        round_body = store.get_round(round_id)
        if round_body is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        file_token = str(body.get("file_token") or "").strip()
        return_url = round_return_url(settings.canonical_web_url(), round_body["id"])
        try:
            result = write_round_history(
                http, session.access_token, file_token, round_body, return_url
            )
        except FeishuWritebackError as exc:
            code = str(exc)
            if code == "invalid_file_token":
                return JSONResponse({"error": code}, status_code=400)
            if code in {"round_incomplete", "round_has_errors"}:
                return JSONResponse({"error": code}, status_code=409)
            return JSONResponse({"error": "feishu_writeback_failed"}, status_code=502)
        result["round_id"] = round_body["id"]
        result["return_url"] = return_url
        return JSONResponse(result)

    @app.post("/isolated-dev/reap-staging")
    def isolated_dev_reap(body: dict, request: Request) -> JSONResponse:
        if settings.profile != "isolated-dev":
            return JSONResponse({"error": "not_found"}, status_code=404)
        if not principal_of(request):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            max_age = int(body.get("max_age_seconds", 86400))
            reaped = store.reap_staging(max_age)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_age"}, status_code=400)
        return JSONResponse({"reaped": reaped})

    @app.post("/isolated-dev/sessions")
    def isolated_dev_session(body: dict) -> JSONResponse:
        if settings.profile != "isolated-dev":
            return JSONResponse({"error": "not_found"}, status_code=404)
        name = str(body.get("subject") or "")
        if name not in SUBJECTS:
            return JSONResponse({"error": "unknown_fixture"}, status_code=400)
        store.seed_isolated_dev()
        principal = store.principal_for_fixture(name)
        raw = store.create_session(principal)
        response = JSONResponse(principal.public())
        response.set_cookie(SESSION_COOKIE, raw, **_cookie_kwargs(settings))
        return response

    register_audit_routes(app, store, principal_of)
    register_rule_routes(app, store, principal_of)
    return app


app = create_app()
