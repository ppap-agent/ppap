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
from product.mcp import MAX_RESULT_BYTES, ApprovedCall, Delegation, McpError, fingerprint
from product.store.mcp import McpStore
from pydantic import BaseModel, Field
from starlette.responses import Response

T = TypeVar("T")


class Reply(BaseModel, Generic[T]):
    status: Literal["ok", "error", "confirmation_required", "running", "unknown", "denied"]
    data: T | None = None
    error: str | None = None
    request_id: str | None = None
    confirmation_url: str | None = None
    retry_after_seconds: int | None = None
    provenance: dict | None = None


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

    def transfer_response(self, delegation: Delegation, entry: dict) -> Response:
        """Forward ASGI byte streams; the native file owner retains its size cap.

        There is no extra 200 MiB MCP buffer or httpx response aggregation.
        Stored parameters bind the artifact; query arguments on the transfer URL
        cannot select another file, report, or result attempt.
        """
        app = self.app

        class TransferResponse(Response):
            async def __call__(self, scope, receive, send):
                path = entry["path"]
                delegated = {
                    **scope,
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "root_path": "",
                    "query_string": urlencode(entry["parameters"]).encode("ascii"),
                    "headers": [(k, v) for k, v in scope["headers"] if k.lower() in {b"content-type", b"content-length", b"x-request-id"}],
                    "ppap.delegation": delegation,
                }

                async def forward(message):
                    if message["type"] == "http.response.start":
                        headers = [(k, v) for k, v in message["headers"] if k.lower() in {b"content-type", b"content-length", b"content-disposition", b"x-content-sha256"}]
                        message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
                    await send(message)

                await app(delegated, receive, forward)

        return TransferResponse()

    async def request(self, delegation: Delegation, method: str, path: str, *, body=None, params=None, content=None, operation_id=None, approval=None) -> httpx.Response:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise McpError("invalid_business_path")

        async def authorized(scope, receive, send):
            scope = {**scope, "ppap.delegation": delegation, "ppap.mcp_operation_id": operation_id, "ppap.mcp_approval": approval}
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

    async def write(
        self, delegation: Delegation, scope: str, tool: str, request_id: str, method: str, path: str, body: dict | None, *, probes: list[str], confirmation: dict | None = None, result_path=None
    ) -> Reply:
        delegation.require(scope)
        await self.require_probes(delegation, probes)
        arguments = {"method": method, "path": path, "body": body, "probes": probes}
        operation = await asyncio.to_thread(self.store.prepare_operation, delegation, request_id, tool, arguments, scope, confirmation)
        if operation["state"] == "confirmation_required":
            return Reply(status="confirmation_required", request_id=request_id, confirmation_url=self.store.settings.public_api_url.rstrip("/") + "/mcp/confirm/" + operation["id"])
        if operation["state"] != "ready":
            return await self.operation_reply(delegation, operation)
        if operation["confirmation"] is not None and fingerprint(operation["confirmation"]) != fingerprint(confirmation):
            raise McpError("confirmation_content_changed", 409)
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
            response = await self.request(delegation, method, path, body=body, operation_id=operation["id"], approval=ApprovedCall.from_operation(delegation.id, operation))
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

    async def operation_reply(self, delegation: Delegation, operation: dict) -> Reply:
        await self.require_probes(delegation, operation["arguments"].get("probes", []))
        result: dict = operation.get("result") or {}
        if result:
            await self.require_probes(delegation, result.get("probes", []))
        state = operation["state"]
        if state in {"succeeded", "failed"} and "data" not in result:
            raise McpError("operation_receipt_unavailable", 503)
        if state == "succeeded":
            value = result["data"]
            if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
                # The receipt remains durable. A native read with a JSON Pointer
                # returns the requested portion; never silently truncate facts.
                return Reply(status="ok", data={"receipt_saved": True, "read_paths": result["probes"]}, request_id=operation["request_id"])
            return Reply(
                status="ok",
                data=value,
                request_id=operation["request_id"],
                provenance={
                    "client_id": delegation.client_id,
                    "confirmed_by": operation["confirmed_by"],
                    "confirmed_at": operation["confirmed_at"],
                },
            )
        if state == "failed":
            value = result["data"]
            return Reply(status="error", error=value.get("error", "business_request_failed"), request_id=operation["request_id"])
        return Reply(
            status=state if state in {"confirmation_required", "running", "unknown", "denied"} else "running",
            request_id=operation["request_id"],
            retry_after_seconds=5 if state == "running" else None,
            confirmation_url=self.store.settings.public_api_url.rstrip("/") + "/mcp/confirm/" + operation["id"] if state == "confirmation_required" else None,
        )
