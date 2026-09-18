from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

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
    accept_event,
    authorize_url,
    exchange_code,
    refresh_user_token,
    user_info,
)
from ppap_core.files import MAX_UPLOAD_BYTES, ObjectExists, ObjectMissing, UnsafeSource, fetch_remote, original_filename
from ppap_core.fixtures import SUBJECTS
from ppap_core.identity import SESSION_COOKIE, can_read, can_review, new_token, safe_return_to
from ppap_core.settings import Settings, load_settings
from ppap_core.storage import object_store_ready
from ppap_api.store import AuthStore

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
        expose_headers=["Content-Disposition", "X-Content-SHA256"],
    )

    def principal_of(request: Request):
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
            return session.principal
        if session.provider_auth_dead(now):
            store.revoke_session(raw)
            return None
        return session.principal

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
        filename = original_filename(version.original_filename)
        return Response(
            content=data,
            media_type=version.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
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

    return app


app = create_app()
