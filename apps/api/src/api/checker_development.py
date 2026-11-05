"""Authenticated sample correction and explicit opt-in checker optimization."""

from __future__ import annotations

from typing import Any, Literal

from documents.model_access import ModelUnavailable
from fastapi import Query, Request
from fastapi.responses import JSONResponse
from product.store.checker_development import CheckerDevelopmentStore
from pydantic import BaseModel, ConfigDict, Field


class CheckerFeedbackEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_version_id: str
    locator: dict[str, Any]
    excerpt: str = Field(max_length=500)


class FeedbackEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_status: Literal["PASS", "NG", "NA"]
    rationale: str = Field(min_length=1, max_length=8000)
    evidence: list[CheckerFeedbackEvidence] = Field(min_length=1, max_length=20)


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


class FeedbackResponse(FeedbackEdit):
    id: str
    operation_id: str
    round_id: str
    file_version_id: str
    rule_version_id: str
    rule_clause_id: str
    result_attempt: int
    original_status: Literal["PASS", "NG", "NA", "UNABLE", "ERR"]
    suite_revision_id: str
    suite_key: str
    ordinal: int
    revision: int


class ValidationView(BaseModel):
    model_config = ConfigDict(extra="allow")
    verified: bool
    reasons: list[str] = Field(default_factory=list)
    total_samples: int
    passed_samples: int
    new_errors: int
    cases: list[dict[str, Any]] = Field(default_factory=list)


class DevelopmentReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    validation: ValidationView | None = None
    verified: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    error_code: str | None = None


class OptimizationReceipt(BaseModel):
    operation_id: str
    suite_revision_id: str
    suite_key: str
    state: Literal["saved", "queued", "running", "completed", "failed", "unknown", "stopped"]
    candidate_revision_id: str | None
    report: DevelopmentReport | None
    usage: dict[str, Any]
    failure: str | None
    cancel_requested: bool
    created_at: str


class FeedbackView(BaseModel):
    round_id: str
    feedback: list[FeedbackResponse]
    optimizations: list[OptimizationReceipt]


class FeedbackDeletion(BaseModel):
    id: str
    deleted: bool
    revision: int


class SuiteFeedbackView(BaseModel):
    feedback: list[FeedbackResponse]


def register_checker_development_routes(app, store, principal_of):
    def development():
        return CheckerDevelopmentStore(store)

    def call(request, operation):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return operation(principal)
        except ModelUnavailable:
            return JSONResponse({"error": "personal_model_unavailable"}, status_code=409)
        except KeyError:
            return JSONResponse({"error": "checker_access_denied"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/audit-rounds/{round_id}/checker-feedback", response_model=FeedbackView, response_model_exclude_unset=True)
    def read_feedback(round_id: str, request: Request):
        return call(request, lambda principal: development().list_feedback(principal, round_id))

    @app.get("/suite-revisions/{revision_id}/checker-feedback", response_model=SuiteFeedbackView)
    def read_suite_feedback(revision_id: str, request: Request):
        return call(request, lambda principal: development().list_suite_feedback(principal, revision_id))

    @app.post("/audit-rounds/{round_id}/checker-feedback", response_model=FeedbackResponse)
    def create_feedback(round_id: str, body: FeedbackCreate, request: Request):
        return call(request, lambda principal: development().save_feedback(principal, round_id, body.model_dump()))

    @app.put("/audit-rounds/{round_id}/checker-feedback/{feedback_id}", response_model=FeedbackResponse)
    def edit_feedback(round_id: str, feedback_id: str, body: FeedbackUpdate, request: Request):
        return call(
            request, lambda principal: development().edit_feedback(principal, round_id, feedback_id, expected_revision=body.expected_revision, payload=body.model_dump(exclude={"expected_revision"}))
        )

    @app.delete("/audit-rounds/{round_id}/checker-feedback/{feedback_id}", response_model=FeedbackDeletion)
    def delete_feedback(round_id: str, feedback_id: str, request: Request, expected_revision: int = Query(ge=1)):
        return call(request, lambda principal: development().edit_feedback(principal, round_id, feedback_id, expected_revision=expected_revision))

    @app.post("/suite-revisions/{revision_id}/optimizations", response_model=OptimizationReceipt, response_model_exclude_unset=True)
    def start_optimization(revision_id: str, body: OptimizationCreate, request: Request):
        return call(request, lambda principal: development().start(principal, revision_id, body.model_dump()))

    @app.get("/checker-optimizations/{operation_id}", response_model=OptimizationReceipt, response_model_exclude_unset=True)
    def read_optimization(operation_id: str, request: Request):
        return call(request, lambda principal: development().read(principal, operation_id))

    @app.post("/checker-optimizations/{operation_id}/stop", response_model=OptimizationReceipt, response_model_exclude_unset=True)
    def stop_optimization(operation_id: str, request: Request):
        return call(request, lambda principal: development().stop(principal, operation_id))
