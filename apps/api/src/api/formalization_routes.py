"""Authenticated formalization generation and confirmation for one published clause."""

from __future__ import annotations

from typing import Any, Literal

from documents.model_access import ModelUnavailable
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from product.store.formalizations import FormalizationStore
from pydantic import BaseModel, ConfigDict, Field


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    expected_source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    based_on_candidate_id: str | None = None
    clarification_answer: dict[str, Any] | None = None
    user_instruction: str | None = Field(default=None, max_length=2000)
    expected_model_revision: str | None = Field(default=None, max_length=80)
    consume_model: bool = False


class ConfirmationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    candidate_id: str
    expected_candidate_revision: int = Field(ge=1)
    expected_source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_display_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_current_confirmation_id: str | None = None


class MaterializeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    suite_revision_id: str


class FormalizationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    summary: str
    counterexample: str


class FormalizationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reason: str
    choices: list[FormalizationChoice] | None = None


class FormalizationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    formalization_revision: int
    parent_candidate_id: str | None = None
    formal_profile: str
    completeness: Literal["complete", "partial"]
    owl_content: str
    clif_content: str | None = None
    vocabulary: dict[str, Any]
    obligations: list[Any]
    assumptions: list[Any]
    unresolved: list[FormalizationQuestion]
    cnl_text: str
    cnl_renderer_version: str
    formal_digest: str
    display_digest: str
    source_rule_digest: str
    created_at: str


class FormalizationConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    operation_id: str
    candidate_id: str
    formalization_revision: int
    source_rule_digest: str
    display_digest: str
    confirmed_by: str
    confirmed_at: str
    current: bool


class OperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    rule_version_id: str
    clause_id: str
    state: str
    candidate_id: str | None = None
    usage: dict[str, Any] | None = None
    model_identity: dict[str, Any] | None = None
    failure: str | None = None
    created_at: str | None = None


class ClauseFormalizationView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_version_id: str
    clause_id: str
    clause_text: str
    source_rule_digest: str
    state: str
    candidates: list[FormalizationCandidate]
    confirmations: list[FormalizationConfirmation]
    operation: OperationReceipt | None = None


class FormalizationList(BaseModel):
    model_config = ConfigDict(extra="allow")
    rule_version_id: str
    clauses: list[dict]


class ConfirmationReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    operation_id: str
    candidate_id: str
    current: bool


class MaterializeReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    files: dict[str, str]
    rule_impact: dict[str, Any]


class FormalizationProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: str


_PROBLEM = {"model": FormalizationProblem}
_READ_PROBLEMS = {401: _PROBLEM, 403: _PROBLEM, 404: _PROBLEM, 422: _PROBLEM}
_WRITE_PROBLEMS = {401: _PROBLEM, 403: _PROBLEM, 409: _PROBLEM, 422: _PROBLEM, 503: _PROBLEM}


_CONFLICTS = {
    "source_changed",
    "candidate_stale",
    "display_changed",
    "conflicting_confirmation",
    "operation_conflict",
    "not_confirmable",
    "suite_key_mismatch",
    "source_unchecked",
}


def register_formalization_routes(app: FastAPI, store, principal_of):
    formalizations = FormalizationStore(store)

    def principal(request: Request):
        current = principal_of(request)
        if not current:
            return None, JSONResponse({"error": "unauthenticated"}, status_code=401)
        return current, None

    def fail(exc: Exception):
        if isinstance(exc, ModelUnavailable):
            return JSONResponse({"error": "model_unavailable"}, status_code=503)
        if isinstance(exc, KeyError):
            return JSONResponse({"error": "access_denied"}, status_code=403)
        code = str(exc)
        if code == "clause_not_found":
            return JSONResponse({"error": "clause_not_found"}, status_code=404)
        if code in _CONFLICTS:
            return JSONResponse({"error": code}, status_code=409)
        if code == "model_consumption_refused":
            return JSONResponse({"error": code}, status_code=422)
        return JSONResponse({"error": "invalid_formalization"}, status_code=422)

    @app.get("/rules/{rule_version_id}/formalizations", response_model=FormalizationList, responses=_READ_PROBLEMS)
    def list_formalizations(rule_version_id: str, request: Request):
        current, error = principal(request)
        if error:
            return error
        try:
            return JSONResponse(formalizations.list_clauses(current, rule_version_id), headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)

    @app.get("/rules/{rule_version_id}/formalizations/{clause_id}", response_model=ClauseFormalizationView, responses=_READ_PROBLEMS)
    def read_formalization(rule_version_id: str, clause_id: str, request: Request):
        current, error = principal(request)
        if error:
            return error
        try:
            return JSONResponse(formalizations.read_clause(current, rule_version_id, clause_id), headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)

    @app.post("/rules/{rule_version_id}/formalizations/{clause_id}/generations", status_code=202, response_model=OperationReceipt, responses=_WRITE_PROBLEMS)
    def start_generation(rule_version_id: str, clause_id: str, request: Request, body: GenerationCreate):
        current, error = principal(request)
        if error:
            return error
        try:
            receipt = formalizations.start_generation(current, rule_version_id, clause_id, body.model_dump(mode="json"))
            return JSONResponse(receipt, status_code=202, headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)

    @app.get("/rule-formalization-generations/{operation_id}", response_model=OperationReceipt, responses=_READ_PROBLEMS)
    def read_generation(operation_id: str, request: Request):
        current, error = principal(request)
        if error:
            return error
        try:
            return JSONResponse(formalizations.read_operation(current, operation_id), headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)

    @app.post("/rules/{rule_version_id}/formalization-suite-candidates", status_code=201, response_model=MaterializeReceipt, responses=_WRITE_PROBLEMS)
    def materialize_formalization(rule_version_id: str, request: Request, body: MaterializeCreate):
        current, error = principal(request)
        if error:
            return error
        try:
            receipt = formalizations.materialize_candidate(current, rule_version_id, body.model_dump(mode="json"))
            return JSONResponse(receipt, status_code=201, headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)

    @app.post("/rules/{rule_version_id}/formalizations/{clause_id}/confirmations", status_code=201, response_model=ConfirmationReceipt, responses=_WRITE_PROBLEMS)
    def confirm_formalization(rule_version_id: str, clause_id: str, request: Request, body: ConfirmationCreate):
        current, error = principal(request)
        if error:
            return error
        try:
            receipt = formalizations.confirm(current, rule_version_id, clause_id, body.model_dump(mode="json"))
            return JSONResponse(receipt, status_code=201, headers={"Cache-Control": "private, no-store"})
        except (KeyError, ValueError, ModelUnavailable) as exc:
            return fail(exc)
