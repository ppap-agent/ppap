"""Signing off a round (#357): the person who ran it stands by its conclusion, and may withdraw it with a reason.

Both writes are signing moments (`api.signing`): the person does them in the Web; an MCP client only proposes signing
(`sign_round`). Who did them, when and why is their action as recorded (#362). Everyone in the enterprise reads where a
round stands; only its creator signs or withdraws (403 `access_denied` for anyone else).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from fastapi import Body, Depends, Request
from fastapi.responses import JSONResponse
from product.store import signoff
from pydantic import BaseModel

from api.access import object_denied
from api.signing import signing
from api.web_contract import openapi_body

Conclusion = Literal["PASS", "NG", "UNKNOWN", "ERROR"]
# Refusals the person can fix by what they send; every other refusal is the round's state (409).
BAD_REQUEST = frozenset({"withdraw_reason_required", "withdraw_reason_too_long", "invalid_signoff_request", "signoff_unrecorded"})


class SignoffReport(BaseModel):
    report_id: str
    sha256: str | None


class SignoffFile(BaseModel):
    file_version_id: str
    filename: str
    rule_title: str | None
    # As the result page shows it (confirmed original facts included); the machine's own beside it.
    conclusion: Conclusion | None
    machine_conclusion: Conclusion | None
    # 待审核 clauses (无法判断 / 未覆盖), never counted as PASS.
    pending_count: int
    # Clauses with a human review (人工确认), and those accepted on the machine's conclusion.
    reviewed_count: int
    accepted_count: int
    report: SignoffReport | None


class SignoffReview(BaseModel):
    item_key: str
    version: int
    judgment: str


class SignoffUnreviewed(BaseModel):
    file_version_id: str
    clause_id: str
    item_key: str | None
    outcome: str


class SignoffContent(BaseModel):
    """What a signoff freezes: the round's one conclusion, each file's, the review version of every reviewed item, the
    items accepted on the machine's conclusion and the reports' fingerprint."""

    conclusion: Conclusion | None
    machine_conclusion: Conclusion | None
    files: list[SignoffFile]
    reviewed_count: int
    accepted_count: int
    reviews: list[SignoffReview]
    unreviewed: list[SignoffUnreviewed]
    report_fingerprint: str
    # Of everything above: the Web sends the one it showed as `if_match`.
    fingerprint: str


class SignoffSigner(BaseModel):
    name: str
    mine: bool


class SignoffUnavailable(BaseModel):
    code: Literal["not_creator", "not_finished"]
    message: str


class SignoffEvent(BaseModel):
    action: Literal["signed", "withdrawn"]
    at: str
    by: str
    reason: str | None


class RoundSignoff(BaseModel):
    round_id: str
    task_id: str | None
    state: Literal["signed", "unsigned"]
    signed_at: str | None
    signer: SignoffSigner | None
    # What was signed, while signed.
    signed: SignoffContent | None
    # What signing now would freeze, while unsigned and the task has ended.
    current: SignoffContent | None
    can_sign: bool
    can_withdraw: bool
    unavailable: SignoffUnavailable | None
    history: list[SignoffEvent]


class SignoffRequest(TypedDict, total=False):
    # The `current.fingerprint` the person saw; a round that changed since is refused (409 content_changed), not signed.
    if_match: str


class SignoffWithdrawal(TypedDict):
    reason: str


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def register_signoff_routes(app, store, principal_of) -> None:
    def call(request: Request, round_id: str, operation, *, modify: bool = False) -> JSONResponse:
        principal = principal_of(request)
        if principal is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        parsed = _uuid(round_id)
        if parsed is None:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        if denied := object_denied(store, principal, parsed, modify=modify, kind="audit_round"):
            return denied
        try:
            return JSONResponse(operation(principal, parsed))
        except KeyError:
            return JSONResponse({"error": "access_denied"}, status_code=403)
        except ValueError as exc:
            code = str(exc)
            return JSONResponse({"error": code}, status_code=400 if code in BAD_REQUEST else 409)

    @app.get("/rounds/{round_id}/signoff", response_model=RoundSignoff)
    def read_signoff(round_id: str, request: Request):
        return call(request, round_id, lambda principal, parsed: signoff.status(store, principal, parsed))

    @app.post("/rounds/{round_id}/signoff", dependencies=[Depends(signing)], response_model=RoundSignoff, openapi_extra=openapi_body(SignoffRequest, required=False))
    def sign_round(round_id: str, request: Request, body: Any = Body(None)):
        def sign(principal, parsed):
            if body is not None and (not isinstance(body, dict) or set(body) - {"if_match"} or not isinstance(body.get("if_match"), str | None)):
                raise ValueError("invalid_signoff_request")
            expected = body.get("if_match") if isinstance(body, dict) else None
            return signoff.sign(store, principal, parsed, action_id=getattr(request.state, "action_id", None), expected=expected)

        return call(request, round_id, sign, modify=True)

    @app.post("/rounds/{round_id}/signoff/withdraw", dependencies=[Depends(signing)], response_model=RoundSignoff, openapi_extra=openapi_body(SignoffWithdrawal))
    def withdraw_signoff(round_id: str, request: Request, body: Any = Body(None)):
        def withdraw(principal, parsed):
            if body is not None and (not isinstance(body, dict) or set(body) - {"reason"}):
                raise ValueError("invalid_signoff_request")
            # The reason reaches the record as this action's parameter (#362); nothing else keeps it.
            return signoff.withdraw(store, principal, parsed, (body or {}).get("reason"), action_id=getattr(request.state, "action_id", None))

        return call(request, round_id, withdraw, modify=True)
