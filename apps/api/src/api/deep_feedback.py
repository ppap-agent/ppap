"""Web adapter for the same rule-change application used by MCP."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from product.mcp import McpError
from pydantic import BaseModel, ConfigDict, Field


class FeedbackRuleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    rule_id: str


class FeedbackScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(min_length=1, max_length=256)
    rule_ids: list[str] = Field(min_length=1, max_length=256)
    rule_targets: list[FeedbackRuleTarget] | None = Field(default=None, min_length=1, max_length=16384)


class FeedbackCandidate(BaseModel):
    id: str
    label: str
    explanation: str


class FeedbackSource(BaseModel):
    document_id: str
    locator: dict[str, Any]
    excerpt: str | None = None


class FeedbackQuestion(BaseModel):
    id: str
    fingerprint: str
    input_id: str
    kind: str
    question: str
    document_ids: list[str]
    rule_ids: list[str]
    candidates: list[FeedbackCandidate]
    sources: list[FeedbackSource]
    scope: FeedbackScope


class ConfirmedAppendix(BaseModel):
    id: str
    kind: Literal["interpretation", "mapping", "standard_change"]
    content: str
    scope: FeedbackScope
    question_id: str | None = None
    candidate_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    confirmation_source: Literal["user", "synthetic"] = "user"


class RuleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    expected_revision: int = Field(ge=0, strict=True)
    question_id: str | None = None
    expected_question_fingerprint: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    candidate_id: str | None = None
    kind: Literal["interpretation", "mapping", "standard_change"] | None = None
    content: str | None = Field(default=None, min_length=1, max_length=8192)
    scope: FeedbackScope | None = None
    remove_appendix_ids: list[str] = Field(default_factory=list, max_length=256)
    intent: Literal["save_only", "save_and_analyze"]
    confirmation_source: Literal["user", "synthetic"] = "user"


class McpRuleChangeProposal(BaseModel):
    round_id: str
    change: RuleChangeRequest


class RuleChangeReceipt(BaseModel):
    operation_id: str
    workspace_id: str
    state: Literal["saved"]
    revision: int
    source_round_id: str
    analysis_round_id: str | None
    analysis_state: Literal["not_started", "queued"]
    appendix: list[ConfirmedAppendix]


class FeedbackResult(BaseModel):
    round_id: str
    execution: str
    current: bool
    findings: list[dict[str, Any]]
    coverage: dict[str, Any]
    inputs: list[dict[str, Any]]


class DeepFeedbackView(BaseModel):
    round_id: str
    workspace_id: str
    current_round_id: str
    revision: int
    input_changed: bool
    clarifications: list[FeedbackQuestion]
    appendix: list[ConfirmedAppendix]
    operations: list[RuleChangeReceipt]
    results: list[FeedbackResult]


def register_deep_feedback_routes(app: FastAPI, store, principal_of, quota_for) -> None:
    def call(request, operation):
        principal = principal_of(request)
        if not principal:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return operation(principal)
        except McpError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code}, status_code=409 if "conflict" in code or "expired" in code or "changed" in code else 400)

    @app.get("/audit-rounds/{round_id}/deep-feedback", response_model=DeepFeedbackView)
    def read(round_id: str, request: Request):
        return call(request, lambda principal: store.read_deep_feedback(principal, round_id))

    @app.post("/audit-rounds/{round_id}/rule-changes", response_model=RuleChangeReceipt)
    def apply(round_id: str, body: RuleChangeRequest, request: Request):
        return call(
            request,
            lambda principal: store.apply_rule_change(
                principal,
                round_id,
                body.model_dump(exclude_none=True),
                usage_policy=quota_for(request, principal) if body.intent == "save_and_analyze" and store.settings.usage_charge_enabled else None,
            ),
        )

    @app.get("/audit-rounds/{round_id}/rule-changes/{operation_id}", response_model=RuleChangeReceipt | None)
    def operation(round_id: str, operation_id: str, request: Request):
        return call(request, lambda principal: store.read_rule_change(principal, round_id, operation_id))

    @app.get("/audit-rounds/{round_id}/deep-feedback/export")
    def export(round_id: str, request: Request):
        return call(request, lambda principal: store.export_deep_feedback(principal, round_id))

    @app.get("/audit-rounds/{round_id}/mcp-rule-changes/{transport_id}", response_model=McpRuleChangeProposal)
    def proposal(round_id: str, transport_id: str, request: Request):
        def read_proposal(principal):
            adapter = getattr(app.state, "mcp_deep_adapter", None)
            if adapter is None:
                return JSONResponse({"error": "mcp_not_available"}, status_code=503)
            return adapter.proposal(principal, round_id, transport_id)

        return call(request, read_proposal)
