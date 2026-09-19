from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ppap_core.capabilities import report as capability_report
from ppap_core.db import postgres_ready
from ppap_core.settings import Settings, load_settings
from ppap_core.storage import object_store_ready

Probe = Callable[[Settings], dict[str, str]]


def create_app(
    settings: Settings | None = None,
    postgres_probe: Probe | None = None,
    object_store_probe: Probe | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    postgres_probe = postgres_probe or postgres_ready
    object_store_probe = object_store_probe or object_store_ready
    app = FastAPI(title="PPAP Product API", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins(),
        allow_methods=["GET"],
        allow_headers=["Accept"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"git_sha": settings.git_sha}

    @app.get("/public-config")
    def public_config() -> dict[str, str]:
        return settings.public_config()

    @app.get("/capabilities")
    def capabilities() -> dict[str, dict[str, str]]:
        return capability_report(settings)

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

    return app


app = create_app()
