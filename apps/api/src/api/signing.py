"""Signing moments: actions with legal or financial weight, taken only by the person themselves (#348, #355).

A route marked with `Depends(signing)` is one. A delegated (MCP) token cannot call it; an MCP client proposes, and the
person completes the action in the Web or a Feishu card. When the person completes something an external client proposed
(the request names it in `X-PPAP-Intent`), their sign-in must be recent. Everything else about signing follows from
this one marker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from product.mcp import Delegation

RECENT_SIGN_IN = timedelta(minutes=30)


class SigningRefused(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


def signing(request: Request) -> None:
    if isinstance(request.scope.get("ppap.delegation"), Delegation):
        raise SigningRefused("signing_requires_person", 403)
    if not request.headers.get("x-ppap-intent", "").strip():
        return
    session = request.app.state.session_of(request)
    if session is None:
        return  # the route itself answers an unauthenticated request
    # Completing a client's proposal: the person's own, still open, and this very call.
    proposal = getattr(request.state, "intent", None)
    if proposal is None or proposal["outcome"]["state"] in {"succeeded", "declined"}:
        raise SigningRefused("intent_unavailable", 409)
    if (proposal["arguments"]["method"], proposal["arguments"]["path"]) != (request.method, request.url.path):
        raise SigningRefused("intent_mismatch", 409)
    signed_in = session.signed_in_at
    if signed_in is None or datetime.now(timezone.utc) - signed_in > RECENT_SIGN_IN:
        raise SigningRefused("reauthentication_required", 401)


def refused(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SigningRefused)
    return JSONResponse({"error": exc.code}, status_code=exc.status)


def is_signing(route) -> bool:
    dependant = getattr(route, "dependant", None)
    return dependant is not None and any(dependency.call is signing for dependency in dependant.dependencies)
