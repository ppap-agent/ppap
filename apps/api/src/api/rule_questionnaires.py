"""Rule clarification questionnaires on the rules page: members of the enterprise read them and answer.

A platform operator sends cards to the enterprise on one rulebook revision; offline loading, closing and exporting
through the database stay as they are.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from product.mcp import McpError
from product.store import rule_questionnaires as questionnaires
from pydantic import BaseModel

from api import web_contract as wc
from api.mcp_confirmation import approval_for

HEADERS = {"Cache-Control": "private, no-store"}
CONFLICTS = {"idempotency_conflict", "questionnaire_closed", "answer_revision_conflict", "question_exists"}


class RuleQuestionnaireRulebook(BaseModel):
    version: str
    revision: int


class RuleQuestionnaireSummary(BaseModel):
    id: str
    customer_id: str
    customer_name: str
    rule_version: str
    # The rulebook revision it was asked on.
    rulebook: RuleQuestionnaireRulebook
    title: str
    imported_at: str
    closed_at: str | None
    closed: bool
    total: int
    answered: int
    mine: int


class RuleQuestionnaireList(BaseModel):
    questionnaires: list[RuleQuestionnaireSummary]


class RuleQuestionOption(BaseModel):
    key: str
    label: str
    detail: str


class RuleQuestionRelated(BaseModel):
    clause: str
    question: str
    example: str


class RuleQuestionSource(BaseModel):
    """The full rule text a card is about, with the passages its question turns on."""

    clause: str
    text: str
    quotes: list[str]


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
    sources: list[RuleQuestionSource]
    items: list[str]
    relayed: RuleQuestionRelayed | None
    # The card this one asks further about, when its answers left something open.
    follows: str | None = None
    answers: list[RuleQuestionAnswer]


class RuleCardsRequest(BaseModel):
    customer_id: str
    rulebook: RuleQuestionnaireRulebook
    cards: list[dict[str, Any]]


class RuleCardsReceipt(RuleQuestionnaireSummary):
    added: int


class RuleQuestionnaireView(RuleQuestionnaireSummary):
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


def register_rule_questionnaire_routes(app: FastAPI, store, principal_of) -> None:
    def call(request: Request, operation):
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401, headers=HEADERS)
        try:
            return JSONResponse(operation(principal), headers=HEADERS)
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=HEADERS)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code}, status_code=409 if code in CONFLICTS else 400, headers=HEADERS)

    @app.get("/rule-questionnaires", response_model=RuleQuestionnaireList)
    def list_rule_questionnaires(request: Request):
        return call(request, lambda principal: questionnaires.list_questionnaires(store, principal))

    @app.post("/rule-questionnaires/cards", response_model=RuleCardsReceipt, openapi_extra=wc.openapi_body(RuleCardsRequest))
    def send_rule_cards(request: Request, body: Any = Body(...)):
        """Putting cards before the customer is a platform operator's decision; through MCP, one they approved."""
        principal = principal_of(request)
        if principal is not None and not store.settings.platform_operator(principal.tenant_key, principal.subject):
            return JSONResponse({"error": "platform_operator_required"}, status_code=403, headers=HEADERS)
        try:
            approval_for(request)
        except McpError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status, headers=HEADERS)
        return call(request, lambda principal: questionnaires.send_cards(store, principal, body))

    @app.get("/rule-questionnaires/{questionnaire_id}", response_model=RuleQuestionnaireView)
    def read_rule_questionnaire(questionnaire_id: str, request: Request):
        return call(request, lambda principal: questionnaires.read_questionnaire(store, principal, questionnaire_id))

    @app.post("/rule-questionnaires/{questionnaire_id}/answers", response_model=RuleAnswerReceipt, openapi_extra=wc.openapi_body(RuleAnswerRequest))
    def answer_rule_question(questionnaire_id: str, request: Request, body: Any = Body(...)):
        return call(request, lambda principal: questionnaires.answer(store, principal, questionnaire_id, body))

    @app.get("/rule-questionnaires/{questionnaire_id}/answers/{operation_id}", response_model=RuleAnswerReceipt | None)
    def read_rule_answer_operation(questionnaire_id: str, operation_id: str, request: Request):
        return call(request, lambda principal: questionnaires.read_answer_operation(store, principal, questionnaire_id, operation_id))
