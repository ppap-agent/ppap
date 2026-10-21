from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from ppap_application import build_product_store
from ppap_core.capabilities import report as capability_report
from ppap_core.db import postgres_ready
from ppap_core.feishu import (
    AppMismatch,
    AppTicket,
    CardAction,
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
from ppap_core.feishu_personal_upload import PersonalDriveClient, UploadRejected, UploadUnknown
from ppap_core.feishu_usage import (
    FeishuUsageError,
    query_tenant_ai_item,
    quota_payload,
    quota_unavailable,
    remaining_trial_points,
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
from ppap_core.identity import EDITION_COOKIE, SESSION_COOKIE, can_read, can_review, new_token, safe_return_to
from ppap_core.logs import QUIET_PATHS, bind, emit, new_request_id, reset, round_id_from_path
from ppap_core.logs import configure as configure_logs
from ppap_core.settings import Settings, load_settings
from ppap_core.storage import object_store_ready
from ppap_core.writeback_targets import public_target
from ppap_product_store import AuthStore

from ppap_api import web_contract as wc
from ppap_api.audit_routes import register_audit_routes
from ppap_api.notifications import run_task_notifications
from ppap_api.office_routes import register_office_routes
from ppap_api.report_routes import register_report_routes
from ppap_api.rule_routes import register_rule_routes
from ppap_api.sharing_routes import register_sharing_routes
from ppap_api.support_routes import register_support_routes
from ppap_api.usage_reporting import report_usage, run_pending_usage

Probe = Callable[[Settings], dict[str, str]]


def _cookie_kwargs(settings: Settings) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.profile == "production",
        "max_age": 43200,
        "path": "/",
    }


def _start_feishu_session(store, settings: Settings, info: dict, tokens):
    if not settings.tenant_allowed(str(info.get("tenant_key") or "")):
        return None
    principal = store.upsert_feishu_identity(info["tenant_key"], info["subject"], info["display_name"])
    return principal, store.create_session(principal, tokens)


def _edition_cookie_kwargs(settings: Settings) -> dict:
    return {
        "httponly": False,
        "samesite": "lax",
        "secure": settings.profile == "production",
        "max_age": 31536000,
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
    store = auth_store or build_product_store(settings)
    http = http or httpx.Client()

    @asynccontextmanager
    async def lifespan(_app):
        stop = threading.Event()
        notifier = None
        usage_reporter = None
        if settings.feishu_app_id and settings.feishu_app_secret:
            notifier = threading.Thread(target=run_task_notifications, args=(store, settings, stop), name="ppap-notifier", daemon=True)
            notifier.start()
        if settings.usage_charge_enabled and settings.feishu_app_id and settings.feishu_app_secret:
            usage_reporter = threading.Thread(target=run_pending_usage, args=(store, settings, stop), name="ppap-usage", daemon=True)
            usage_reporter.start()
        try:
            yield
        finally:
            stop.set()
            if notifier:
                await asyncio.to_thread(notifier.join, 1)
            if usage_reporter:
                await asyncio.to_thread(usage_reporter.join, 1)

    app = FastAPI(title="PPAP Product API", docs_url=None, redoc_url=None, lifespan=lifespan)
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
        request.state.authenticated_principal = None
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
            session = store.load_session(raw)
        elif session.provider_auth_dead(now):
            store.revoke_session(raw)
            return None
        if session:
            request.state.authenticated_principal = session.principal
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
            principal = getattr(request.state, "authenticated_principal", None)
            if principal:
                tokens.extend(bind(tenant_id=principal.tenant_id))
            if path not in QUIET_PATHS:
                level = logging.INFO if settings.profile == "production" or str(settings.LOG_FORMAT).strip().lower() == "json" else logging.DEBUG
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

    def require_file_version(request: Request, file_id: str, version_id: str):
        principal = principal_of(request)
        if not principal:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return store.read_file_version(principal, file_id, version_id), None
        except KeyError:
            return None, JSONResponse({"error": "access_denied"}, status_code=403)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"git_sha": settings.git_sha}

    @app.get("/public-config", response_model=wc.PublicConfig)
    def public_config() -> dict[str, object]:
        cfg = settings.public_config()
        cfg["login"] = "feishu" if settings.feishu_app_id and settings.feishu_app_secret else "disabled"
        cfg["personal_drive_upload"] = cfg["login"] == "feishu"
        return cfg

    @app.get("/capabilities")
    def capabilities() -> dict[str, dict[str, str]]:
        body = capability_report(settings)
        if body["feishu"]["state"] == "configured":
            try:
                body["feishu"].update(store.app_ticket_status(settings.feishu_app_id))
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
            if parsed.issued_at is None:
                return JSONResponse({"error": "invalid_ticket_timestamp"}, status_code=400)
            updated = store.save_app_ticket(settings.feishu_app_id, parsed.value, issued_at=parsed.issued_at)
            emit("feishu_app_ticket_received", updated=updated)
            return JSONResponse({"ok": True})
        if isinstance(parsed, CardAction):
            try:
                receipt = store.enqueue_task_confirmation(parsed)
                if (
                    not isinstance(receipt, dict)
                    or not isinstance(receipt.get("request_id"), str)
                    or not receipt["request_id"].strip()
                    or receipt.get("state") not in {"pending", "accepted", "rejected"}
                ):
                    raise RuntimeError("invalid_confirmation_receipt")
            except KeyError:
                return JSONResponse({"toast": {"type": "error", "content": "无法确认此卡片。请由任务创建人打开页面核对。"}})
            except ValueError as error:
                message = {
                    "rule_candidate_expired": "规则已更新，本次未受理。请打开页面重新识别并核对。",
                    "task_revision_conflict": "文件或审核模式已变化，本次未受理。请打开页面核对。",
                    "usage_quota_unavailable": "当前无法确认用量，未开始审核。请打开页面查看。",
                    "usage_ai_disabled": "飞书AI包开关已关闭，未开始审核。请联系管理员。",
                    "usage_balance_empty": "当前没有可用额度，未开始审核。请联系管理员。",
                }.get(str(error), "本次未受理，请打开页面核对当前任务。")
                return JSONResponse({"toast": {"type": "warning", "content": message}})
            except Exception:
                emit("card_confirmation_unavailable", level=logging.WARNING)
                return JSONResponse({"toast": {"type": "warning", "content": "受理结果尚未确认。请打开页面读取任务，不要重复新建审核。"}})
            if receipt["state"] == "pending":
                return JSONResponse({"toast": {"type": "info", "content": "已收到确认，正在核对。请打开审核查看结果。"}})
            if receipt["state"] == "rejected":
                reason = receipt.get("reason")
                safe_reasons = (
                    "规则已更新，请打开页面重新识别并核对。",
                    "文件或审核模式已变化，请打开页面核对。",
                    "当前无法确认用量，请打开页面核对后重新确认。",
                    "飞书AI包开关已关闭，请联系管理员。",
                    "当前没有可用额度，请联系管理员。",
                    "本次未受理，请由任务创建人打开页面核对权限和当前任务。",
                )
                return JSONResponse({"toast": {"type": "warning", "content": reason if reason in safe_reasons else "本次未受理，请打开页面核对当前任务。"}})
            return JSONResponse({"toast": {"type": "success", "content": "本任务已受理，请打开审核查看进度。"}})
        if isinstance(parsed, UnauthenticatedEvent):
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if isinstance(parsed, AppMismatch):
            return JSONResponse({"error": "app_mismatch"}, status_code=403)
        if isinstance(parsed, EncryptedEvent):
            return JSONResponse({"error": "encrypted_event_not_configured"}, status_code=400)
        return JSONResponse({"error": "ignored"}, status_code=400)

    @app.get("/login/feishu", response_model=None)
    def login_feishu(return_to: str = "", purpose: str = "") -> RedirectResponse | JSONResponse:
        if purpose not in ("", "report"):
            return JSONResponse({"error": "invalid_login_purpose"}, status_code=400)
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            return JSONResponse({"error": "feishu_disabled"}, status_code=409)
        state = new_token()
        store.save_state(state, safe_return_to(return_to, settings))
        return RedirectResponse(authorize_url(settings, state, report=purpose == "report"), status_code=302)

    @app.get("/login/feishu/callback")
    def login_feishu_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        dest = store.consume_state(state) if state else None
        fallback = settings.canonical_web_url() + "/"
        if not dest:
            return RedirectResponse(fallback, status_code=303)

        def failed(reason: str) -> RedirectResponse:
            parts = urlsplit(dest)
            query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "auth_error"]
            query.append(("auth_error", reason))
            return RedirectResponse(urlunsplit(parts._replace(query=urlencode(query))), status_code=303)

        if error or not code:
            return failed("cancelled" if error == "access_denied" else "failed")
        try:
            tokens = exchange_code(http, settings, code)
            info = user_info(http, tokens.access_token)
            started = _start_feishu_session(store, settings, info, tokens)
            if started is None:
                return failed("tenant_not_allowed")
            _principal, raw = started
        except FeishuError:
            return failed("failed")
        response = RedirectResponse(dest, status_code=303)
        response.set_cookie(SESSION_COOKIE, raw, **_cookie_kwargs(settings))
        return response

    @app.post("/login/feishu/host", response_model=wc.PrincipalPublic, openapi_extra=wc.openapi_body(wc.HostLoginRequest))
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
            started = _start_feishu_session(store, settings, info, tokens)
            if started is None:
                return JSONResponse({"error": "tenant_not_allowed"}, status_code=403)
            principal, raw = started
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

    def _enter_edition(request: Request, edition: str, return_to: str = "") -> RedirectResponse | JSONResponse:
        default = settings.canonical_web_url() + ("/workbench" if edition == "current" else "/")
        target = safe_return_to(return_to, settings)
        if not return_to.strip() or target != return_to or urlsplit(target).path in {"/enter-current", "/enter-legacy"}:
            target = default
        principal = principal_of(request)
        if principal is None:
            dest = settings.canonical_web_url() + "/enter-" + edition
            if return_to:
                dest += "?" + urlencode({"return_to": target})
            login = settings.public_api_url.rstrip("/") + "/login/feishu?return_to=" + quote(dest, safe="")
            return RedirectResponse(login, status_code=302)
        if not settings.tenant_allowed(principal.tenant_key):
            return JSONResponse({"error": "not_found"}, status_code=404)
        response = RedirectResponse(target, status_code=302)
        response.set_cookie(EDITION_COOKIE, edition, **_edition_cookie_kwargs(settings))
        return response

    @app.get("/enter-current", response_model=None)
    def enter_current(request: Request, return_to: str = "") -> RedirectResponse | JSONResponse:
        return _enter_edition(request, "current", return_to)

    @app.get("/enter-legacy", response_model=None)
    def enter_legacy(request: Request) -> RedirectResponse | JSONResponse:
        return _enter_edition(request, "legacy")

    @app.get("/me", response_model=wc.PrincipalPublic)
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
        return JSONResponse(
            {
                "id": resource.id,
                "tenant_id": resource.tenant_id,
                "kind": resource.kind,
                "review_allowed": can_review(principal, resource, grants),
            }
        )

    @app.post("/files", response_model=wc.FileVersion, status_code=201, openapi_extra=wc.openapi_body(wc.FileCreateRequest))
    def files_create(body: dict, request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if ("request_id" in body or "sha256" in body) and (not body.get("request_id") or not body.get("sha256")):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        try:
            version = store.create_staging(
                principal,
                str(body.get("original_filename") or ""),
                str(body.get("content_type") or "application/octet-stream"),
                int(body.get("byte_size") or 0),
                request_id=body.get("request_id"),
                expected_sha256=body.get("sha256"),
                feishu_folder_token=body.get("feishu_folder_token"),
            )
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except (ValueError, TypeError) as exc:
            code = "upload_request_conflict" if str(exc) == "upload_request_conflict" else "invalid_upload"
            return JSONResponse({"error": code}, status_code=409 if code == "upload_request_conflict" else 400)
        return JSONResponse(version.public(), status_code=201)

    @app.post("/files/{file_id}/versions", response_model=wc.FileVersion, status_code=201, openapi_extra=wc.openapi_body(wc.FileCreateRequest))
    def files_new_version(file_id: str, body: dict, request: Request) -> JSONResponse:
        principal, denied = require_file_read(request, file_id)
        if denied:
            return denied
        if ("request_id" in body or "sha256" in body) and (not body.get("request_id") or not body.get("sha256")):
            return JSONResponse({"error": "invalid_upload"}, status_code=400)
        try:
            version = store.create_staging(
                principal,
                str(body.get("original_filename") or ""),
                str(body.get("content_type") or "application/octet-stream"),
                int(body.get("byte_size") or 0),
                file_id=file_id,
                request_id=body.get("request_id"),
                expected_sha256=body.get("sha256"),
                feishu_folder_token=body.get("feishu_folder_token"),
            )
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except (ValueError, TypeError) as exc:
            code = "upload_request_conflict" if str(exc) == "upload_request_conflict" else "invalid_upload"
            return JSONResponse({"error": code}, status_code=409 if code == "upload_request_conflict" else 400)
        return JSONResponse(version.public(), status_code=201)

    @app.get("/file-uploads/{request_id}", response_model=wc.AuditUploadItem)
    def files_upload_get(request_id: str, request: Request) -> JSONResponse:
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            receipt = store.get_upload_request(principal, request_id)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError:
            return JSONResponse({"error": "invalid_upload_request"}, status_code=400)
        return JSONResponse(receipt)

    @app.put("/files/{file_id}/versions/{version_id}/blob", response_model=wc.FileVersion)
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
        except ValueError as exc:
            code = "upload_content_conflict" if str(exc) == "upload_content_conflict" else "invalid_upload"
            return JSONResponse({"error": code}, status_code=409 if code == "upload_content_conflict" else 400)
        return JSONResponse(updated.public())

    @app.post("/files/{file_id}/versions/{version_id}/finalize", response_model=wc.FileVersion, openapi_extra=wc.openapi_body(wc.FileFinalizeRequest, required=False))
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
        return JSONResponse(version_payload(ready))

    @app.get("/files/{file_id}/versions/{version_id}", response_model=wc.FileVersion)
    def files_version_get(file_id: str, version_id: str, request: Request) -> JSONResponse:
        version, denied = require_file_version(request, file_id, version_id)
        if denied:
            return denied
        return JSONResponse(version_payload(version))

    @app.get("/files/{file_id}/versions/{version_id}/download")
    def files_download(file_id: str, version_id: str, request: Request):
        version, denied = require_file_version(request, file_id, version_id)
        if denied:
            return denied
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

    @app.get("/files/{file_id}/versions/{version_id}/preview", response_model=wc.PreviewResponse)
    def files_preview(file_id: str, version_id: str, request: Request, sheet: str = Query("", max_length=100), start_row: int = Query(1, ge=1, le=1048576)):
        from ppap_core.preview import MAX_PREVIEW_BYTES, document_preview

        version, denied = require_file_version(request, file_id, version_id)
        if denied:
            return denied
        if version.byte_size > MAX_PREVIEW_BYTES:
            return JSONResponse({"available": False, "notice": "文件过大，请下载原件。"})
        try:
            return JSONResponse(document_preview(store.version_bytes(version), version.original_filename, sheet, start_row))
        except Exception:
            return JSONResponse({"available": False, "notice": "无法预览，请下载原件。"})

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
        ready = store.finalize_version(version)
        token = str(getattr(imported, "file_token", "") or "").strip()
        kind = str(getattr(imported, "doc_type", "") or "").strip()
        if token:
            store.save_file_source(ready.id, kind or "file", token)
        return ready

    def version_payload(version):
        body = version.public()
        body["origin"] = public_target(store.file_source(version.id))
        return body

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

    @app.get("/integrations/feishu/files", response_model=wc.DriveFilesList)
    def feishu_files_list(request: Request, folder_token: str = "", page_token: str = ""):
        from ppap_core.feishu_drive import list_drive_folder

        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        try:
            return JSONResponse(list_drive_folder(http, session.access_token, folder_token, page_token))
        except ValueError:
            return JSONResponse({"error": "invalid_folder_query"}, status_code=400)
        except FeishuMetaError:
            return JSONResponse({"error": "feishu_folder_failed"}, status_code=502)

    @app.get("/integrations/feishu/root-folder", response_model=wc.DriveFile)
    def feishu_root_folder(request: Request):
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        try:
            return PersonalDriveClient(http, session.access_token).root_folder()
        except (UploadUnknown, UploadRejected):
            return JSONResponse({"error": "feishu_folder_failed"}, status_code=502)

    @app.post("/file-uploads/{request_id}/feishu", response_model=wc.AuditUploadItem)
    def files_personal_upload(request_id: str, request: Request):
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        if not session.access_token:
            return JSONResponse({"error": "feishu_user_token_missing"}, status_code=409)
        try:
            return store.upload_to_personal_drive(session.principal, request_id, PersonalDriveClient(http, session.access_token))
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            conflict = str(exc) == "upload_request_conflict"
            return JSONResponse({"error": "upload_request_conflict" if conflict else "invalid_upload_request"}, status_code=409 if conflict else 400)

    @app.post("/integrations/feishu/files/import", response_model=wc.FileVersion, status_code=201, openapi_extra=wc.openapi_body(wc.FeishuFileImportRequest))
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
            if "request_id" in body:
                ready = store.receive_drive_upload(session.principal, body["request_id"], file_token, doc_type, lambda: import_drive_material(http, session.access_token, file_token, doc_type))
            else:
                imported = import_drive_material(http, session.access_token, file_token, doc_type)
                ready = persist_imported(session.principal, imported)
        except Exception as exc:
            if isinstance(exc, ValueError) and str(exc) in {"upload_request_conflict", "upload_content_conflict"}:
                return JSONResponse({"error": str(exc)}, status_code=409)
            mapped = feishu_material_error(exc)
            if mapped:
                return mapped
            raise
        return JSONResponse(version_payload(ready), status_code=201)

    @app.get("/integrations/feishu/docs/links", response_model=wc.DocLinksResponse)
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

    @app.get("/integrations/feishu/usage/quota", response_model=wc.QuotaEstimate)
    def feishu_usage_quota(request: Request, ai_item_id: str = "", target_count: int | None = Query(None, ge=0, le=200)) -> JSONResponse:
        from ppap_core.feishu_usage import estimate_audit

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
        partner = None
        try:
            partner = query_tenant_ai_item(http, session.access_token, item_id)
        except FeishuUsageError:
            partner = None
        if target_count is None:
            if partner is None:
                body = quota_unavailable("检查额度异常", "检查AI包额度时发生异常")
                body["remaining_trial_ai_points"] = remaining
                body["usage_charge_enabled"] = settings.usage_charge_enabled
                return JSONResponse(body)
            body = quota_payload(partner, remaining)
            body["usage_charge_enabled"] = settings.usage_charge_enabled
            return JSONResponse(body)
        return JSONResponse(estimate_audit(target_count, remaining, partner, settings.usage_charge_enabled))

    @app.post("/integrations/feishu/usage/events/{event_id}/report")
    def feishu_usage_report(event_id: str, request: Request) -> JSONResponse:
        session = session_of(request)
        if not session:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        body, status = report_usage(store, http, settings, session, event_id)
        return JSONResponse(body, status_code=status)

    register_report_routes(app, store, session_of, http, settings)
    register_office_routes(app, store, principal_of, settings)

    @app.post("/integrations/feishu/rounds/{round_id}/writeback", response_model=wc.WritebackResponse)
    def feishu_round_writeback(round_id: str, request: Request) -> JSONResponse:
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
        stored = store.round_writeback(round_id) or {}
        file_token = str(stored.get("object_ref") or "").strip()
        if not file_token:
            return JSONResponse({"error": "writeback_target_missing"}, status_code=409)
        return_url = round_return_url(settings.canonical_web_url(), round_body["id"])
        try:
            result = write_round_history(http, session.access_token, file_token, round_body, return_url)
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

    def quota_for(request, principal):
        session = session_of(request)
        if not session or session.principal != principal or not session.access_token:
            raise FeishuUsageError("feishu_user_token_missing")
        return query_tenant_ai_item(http, session.access_token, settings.feishu_ai_item_id)

    register_audit_routes(app, store, principal_of, quota_for)
    register_rule_routes(app, store, principal_of, require_file_read)
    register_support_routes(app, principal_of, settings)
    register_sharing_routes(app, store, session_of, http, settings)
    wc.add_ui_contract(app)
    return app


app = create_app()
