"""Rule clarification questionnaires on the rules page: members answer, platform operators import, close and export."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from product.store import rule_questionnaires as questionnaires
from pydantic import BaseModel

from api import web_contract as wc

HEADERS = {"Cache-Control": "private, no-store"}
CONFLICTS = {"idempotency_conflict", "questionnaire_exists", "questionnaire_closed", "answer_revision_conflict"}


class RuleQuestionnaireSummary(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    rule_version: str
    title: str
    imported_at: str
    closed_at: str | None
    closed: bool
    total: int
    answered: int
    mine: int


class RuleQuestionnaireList(BaseModel):
    questionnaires: list[RuleQuestionnaireSummary]
    can_manage: bool


class RuleQuestionOption(BaseModel):
    key: str
    label: str
    detail: str


class RuleQuestionRelated(BaseModel):
    clause: str
    question: str
    example: str


class RuleQuestionRelayed(BaseModel):
    answer: str
    applied: str
    source: str
    date: str


class RuleAnswerRevision(BaseModel):
    revision: int
    choice: str | None
    text: str | None
    note: str | None
    answered_at: str


class RuleQuestionAnswer(RuleAnswerRevision):
    answerer: str
    mine: bool
    first_answered_at: str
    history: list[RuleAnswerRevision]


class RuleQuestion(BaseModel):
    key: str
    group: Literal["common", "conclusion", "general", "clause"]
    clauses: list[str]
    question: str
    why: str
    options: list[RuleQuestionOption]
    expect: str
    related: list[RuleQuestionRelated]
    items: list[str]
    relayed: RuleQuestionRelayed | None
    answers: list[RuleQuestionAnswer]


class RuleQuestionnaireView(RuleQuestionnaireSummary):
    can_manage: bool
    questions: list[RuleQuestion]


class RuleAnswerRequest(BaseModel):
    operation_id: str
    question_key: str
    expected_revision: int
    choice: str | None
    text: str | None
    note: str | None


class RuleAnswerReceipt(RuleAnswerRevision):
    operation_id: str
    questionnaire_id: str
    question_key: str
    answerer: str
    mine: bool


class RuleQuestionnaireImportRequest(BaseModel):
    operation_id: str
    questionnaire: dict[str, Any]


def register_rule_questionnaire_routes(app: FastAPI, store, principal_of) -> None:
    def call(request: Request, operation, status_code: int = 200):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        try:
            return JSONResponse(operation(principal), status_code=status_code, headers=HEADERS)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403, headers=HEADERS)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=HEADERS)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code}, status_code=409 if code in CONFLICTS else 400, headers=HEADERS)

    @app.get("/rule-questionnaires", response_model=RuleQuestionnaireList)
    def list_rule_questionnaires(request: Request):
        return call(request, lambda principal: questionnaires.list_questionnaires(store, principal))

    @app.post("/rule-questionnaires", response_model=RuleQuestionnaireSummary, status_code=201, openapi_extra=wc.openapi_body(RuleQuestionnaireImportRequest))
    def import_rule_questionnaire(request: Request, body: Any = Body(...)):
        return call(request, lambda principal: questionnaires.import_questionnaire(store, principal, body), 201)

    @app.get("/rule-questionnaires/{questionnaire_id}", response_model=RuleQuestionnaireView)
    def read_rule_questionnaire(questionnaire_id: str, request: Request):
        return call(request, lambda principal: questionnaires.read_questionnaire(store, principal, questionnaire_id))

    @app.post("/rule-questionnaires/{questionnaire_id}/answers", response_model=RuleAnswerReceipt, openapi_extra=wc.openapi_body(RuleAnswerRequest))
    def answer_rule_question(questionnaire_id: str, request: Request, body: Any = Body(...)):
        return call(request, lambda principal: questionnaires.answer(store, principal, questionnaire_id, body))

    @app.get("/rule-questionnaires/{questionnaire_id}/answers/{operation_id}", response_model=RuleAnswerReceipt | None)
    def read_rule_answer_operation(questionnaire_id: str, operation_id: str, request: Request):
        return call(request, lambda principal: questionnaires.read_answer_operation(store, principal, questionnaire_id, operation_id))

    @app.post("/rule-questionnaires/{questionnaire_id}/close", response_model=RuleQuestionnaireSummary)
    def close_rule_questionnaire(questionnaire_id: str, request: Request):
        return call(request, lambda principal: questionnaires.close_questionnaire(store, principal, questionnaire_id))

    @app.get("/rule-questionnaires/{questionnaire_id}/export")
    def export_rule_questionnaire(questionnaire_id: str, request: Request):
        def export(principal):
            return questionnaires.export_answers(store, principal, questionnaire_id)

        response = call(request, export)
        if response.status_code == 200:
            response.headers["Content-Disposition"] = 'attachment; filename="rule-questionnaire-answers.json"'
        return response
