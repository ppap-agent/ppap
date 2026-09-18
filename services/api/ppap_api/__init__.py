from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from ppap_core.capabilities import report as capability_report
from ppap_core.db import postgres_ready
from ppap_core.feishu import AppTicket, Challenge, EncryptedEvent, FeishuError, authorize_url, exchange_code, parse_event, user_info
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
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
    )

    def principal_of(request: Request):
        raw = request.cookies.get(SESSION_COOKIE, "")
        if not raw:
            return None
        return store.principal_from_token(raw)

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
        parsed = parse_event(body)
        if isinstance(parsed, Challenge):
            return JSONResponse({"challenge": parsed.value})
        if isinstance(parsed, AppTicket) and settings.feishu_app_id:
            store.save_app_ticket(settings.feishu_app_id, parsed.value)
            return JSONResponse({"ok": True})
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
        fallback = settings.web_origins()[0] + "/"
        if error or not code or not dest:
            return RedirectResponse(fallback, status_code=303)
        try:
            token = exchange_code(http, settings, code)
            info = user_info(http, token)
            principal = store.upsert_feishu_identity(info["tenant_key"], info["subject"], info["display_name"])
            raw = store.create_session(principal)
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
