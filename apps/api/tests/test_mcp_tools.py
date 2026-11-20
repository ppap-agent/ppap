"""Tool-level contracts that need no database: recovery hints and replays that cannot drift."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.mcp_gateway import Reply
from api.mcp_tools import register_tools, step
from fastmcp import Client, FastMCP
from product.mcp import McpError
from product.settings import Settings


class Actor:
    def require(self, scope):
        pass


def run(gateway, tool, arguments):
    async def authority():
        return Actor()

    async def call():
        server = FastMCP("tools")
        register_tools(server, gateway, authority)
        async with Client(server) as client:
            return (await client.call_tool(tool, arguments)).structured_content

    return asyncio.run(call())


def gateway_with(*, recorded=None, task=None, writes=None, reads=None):
    def operation(actor, rid):
        if recorded is None:
            raise McpError("operation_not_found", 404)
        return recorded

    async def get(actor, scope, path, params=None):
        if reads is not None:
            reads.append((scope, path, params))
        return task

    async def write(actor, scope, tool, rid, method, path, body, *, probes, confirmation=None, result_path=None):
        writes.append({"tool": tool, "rid": rid, "method": method, "path": path, "body": body, "probes": probes, "confirmation": confirmation})
        return Reply(status="ok", data={}, request_id=rid)

    return SimpleNamespace(get=get, write=write, store=SimpleNamespace(settings=Settings(), operation=operation))


@pytest.mark.parametrize("code,retry_after", [("share_directory_permission_missing", None), ("share_directory_user_permission_missing", None), ("share_directory_timeout", 5)])
def test_directory_permission_does_not_schedule_retries_and_self_remains_available(code, retry_after):
    calls = []

    async def get(actor, scope, path, params=None):
        calls.append((scope, path, params))
        if params["kind"] == "group":
            raise McpError(code, 503)
        return {"targets": [{"id": "ou_self", "eligibility": "eligible"}]}

    gateway = SimpleNamespace(get=get, store=SimpleNamespace(settings=Settings()))
    denied = run(gateway, "find_recipients", {"kind": "group"})
    assert denied["error"] == code and denied["retry_after_seconds"] == retry_after
    own = run(gateway, "find_recipients", {})
    assert own["status"] == "ok" and own["data"]["value"]["targets"][0]["id"] == "ou_self"
    assert len(calls) == 2


def test_first_call_reads_the_current_revision_for_the_caller():
    writes = []
    task_id, rid = str(uuid4()), str(uuid4())
    reply = run(gateway_with(task={"input_revision": 3}, writes=writes), "set_deep_mode", {"task_id": task_id, "request_id": rid, "enabled": True})
    assert reply["status"] == "ok"
    assert writes == [
        {
            "tool": "set_deep_mode",
            "rid": rid,
            "method": "PUT",
            "path": f"/audit-tasks/{task_id}/deep-mode",
            "body": {"deep_mode": True, "expected_revision": 3},
            "probes": [f"/audit-tasks/{task_id}"],
            "confirmation": None,
        }
    ]


def test_replay_keeps_the_recorded_arguments_after_the_task_moved_on():
    # The confirmation changed the task's revision; replaying the same request must not become a request conflict.
    writes = []
    task_id, rid = str(uuid4()), str(uuid4())
    path, probes = f"/audit-tasks/{task_id}/confirm", [f"/audit-tasks/{task_id}"]
    body = {"request_id": rid, "expected_revision": 0, "expected_confirmation_basis_digest": "a" * 64}
    recorded = {"tool": "confirm_audit", "arguments": {"method": "POST", "path": path, "body": body, "probes": probes}}
    task = {"number": "T-1", "input_revision": 1, "confirmation_basis_digest": "b" * 64, "deep_mode": False, "files": [{"selection": "selected", "file": {"original_filename": "控制计划.xlsx"}}]}
    reply = run(gateway_with(recorded=recorded, task=task, writes=writes), "confirm_audit", {"task_id": task_id, "request_id": rid})
    assert reply["status"] == "ok"
    assert [(w["path"], w["body"], w["probes"]) for w in writes] == [(path, body, probes)]
    # What the person approves is what will happen, in their words, with no internal identifiers.
    summary = writes[0]["confirmation"]["summary"]
    assert summary[0] == "开始审核：任务 T-1，共 1 份文件。" and "· 控制计划.xlsx" in summary
    assert not any(task_id in line or rid in line for line in summary)


def test_each_effect_of_one_call_has_its_own_stable_request_id():
    rid = uuid4()
    assert step(rid, "validate") == step(str(rid), "validate") != step(rid, "finalize")


def test_replay_recomputes_the_callers_own_arguments_so_a_change_stays_a_conflict():
    writes = []
    task_id, rid = str(uuid4()), str(uuid4())
    recorded = {"tool": "set_deep_mode", "arguments": {"body": {"deep_mode": True, "expected_revision": 3}}}
    run(gateway_with(recorded=recorded, task={"input_revision": 9}, writes=writes), "set_deep_mode", {"task_id": task_id, "request_id": rid, "enabled": False})
    # The revision the tool read stays as recorded; the caller's changed choice reaches the receipt, which refuses it.
    assert writes[0]["body"] == {"deep_mode": False, "expected_revision": 3}
