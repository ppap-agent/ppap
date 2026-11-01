"""Tool recovery hints distinguish missing authorization from temporary failures."""

import asyncio
from types import SimpleNamespace

import pytest
from api.mcp_tools import register_tools
from fastmcp import Client, FastMCP
from product.mcp import McpError


@pytest.mark.parametrize("code,retry_after", [("share_directory_permission_missing", None), ("share_directory_user_permission_missing", None), ("share_directory_timeout", 5)])
def test_directory_permission_does_not_schedule_retries_and_self_remains_available(code, retry_after):
    calls = []

    async def authority():
        return object()

    async def get(actor, scope, path, params=None):
        calls.append((scope, path, params))
        if params["kind"] == "group":
            raise McpError(code, 503)
        return {"targets": [{"id": "ou_self", "eligibility": "eligible"}]}

    async def run():
        server = FastMCP("directory-recovery")
        register_tools(server, SimpleNamespace(get=get), authority)
        async with Client(server) as client:
            denied = await client.call_tool("list_share_targets", {"kind": "group"})
            assert denied.structured_content["error"] == code
            assert denied.structured_content["retry_after_seconds"] == retry_after
            own = await client.call_tool("list_share_targets", {})
            assert own.structured_content["status"] == "ok"
            assert own.structured_content["data"]["value"]["targets"][0]["id"] == "ou_self"

    asyncio.run(run())
    assert len(calls) == 2
