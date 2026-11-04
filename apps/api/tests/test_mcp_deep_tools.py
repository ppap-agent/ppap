"""Deep uses the existing SDK, confirmation gateway and Product API."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.mcp_gateway import Reply
from api.mcp_tools import register_tools
from fastmcp import Client, FastMCP
from product.mcp import McpError


@pytest.mark.parametrize("intent", ["save_only", "save_and_analyze"])
def test_rule_change_uses_shared_confirmation_and_exact_business_receipt(intent):
    calls = []
    required = []
    rid, operation_id = str(uuid4()), str(uuid4())
    body = {"operation_id": operation_id, "expected_revision": 2, "intent": intent, "content": "Use column B", "kind": "mapping", "scope": {"document_ids": ["file"], "rule_ids": ["R1"]}}

    async def authority():
        return SimpleNamespace(require=required.append)

    async def get(actor, scope, path, params=None):
        calls.append(("get", path))
        return {"revision": 2}

    async def write(actor, scope, name, request_id, method, path, payload, **kwargs):
        calls.append(("write", scope, name, request_id, path, payload, kwargs))
        return Reply(status="confirmation_required", request_id=request_id, confirmation_url="https://ppap.example/mcp/confirm/operation")

    async def run():
        server = FastMCP("deep")
        register_tools(server, SimpleNamespace(get=get, write=write), authority)
        async with Client(server) as client:
            reply = await client.call_tool("apply_rule_change", {"round_id": rid, "body": body})
            assert reply.structured_content["status"] == "confirmation_required"
            assert reply.structured_content["request_id"] == operation_id
            await client.call_tool("read_audit", {"round_id": rid})
            await client.call_tool("get_rule_change", {"round_id": rid, "operation_id": operation_id})

    asyncio.run(run())
    call = next(c for c in calls if c[0] == "write")
    assert "rules:write" in required and "audits:read" in required
    assert call[1] == ("audits:execute" if intent == "save_and_analyze" else "audits:write")
    assert call[3] == operation_id
    assert call[4] == f"/audit-rounds/{rid}/rule-changes"
    assert call[5]["content"] == body["content"]
    assert call[6]["confirmation"]["sources"] == [{"revision": 2}]
    assert call[6]["result_path"]({}) == f"/audit-rounds/{rid}/rule-changes/{operation_id}"
    assert calls[-1] == ("get", f"/audit-rounds/{rid}/rule-changes/{operation_id}")


def test_start_analysis_requires_execute_but_not_rule_write():
    required, calls = [], []

    async def authority():
        return SimpleNamespace(require=required.append)

    async def get(*args, **kwargs):
        return {"revision": 4}

    async def write(*args, **kwargs):
        calls.append((args, kwargs))
        return Reply(status="confirmation_required")

    async def run():
        server = FastMCP("deep")
        register_tools(server, SimpleNamespace(get=get, write=write), authority)
        async with Client(server) as client:
            response = await client.call_tool("start_analysis", {"round_id": str(uuid4()), "operation_id": str(uuid4()), "expected_revision": 4})
            assert response.structured_content["status"] == "confirmation_required"

    asyncio.run(run())
    assert "audits:execute" in required
    assert "rules:write" not in required
    assert calls[0][0][6]["intent"] == "save_and_analyze"
    assert calls[0][1]["confirmation"] is not None


def test_missing_rule_scope_never_reaches_gateway():
    async def authority():
        def require(scope):
            if scope == "rules:write":
                raise McpError("insufficient_scope", 403)

        return SimpleNamespace(require=require)

    async def run():
        server = FastMCP("deep")
        register_tools(server, SimpleNamespace(), authority)
        async with Client(server) as client:
            response = await client.call_tool(
                "apply_rule_change", {"round_id": str(uuid4()), "body": {"operation_id": str(uuid4()), "expected_revision": 0, "intent": "save_only", "content": "change"}}
            )
            assert response.structured_content["error"] == "insufficient_scope"

    asyncio.run(run())


def test_business_route_rechecks_approved_source_after_gateway_read():
    from dataclasses import replace

    from api.deep_feedback import register_deep_feedback_routes
    from api.mcp_gateway import BusinessGateway
    from fastapi import FastAPI
    from product.mcp import ApprovedCall, Delegation, fingerprint

    principal = object()
    delegation = Delegation("grant", "client", frozenset({"audits:read", "audits:execute", "rules:write"}), SimpleNamespace(principal=principal))
    rid = str(uuid4())
    path = f"/audit-rounds/{rid}/rule-changes"
    view = {"round_id": rid, "workspace_id": rid, "current_round_id": rid, "revision": 3, "input_changed": False, "appendix": [], "clarifications": [], "operations": [], "results": []}
    applied = []
    store = SimpleNamespace(
        settings=SimpleNamespace(usage_charge_enabled=False),
        read_deep_feedback=lambda *_: view,
        apply_rule_change=lambda *args, **kwargs: applied.append(args),
    )
    app = FastAPI()
    register_deep_feedback_routes(app, store, lambda _: principal, None)
    gateway = BusinessGateway(app, None)
    body = {"operation_id": str(uuid4()), "expected_revision": 3, "intent": "save_and_analyze", "content": "change"}
    proof = ApprovedCall("grant", "operation", "POST", path, ((f"/audit-rounds/{rid}/deep-feedback", fingerprint(view)),))

    async def run():
        missing = await gateway.request(delegation, "POST", path, body=body, operation_id="operation")
        assert missing.status_code == 403 and missing.json()["error"] == "invalid_confirmation"
        denied = await gateway.request(replace(delegation, scopes=frozenset({"audits:read", "audits:execute"})), "POST", path, body=body, operation_id="operation", approval=proof)
        assert denied.status_code == 403 and denied.json()["error"] == "insufficient_scope"
        view["revision"] = 4
        changed = await gateway.request(delegation, "POST", path, body=body, operation_id="operation", approval=proof)
        assert changed.status_code == 409 and changed.json()["error"] == "confirmation_content_changed"

    asyncio.run(run())
    assert applied == []
