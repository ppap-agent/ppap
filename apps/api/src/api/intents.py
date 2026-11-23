"""Proposals from external clients, as the person sees them in the Web (#348).

An MCP client proposes a signing action; the person opens the page the proposal names and completes it there, with the
request naming the proposal in `X-PPAP-Intent`, or declines it here. What became of it is read from what the person
did (#362). A rule-clarification step has no page of its own, so it is described from the task's current state.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, Request
from fastapi.responses import JSONResponse
from product.mcp import McpError
from product.store.actions import DECLINE_ROUTE

from api import web_contract as wc
from api.mcp_clarification import STEPS

SETTLED = {"succeeded", "declined"}


def register_intent_routes(app, session_of) -> None:
    """Proposals exist only where MCP is installed (`app.state.mcp_store`); elsewhere there are none to show."""

    @app.get("/intents/{intent_id}", response_model=wc.Intent)
    async def get_intent(intent_id: str, request: Request):
        session = await asyncio.to_thread(session_of, request)
        if not session:
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        store = getattr(request.app.state, "mcp_store", None)
        found = None if store is None else await asyncio.to_thread(store.intent, session.principal, intent_id)
        if store is None or found is None:
            return JSONResponse({"error": "intent_not_found"}, status_code=404)
        arguments = found["arguments"]
        shown: dict[str, Any] = {key: found[key] for key in ("id", "tool", "client_name", "created_at", "outcome")} | {
            "method": arguments["method"],
            "path": arguments["path"],
            "page": arguments["page"],
        }
        request_input = arguments["request"]
        if request_input.get("action_id") in STEPS and found["outcome"]["state"] not in SETTLED:
            try:
                delegation = await asyncio.to_thread(store.delegation, found["grant_id"])
                step = await request.app.state.mcp_clarifications.describe(delegation, request_input)
                shown["step"] = {key: step[key] for key in ("method", "path", "body", "summary", "details")}
            except McpError as exc:
                shown["unavailable"] = exc.code
        return shown

    @app.post(DECLINE_ROUTE, response_model=wc.IntentOutcome, openapi_extra=wc.openapi_body(wc.IntentDecline, required=False))
    async def decline_intent(intent_id: str, request: Request, body: Any = Body(None)):
        # The decline is an action of its own, recorded against the proposal the request names.
        session = await asyncio.to_thread(session_of, request)
        if not session:
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        found = getattr(request.state, "intent", None)
        if found is None or found["id"] != intent_id:
            return JSONResponse({"error": "intent_not_found"}, status_code=404)
        if found["outcome"]["state"] in SETTLED:
            return JSONResponse({"error": "intent_settled"}, status_code=409)
        reason = body.get("reason") if isinstance(body, dict) else None
        if body is not None and (not isinstance(body, dict) or set(body) - {"reason"} or not isinstance(reason, str | None) or len(reason or "") > 500):
            return JSONResponse({"error": "invalid_decline"}, status_code=400)
        return {"state": "declined", "reason": " ".join((reason or "").split()) or None}
