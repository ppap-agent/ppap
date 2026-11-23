"""Rulebook revisions: every member of the enterprise reads its own; platform operators write a revision once, mark it
clarified and put readings beside its clauses. A call delegated through MCP writes only what the person approved."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from product.mcp import McpError
from product.store import rulebook_revisions as revisions
from product.template_library import template_root
from pydantic import BaseModel

from api import web_contract as wc
from api.signing import signing

HEADERS = {"Cache-Control": "private, no-store"}


class RulebookRevisionRef(BaseModel):
    version: str
    revision: int


class RulebookChangeCounts(BaseModel):
    same: int
    changed: int
    new: int


class RulebookRevisionSummary(BaseModel):
    id: str
    customer_id: str
    version: str
    revision: int
    clause_count: int
    base: RulebookRevisionRef | None
    changes: RulebookChangeCounts | None
    origin: Literal["shipped", "local", "clarification"]
    created_at: str
    clarified: bool


class RulebookRevisionList(BaseModel):
    revisions: list[RulebookRevisionSummary]


class RulebookChangeSource(BaseModel):
    question_key: str | None = None
    answerers: list[str] | None = None
    ruling: str | None = None


class RulebookClauseChange(BaseModel):
    kind: Literal["same", "changed", "new"]
    note: str
    sources: list[RulebookChangeSource] | None = None


class RulebookClauseReading(BaseModel):
    checks: list[str]
    passes: list[str]
    fails: list[str]
    manual: list[str]


class RulebookClause(BaseModel):
    id: str
    category: str
    text: str
    digest: str
    change: RulebookClauseChange | None
    reading: RulebookClauseReading | None


class RulebookDocument(BaseModel):
    key: str
    title: str
    clauses: list[RulebookClause]


class RulebookRevisionView(RulebookRevisionSummary):
    documents: list[RulebookDocument]


class RulebookRevisionRequest(BaseModel):
    version: str
    revision: int
    base: RulebookRevisionRef | None
    documents: list[dict[str, Any]]
    changes: dict[str, Any]
    origin: Literal["local", "clarification"]


class RulebookReadingsRequest(BaseModel):
    readings: dict[str, RulebookClauseReading]


def register_rulebook_routes(app: FastAPI, store, principal_of, settings) -> None:
    def call(request: Request, operation, *, write: bool = False):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        try:
            if write:
                if not settings.platform_operator(principal.tenant_key, principal.subject):
                    return JSONResponse({"error": "platform_operator_required"}, status_code=403, headers=HEADERS)
            found = operation(principal)
        except McpError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status, headers=HEADERS)
        except KeyError:
            return JSONResponse({"error": "rulebook_revision_not_found"}, status_code=404, headers=HEADERS)
        except ValueError as exc:
            code, _, detail = str(exc).partition(":")
            return JSONResponse({"error": code, **({"detail": detail} if detail else {})}, status_code=409 if code in {"rulebook_revision_exists", "clause_reading_exists"} else 400, headers=HEADERS)
        if found is None:
            return JSONResponse({"error": "rulebook_revision_not_found"}, status_code=404, headers=HEADERS)
        return JSONResponse(found, headers=HEADERS)

    root = template_root(settings)

    @app.get("/rulebooks/{customer_id}", response_model=RulebookRevisionList)
    def list_rulebook_revisions(customer_id: str, request: Request):
        return call(request, lambda principal: revisions.list_revisions(store, principal, customer_id, root))

    @app.get("/rulebooks/{customer_id}/{version}/{revision}", response_model=RulebookRevisionView)
    def read_rulebook_revision(customer_id: str, version: str, revision: int, request: Request):
        return call(request, lambda principal: revisions.read_revision(store, principal, customer_id, version, revision, root))

    @app.post("/rulebooks/{customer_id}/revisions", dependencies=[Depends(signing)], response_model=RulebookRevisionView, openapi_extra=wc.openapi_body(RulebookRevisionRequest))
    def create_rulebook_revision(customer_id: str, request: Request, body: Any = Body(...)):
        return call(request, lambda principal: revisions.create_revision(store, principal, customer_id, body, root), write=True)

    @app.post("/rulebooks/{customer_id}/{version}/{revision}/clarified", dependencies=[Depends(signing)], response_model=RulebookRevisionView)
    def clarify_rulebook_revision(customer_id: str, version: str, revision: int, request: Request):
        return call(request, lambda principal: revisions.clarify(store, principal, customer_id, version, revision, root), write=True)

    @app.put("/rulebooks/{customer_id}/{version}/{revision}/readings", dependencies=[Depends(signing)], response_model=RulebookRevisionView, openapi_extra=wc.openapi_body(RulebookReadingsRequest))
    def save_rulebook_readings(customer_id: str, version: str, revision: int, request: Request, body: Any = Body(...)):
        return call(request, lambda principal: revisions.save_readings(store, principal, customer_id, version, revision, body, root), write=True)
