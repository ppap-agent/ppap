"""An in-process adapter to the same authorized Product API used by Web.

Only this adapter can add a Delegation object to the ASGI scope. No trusted
identity headers, loopback HTTP credentials, direct Store bypass or new job queue.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Generic, Literal, TypeVar
from urllib.parse import urlencode

import httpx
from product.logs import REQUEST_ID, emit
from product.mcp import MAX_RESULT_BYTES, Delegation, McpError
from product.store.mcp import McpStore
from pydantic import BaseModel, Field

T = TypeVar("T")


class Reply(BaseModel, Generic[T]):
    """The receipt of one write, as the gateway knows it."""

    status: Literal["ok", "error", "confirmation_required", "running", "unknown", "denied"]
    data: T | None = None
    error: str | None = None
    request_id: str | None = None
    confirmation_url: str | None = None
    retry_after_seconds: int | None = None
    provenance: dict | None = None


class Outcome(BaseModel):
    """What every tool returns: the confirmed result, what is open, and the one thing to do next."""

    schema_version: str = "1.0"
    task_id: str | None = None
    task_revision: int | None = Field(default=None, description="Pass back as expected_revision when deciding an issue.")
    result: Any = Field(default=None, description="What is confirmed. result.summary says it in the user's words; tell them that, not the fields.")
    issues: list[dict] = Field(default_factory=list, description="Open problems and decisions. Each lists the only actions it accepts.")
    next: dict = Field(description="call_tool, user_action, wait, recover, done or blocked. Advice from the task's state, never permission to charge or send.")
    available_actions: list[dict] = Field(default_factory=list)
    request_id: str | None = None


class PageRequest(BaseModel):
    pointer: str = Field(default="", description="JSON Pointer to an array or object within the authorized result.")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=100)


class DataPage(BaseModel):
    value: Any
    pointer: str
    next_offset: int | None = None
    total: int | None = None


def page_result(value: Any, page: PageRequest) -> DataPage:
    selected = value
    if page.pointer:
        if not page.pointer.startswith("/"):
            raise McpError("invalid_pointer")
        try:
            for token in page.pointer[1:].split("/"):
                part = token.replace("~1", "/").replace("~0", "~")
                selected = selected[int(part)] if isinstance(selected, list) else selected[part]
        except (KeyError, IndexError, TypeError, ValueError):
            raise McpError("pointer_not_found", 404) from None
    total = None
    next_offset = None
    if isinstance(selected, list):
        total = len(selected)
        end = min(total, page.offset + page.limit)
        selected = selected[page.offset : end]
        next_offset = end if end < total else None
    result = DataPage(value=selected, pointer=page.pointer, next_offset=next_offset, total=total)
    if len(result.model_dump_json().encode()) > MAX_RESULT_BYTES:
        raise McpError("result_too_large_use_pointer_or_smaller_page", 413)
    return result


class BusinessGateway:
    def __init__(self, app, store: McpStore):
        self.app, self.store = app, store

    async def request(self, delegation: Delegation, method: str, path: str, *, body=None, params=None, content=None, operation_id=None) -> httpx.Response:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise McpError("invalid_business_path")

        async def authorized(scope, receive, send):
            scope = {**scope, "ppap.delegation": delegation, "ppap.mcp_operation_id": operation_id}
            await self.app(scope, receive, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=authorized), base_url="http://ppap.internal") as client:
            return await client.request(method, path, json=body, params=params, content=content, headers={"X-Request-ID": REQUEST_ID.get()})

    async def get(self, delegation: Delegation, scope: str, path: str, *, params=None) -> Any:
        delegation.require(scope)
        response = await self.request(delegation, "GET", path, params=params)
        if not response.is_success:
            try:
                code = response.json().get("error", "business_request_failed")
            except ValueError:
                code = "business_request_failed"
            raise McpError(code, response.status_code)
        return response.json()

    async def require_probes(self, delegation: Delegation, probes: list[str]):
        for path in probes:
            response = await self.request(delegation, "GET", path)
            if not response.is_success:
                raise McpError("access_denied", 403)

    async def write(self, delegation: Delegation, scope: str, tool: str, request_id: str, method: str, path: str, body: dict | None, *, probes: list[str], result_path=None) -> Reply:
        """A write the grant itself allows (never a signing one, #348), carried out once under its request_id."""
        delegation.require(scope)
        await self.require_probes(delegation, probes)
        arguments = {"method": method, "path": path, "body": body, "probes": probes}
        operation = await asyncio.to_thread(self.store.prepare_operation, delegation, request_id, tool, arguments, scope)
        if operation["state"] != "ready":
            return await self.operation_reply(delegation, operation)
        if not await asyncio.to_thread(self.store.claim_operation, delegation, operation["id"]):
            return await self.operation_reply(delegation, await asyncio.to_thread(self.store.operation, delegation, request_id))
        correlation = {
            "operation_id": operation["id"],
            "write_request_id": request_id,
            "tool": tool,
            "tenant_id": delegation.session.principal.tenant_id,
            "user_id": delegation.session.principal.user_id,
            "client_id": delegation.client_id,
        }
        emit("mcp_write_claimed", **correlation)
        # Disconnect/cancellation never restores the right to issue this write.
        try:
            response = await self.request(delegation, method, path, body=body, operation_id=operation["id"])
            value = response.json()
            checks = list(probes)
            if response.is_success and result_path is not None:
                checks.append(result_path(value))
            result = {"data": value, "http_status": response.status_code, "probes": checks}
            state = "succeeded" if response.is_success else "unknown" if response.status_code >= 500 else "failed"
            await asyncio.to_thread(self.store.settle_operation, operation["id"], state, result)
            emit("mcp_write_settled", outcome=state, **correlation)
        except BaseException:
            # The durable running claim already prevents a second effect. A late
            # response may still settle it; a crashed process remains unknown.
            emit("mcp_write_reply_lost", **correlation)
            raise
        return await self.operation_reply(delegation, await asyncio.to_thread(self.store.operation, delegation, request_id))

    async def propose(self, delegation: Delegation, scope: str, tool: str, request_id: str, method: str, path: str, *, page: str, request: dict, probes: list[str]) -> Reply:
        """A signing action (#348): recorded as the client's proposal; the person completes it on `page` in the Web, which
        shows what it does now. `request` is the tool's own input, kept to describe it again."""
        delegation.require(scope)
        await self.require_probes(delegation, probes)
        arguments = {"method": method, "path": path, "page": page, "request": request, "probes": probes}
        operation = await asyncio.to_thread(self.store.propose, delegation, request_id, tool, arguments, scope)
        return await self.operation_reply(delegation, operation)

    def intent_url(self, operation: dict) -> str:
        """The page, entered the way a Feishu card enters it: signed in and in the current edition."""
        web, page = self.store.settings.canonical_web_url().rstrip("/"), operation["arguments"]["page"]
        target = web + page + ("&" if "?" in page else "?") + "intent=" + operation["id"]
        return web + "/enter-current?" + urlencode({"return_to": target})

    async def operation_reply(self, delegation: Delegation, operation: dict) -> Reply:
        await self.require_probes(delegation, operation["arguments"].get("probes", []))
        state = operation["state"]
        if state == "proposed":
            # What the person did with it, as recorded (#362).
            outcome = await asyncio.to_thread(self.store.intent_outcome, operation["id"])
            rid = operation["request_id"]
            if outcome["state"] == "proposed":
                return Reply(status="confirmation_required", request_id=rid, confirmation_url=self.intent_url(operation))
            if outcome["state"] == "declined":
                return Reply(status="denied", error=outcome["reason"], request_id=rid)
            if outcome["state"] == "succeeded":
                return Reply(status="ok", request_id=rid, provenance={"client_id": delegation.client_id, "completed_at": outcome["at"]})
            if outcome["state"] == "failed":
                # Not done; the person may still complete it from the same link.
                return Reply(status="confirmation_required", error=outcome["error"], request_id=rid, confirmation_url=self.intent_url(operation))
            return Reply(status="unknown", request_id=rid)
        result: dict = operation.get("result") or {}
        if result:
            await self.require_probes(delegation, result.get("probes", []))
        if state in {"succeeded", "failed"} and "data" not in result:
            raise McpError("operation_receipt_unavailable", 503)
        if state == "succeeded":
            value = result["data"]
            if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
                # The receipt remains durable. A native read with a JSON Pointer
                # returns the requested portion; never silently truncate facts.
                return Reply(status="ok", data={"receipt_saved": True, "read_paths": result["probes"]}, request_id=operation["request_id"])
            return Reply(status="ok", data=value, request_id=operation["request_id"], provenance={"client_id": delegation.client_id})
        if state == "failed":
            value = result["data"]
            return Reply(status="error", error=value.get("error", "business_request_failed"), request_id=operation["request_id"])
        return Reply(status=state if state in {"running", "unknown"} else "running", request_id=operation["request_id"], retry_after_seconds=5 if state == "running" else None)
