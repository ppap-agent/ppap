"""Rulebook revisions shipped with the product: any member reads them on the rules page; nothing here writes."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from product.rulebooks import list_revisions, read_revision
from product.template_library import template_root
from pydantic import BaseModel

HEADERS = {"Cache-Control": "private, no-store"}


class RulebookRevisionRef(BaseModel):
    version: str
    revision: int


class RulebookChangeCounts(BaseModel):
    same: int
    changed: int
    new: int


class RulebookRevisionSummary(BaseModel):
    version: str
    revision: int
    # True only when its clauses are exactly the rules audits run now.
    in_audit: bool
    clause_count: int
    base: RulebookRevisionRef | None
    changes: RulebookChangeCounts | None


class RulebookRevisionList(BaseModel):
    revisions: list[RulebookRevisionSummary]


class RulebookClauseChange(BaseModel):
    kind: Literal["same", "changed", "new"]
    note: str


class RulebookClause(BaseModel):
    id: str
    category: str
    text: str
    change: RulebookClauseChange | None


class RulebookDocument(BaseModel):
    title: str
    clauses: list[RulebookClause]


class RulebookRevisionView(RulebookRevisionSummary):
    documents: list[RulebookDocument]


def register_rulebook_routes(app: FastAPI, principal_of, settings) -> None:
    @app.get("/rulebooks/{customer_id}", response_model=RulebookRevisionList)
    def list_rulebook_revisions(customer_id: str, request: Request):
        if principal_of(request) is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        return JSONResponse({"revisions": list_revisions(template_root(settings), customer_id)}, headers=HEADERS)

    @app.get("/rulebooks/{customer_id}/{version}/{revision}", response_model=RulebookRevisionView)
    def read_rulebook_revision(customer_id: str, version: str, revision: int, request: Request):
        if principal_of(request) is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        found = read_revision(template_root(settings), customer_id, version, revision)
        if found is None:
            return JSONResponse({"error": "rulebook_revision_not_found"}, status_code=404, headers=HEADERS)
        return JSONResponse(found, headers=HEADERS)
