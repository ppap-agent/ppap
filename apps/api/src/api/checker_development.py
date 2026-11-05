"""Authenticated sample correction and explicit opt-in checker optimization."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Query, Request
from fastapi.responses import JSONResponse
from product.store.checker_development import CheckerDevelopmentStore
from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_version_id: str
    locator: dict[str, Any]
    excerpt: str = Field(max_length=500)


class FeedbackEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_status: Literal["PASS", "NG", "NA"]
    rationale: str = Field(min_length=1, max_length=8000)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)


class FeedbackCreate(FeedbackEdit):
    operation_id: str
    file_version_id: str
    rule_version_id: str
    rule_clause_id: str = Field(min_length=1, max_length=200)
    original_status: Literal["PASS", "NG", "NA", "UNABLE", "ERR"]
    result_attempt: int = Field(ge=1, strict=True)


class FeedbackUpdate(FeedbackEdit):
    expected_revision: int = Field(ge=1, strict=True)


class OptimizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    feedback_ids: list[str] = Field(min_length=1, max_length=64)
    action: Literal["save_only", "run_tests"]
    consume_model: bool = False


def register_checker_development_routes(app, store, principal_of):
    development = CheckerDevelopmentStore(store)

    def call(request, operation):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return operation(principal)
        except KeyError:
            return JSONResponse({"error": "checker_access_denied"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/audit-rounds/{round_id}/checker-feedback")
    def read_feedback(round_id: str, request: Request):
        return call(request, lambda principal: development.list_feedback(principal, round_id))

    @app.post("/audit-rounds/{round_id}/checker-feedback")
    def create_feedback(round_id: str, body: FeedbackCreate, request: Request):
        return call(request, lambda principal: development.save_feedback(principal, round_id, body.model_dump()))

    @app.put("/audit-rounds/{round_id}/checker-feedback/{feedback_id}")
    def edit_feedback(round_id: str, feedback_id: str, body: FeedbackUpdate, request: Request):
        return call(
            request, lambda principal: development.edit_feedback(principal, round_id, feedback_id, expected_revision=body.expected_revision, payload=body.model_dump(exclude={"expected_revision"}))
        )

    @app.delete("/audit-rounds/{round_id}/checker-feedback/{feedback_id}")
    def delete_feedback(round_id: str, feedback_id: str, request: Request, expected_revision: int = Query(ge=1)):
        return call(request, lambda principal: development.edit_feedback(principal, round_id, feedback_id, expected_revision=expected_revision))

    @app.post("/suite-revisions/{revision_id}/optimizations")
    def start_optimization(revision_id: str, body: OptimizationCreate, request: Request):
        return call(request, lambda principal: development.start(principal, revision_id, body.model_dump()))

    @app.get("/checker-optimizations/{operation_id}")
    def read_optimization(operation_id: str, request: Request):
        return call(request, lambda principal: development.read(principal, operation_id))

    @app.post("/checker-optimizations/{operation_id}/stop")
    def stop_optimization(operation_id: str, request: Request):
        return call(request, lambda principal: development.stop(principal, operation_id))
