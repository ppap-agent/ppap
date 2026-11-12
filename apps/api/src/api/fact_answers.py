"""Version-bound human observations, separate from rule interpretation and reviews."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from product.mcp import Delegation, McpError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from api.mcp_confirmation import require_approved_source


class FactQuestionView(BaseModel):
    file_version_id: str
    field: str
    prompt: str
    answer_type: Literal["boolean", "text"]
    locator: dict[str, Any]
    clause_ids: list[str]
    item_keys: list[str]
    fingerprint: str
    answered: bool


class FactAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, strict=True)
    file_version_id: str
    field: str = Field(min_length=1, max_length=256)
    expected_question_fingerprint: str = Field(pattern="^[a-f0-9]{64}$")
    value: StrictBool | Annotated[StrictStr, Field(max_length=2000)] | None


class FactAnswer(BaseModel):
    operation_id: str
    round_id: str
    file_version_id: str
    field: str
    value: StrictBool | StrictStr | None
    answered_by: str
    answered_at: str
    locator: dict[str, Any]
    prompt: str
    answer_type: Literal["boolean", "text"]
    result_attempt: int | None = None


class FactAnswerSource(BaseModel):
    file_version_id: str
    field: str
    locator: dict[str, Any]
    answered_by: str
    answered_at: str
    value: StrictBool | StrictStr | None


class FactConfirmedResult(BaseModel):
    item_key: str
    status: Literal["PASS", "NG", "NA", "UNABLE", "ERR"]
    suggestions: str
    answer_ids: list[str]
    confirmed_by: list[str]
    confirmed_at: str
    sources: list[FactAnswerSource]


class FactAnswersView(BaseModel):
    round_id: str
    revision: int
    questions: list[FactQuestionView]
    answers: list[FactAnswer]
    results: list[FactConfirmedResult]
    model_calls: Literal[0]


class FactAnswerReceipt(BaseModel):
    operation_id: str
    round_id: str
    revision: int
    answer: FactAnswer
    results: list[FactConfirmedResult]
    model_calls: Literal[0]


class FactMachineFinding(BaseModel):
    item_key: str
    status: Literal["PASS", "NG", "NA", "UNABLE", "ERR"]
    suggestions: str
    locator: dict[str, Any] | None


class FactAnswersExport(FactAnswersView):
    schema_version: Literal["original-fact-confirmations-v1"] = Field(alias="schema")
    training_eligible: Literal[False]
    machine_findings: list[FactMachineFinding]


def register_fact_answer_routes(app: FastAPI, store, principal_of) -> None:
    def call(request, operation):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            return operation(principal)
        except McpError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code}, status_code=409 if any(word in code for word in ("conflict", "expired", "changed")) else 400)

    @app.get("/audit-rounds/{round_id}/fact-answers", response_model=FactAnswersView, response_model_exclude_unset=True)
    def read_fact_answers(round_id: str, request: Request):
        return call(request, lambda principal: store.read_fact_answers(principal, round_id))

    @app.post("/audit-rounds/{round_id}/fact-answers", response_model=FactAnswerReceipt, response_model_exclude_unset=True)
    def apply_fact_answer(round_id: str, body: FactAnswerRequest, request: Request):
        def apply(principal):
            delegated = request.scope.get("ppap.delegation")
            if isinstance(delegated, Delegation):
                delegated.require("audits:read")
                delegated.require("reviews:write")
                require_approved_source(request, f"/audit-rounds/{round_id}/fact-answers", FactAnswersView.model_validate(store.read_fact_answers(principal, round_id)).model_dump(mode="json"))
            return store.apply_fact_answer(principal, round_id, body.model_dump())

        return call(request, apply)

    @app.get("/audit-rounds/{round_id}/fact-answers/export", response_model=FactAnswersExport, response_model_exclude_unset=True)
    def export_fact_answers(round_id: str, request: Request):
        return call(request, lambda principal: store.export_fact_answers(principal, round_id))

    @app.get("/audit-rounds/{round_id}/fact-answers/report", responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
    def fact_confirmation_report(round_id: str, request: Request):
        from documents.audit_pdf import render_fact_confirmation_pdf

        def render(principal):
            body = render_fact_confirmation_pdf(store.export_fact_answers(principal, round_id))
            return Response(body, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="original-fact-confirmations.pdf"', "Cache-Control": "no-store"})

        return call(request, render)

    @app.get("/audit-rounds/{round_id}/fact-answers/{operation_id}", response_model=FactAnswerReceipt | None, response_model_exclude_unset=True)
    def read_fact_answer_operation(round_id: str, operation_id: str, request: Request):
        return call(request, lambda principal: store.read_fact_answer_operation(principal, round_id, operation_id))
