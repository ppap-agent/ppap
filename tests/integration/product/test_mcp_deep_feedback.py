"""MCP 2026 wire -> real application, native PG and semantic guest."""

import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastmcp import Client
from ppap_core.db import connect
from psycopg.types.json import Jsonb

from .conftest import change_payload

PREFIX = "io.modelcontextprotocol/"
TASKS = "io.modelcontextprotocol/tasks"
VERSION = "2026-07-28"


def rpc(env, token, method, params=None, capabilities=None, rpc_id=None):
    return env["mcp_rpc"](token, method, params, capabilities, rpc_id)


def access(env):
    return env["mcp_access"]()


def record_application_calls(env, monkeypatch):
    """Observe the shared action; its real PG/queue implementation still runs."""
    original = env["store"].apply_rule_change
    calls = []

    def apply(principal, round_id, change, **kwargs):
        calls.append((principal, round_id, change))
        return original(principal, round_id, change, **kwargs)

    assert env["app"].state.mcp_deep_adapter.product is env["store"]
    monkeypatch.setattr(env["store"], "apply_rule_change", apply)
    return calls


def test_native_mrtr_confirmation_uses_same_application_and_recomputes(deep_feedback_env, monkeypatch):
    env = deep_feedback_env
    applications = record_application_calls(env, monkeypatch)
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    payload = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": payload}}
    discovery = rpc(env, token, "server/discover")["result"]
    assert discovery["supportedVersions"] == [VERSION]
    assert discovery["resultType"] == "complete"
    assert discovery["ttlMs"] == 0 and discovery["cacheScope"] == "private"
    assert discovery["_meta"][PREFIX + "serverInfo"]["name"] == "PPAP Deep"
    pending = rpc(env, token, "tools/call", args, rpc_id=1)["result"]
    assert pending["resultType"] == "input_required"
    key = next(iter(pending["inputRequests"]))
    assert pending["inputRequests"][key]["method"] == "elicitation/create"
    message = pending["inputRequests"][key]["params"]["message"]
    assert all(value in message for value in ("按工序编号理解", payload["content"], "原文依据：CP.xlsx", "A1", "原文：作业单元编号", "R1", "R2", "全部待审资料重新分析")), message
    assert "password" not in pending["inputRequests"][key]["params"]["requestedSchema"]["properties"]
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
    accepted = rpc(env, token, "tools/call", {**args, "requestState": pending["requestState"], "inputResponses": {key: {"action": "accept", "content": {"content": payload["content"]}}}}, rpc_id=2)
    assert "error" not in accepted, accepted
    calls = [message for message in env["wire_history"] if message["method"] == "tools/call"]
    assert calls[-2]["id"] != calls[-1]["id"]
    assert calls[-2]["params"]["arguments"]["change"]["operation_id"] == calls[-1]["params"]["arguments"]["change"]["operation_id"]
    receipt = accepted["result"]["structuredContent"]
    env["run_jobs"]()
    result = env["client"].get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [r["status"] for r in result["findings"]] == ["PASS", "NG", "PASS"]
    replay = rpc(env, token, "tools/call", args, rpc_id=3)["result"]["structuredContent"]
    assert replay == receipt
    assert env["client"].get("/audit-rounds/" + env["round_id"] + "/deep-feedback").json()["revision"] == 1
    native = rpc(env, token, "tools/call", {"name": "read_audit", "arguments": {"round_id": env["round_id"]}})["result"]["structuredContent"]
    assert native == env["wire_client"].get("/audit-rounds/" + env["round_id"] + "/deep-feedback").json()
    assert native["appendix"][0]["confirmation_source"] == "synthetic"
    assert len(applications) == 1
    assert applications[0][0] == env["principal"] and applications[0][1] == env["round_id"]
    assert applications[0][2]["operation_id"] == payload["operation_id"]


def test_native_task_update_and_capability_fallback(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    payload = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": payload}}
    unsupported = rpc(env, token, "tools/call", args, capabilities={})["result"]
    assert unsupported["structuredContent"]["applied"] is False
    caps = {"elicitation": {"form": {}}, "extensions": {TASKS: {}}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    assert pending["resultType"] == "task" and pending["status"] == "input_required"
    task_id = pending["taskId"]
    read = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    key = next(iter(read["inputRequests"]))
    no = rpc(env, token, "tasks/update", {"taskId": task_id, "inputResponses": {key: {"action": "decline"}}}, capabilities=caps)
    assert "error" not in no
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
    renewed = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    new_key = next(iter(renewed["inputRequests"]))
    assert new_key != key
    # A closed interaction's key cannot answer the new interaction.
    stale = {"taskId": task_id, "inputResponses": {key: {"action": "accept", "content": {"content": payload["content"]}}}}
    assert "error" not in rpc(env, token, "tasks/update", stale, capabilities=caps)
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
    key = new_key
    update = {"taskId": task_id, "inputResponses": {key: {"action": "accept", "content": {"content": payload["content"]}}}}
    assert "error" not in rpc(env, token, "tasks/update", update, capabilities=caps)
    assert "error" not in rpc(env, token, "tasks/update", update, capabilities=caps)
    done = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert done["status"] == "completed"
    assert done["result"]["structuredContent"]["receipt"]["analysis_round_id"] is None


def test_url_accept_does_not_apply_and_web_commits_same_operation(deep_feedback_env, monkeypatch):
    env = deep_feedback_env
    applications = record_application_calls(env, monkeypatch)
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    caps = {"elicitation": {"url": {}}, "extensions": {TASKS: {}}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    task_id = pending["taskId"]
    key, question = next(iter(pending["inputRequests"].items()))
    assert "mcpRuleChange=" + task_id in question["params"]["url"]
    assert "error" not in rpc(env, token, "tasks/update", {"taskId": task_id, "inputResponses": {key: {"action": "accept"}}}, capabilities=caps)
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
    assert applications == []
    proposal = env["wire_client"].get(f"/audit-rounds/{env['round_id']}/mcp-rule-changes/{task_id}")
    assert proposal.status_code == 200, proposal.text
    web_change = proposal.json()["change"]
    assert web_change["operation_id"] == change["operation_id"]
    applied = env["wire_client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=web_change)
    assert applied.status_code == 200, applied.text
    env["run_jobs"]()
    done = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert done["status"] == "completed"
    assert done["result"]["structuredContent"]["receipt"] == applied.json()
    assert [finding["status"] for finding in done["result"]["structuredContent"]["audit"]["results"][-1]["findings"]] == ["PASS", "NG", "PASS"]
    assert set(parse_qs(urlsplit(question["params"]["url"]).query)) == {"round", "mcpRuleChange"}
    assert len(applications) == 1 and applications[0][2]["operation_id"] == change["operation_id"]


def test_cancelled_task_and_expired_confirmation_cannot_apply(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    caps = {"elicitation": {"form": {}}, "extensions": {TASKS: {}}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    key = next(iter(pending["inputRequests"]))
    assert "error" not in rpc(env, token, "tasks/cancel", {"taskId": pending["taskId"]}, capabilities=caps)
    denied = rpc(env, token, "tasks/update", {"taskId": pending["taskId"], "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}}, capabilities=caps)
    assert denied["error"]["message"] == "task_cancelled"
    change["operation_id"] = str(uuid4())
    expiry = rpc(env, token, "tools/call", args)["result"]
    with env["store"]._conn() as conn:
        conn.execute("UPDATE product.mcp_operations SET created_at=now()-interval '2 hours' WHERE id=%s", (expiry["requestState"],))
    key = next(iter(expiry["inputRequests"]))
    expired = rpc(env, token, "tools/call", {**args, "requestState": expiry["requestState"], "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}})
    assert expired["error"]["message"] == "confirmation_expired"
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0


def test_wire_rejects_header_mismatch_host_origin_and_revoked_grant(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    metadata = {PREFIX + "protocolVersion": VERSION, PREFIX + "clientInfo": {"name": "test", "version": "1"}, PREFIX + "clientCapabilities": {}}
    message = {"jsonrpc": "2.0", "id": 9, "method": "server/discover", "params": {"_meta": metadata}}
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION, "Mcp-Method": "server/discover"}
    mismatched = env["wire_client"].post("/mcp/deep", headers={**headers, "Mcp-Method": "tools/list"}, json=message)
    assert mismatched.status_code == 400 and mismatched.json()["error"]["code"] == -32020
    origin = env["wire_client"].post("/mcp/deep", headers={**headers, "Origin": "https://untrusted.example"}, json=message)
    assert origin.status_code == 403
    malformed = env["wire_client"].post("/mcp/deep", headers=headers, json={**message, "params": []})
    assert malformed.status_code == 400 and malformed.json()["error"]["message"] == "missing_request_metadata"
    with env["store"]._conn() as conn:
        conn.execute("UPDATE product.mcp_grants SET revoked_at=now()")
    revoked = env["wire_client"].post("/mcp/deep", headers=headers, json=message)
    assert revoked.status_code == 401


def test_explicit_start_accepts_without_optional_request_state(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    args = {"name": "start_analysis", "arguments": {"round_id": env["round_id"], "operation_id": str(uuid4()), "expected_revision": 0}}
    waiting = rpc(env, token, "tools/call", args)["result"]
    key = next(iter(waiting["inputRequests"]))
    result = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": "accept", "content": {"confirm": True}}}}, rpc_id=2)
    assert "error" not in result, result
    saved = result["result"]["structuredContent"]
    assert saved["analysis_round_id"] and saved["appendix"] == []
    env["run_jobs"]()
    same_unknown = env["client"].get("/rounds/" + saved["analysis_round_id"]).json()
    assert [r["status"] for r in same_unknown["findings"]] == ["UNABLE", "UNABLE", "PASS"]


def test_discovered_schema_answers_and_expired_task_have_recoverable_contract(deep_feedback_env):
    import jsonschema

    env = deep_feedback_env
    token = access(env)
    tools = rpc(env, token, "tools/list")["result"]["tools"]
    schema = next(t["inputSchema"] for t in tools if t["name"] == "apply_rule_change")
    assert {"operation_id", "expected_revision", "intent"} <= set(schema["properties"]["change"]["required"])
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"round_id": env["round_id"], "change": change}
    jsonschema.validate(args, schema)
    caps = {"elicitation": {"form": {}}, "extensions": {TASKS: {}}}
    pending = rpc(env, token, "tools/call", {"name": "apply_rule_change", "arguments": args}, capabilities=caps)["result"]
    key = next(iter(pending["inputRequests"]))
    malformed = rpc(env, token, "tasks/update", {"taskId": pending["taskId"], "inputResponses": {key: []}}, capabilities=caps)
    assert malformed["error"]["message"] == "invalid_input_response"
    with env["store"]._conn() as conn:
        conn.execute("UPDATE product.mcp_operations SET created_at=now()-interval '2 hours' WHERE id=%s", (pending["taskId"],))
    expired = rpc(env, token, "tasks/get", {"taskId": pending["taskId"]}, capabilities=caps)["result"]
    assert expired["status"] == "failed" and expired["error"]["message"] == "confirmation_expired"
    assert "workspace_url" in expired["error"]["data"]


@pytest.mark.parametrize("saved_in_web", [False, True])
def test_expired_plain_mrtr_replay_reads_saved_receipt_or_returns_workspace(deep_feedback_env, saved_in_web):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args)["result"]
    assert pending["resultType"] == "input_required"
    receipt = None
    if saved_in_web:
        saved = env["client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=change)
        assert saved.status_code == 200, saved.text
        receipt = saved.json()
    with env["store"]._conn() as conn:
        conn.execute("UPDATE product.mcp_operations SET created_at=now()-interval '2 hours' WHERE id=%s AND state='ready'", (pending["requestState"],))
    for _ in range(2):
        replay = rpc(env, token, "tools/call", args)["result"]
        assert replay["resultType"] == "complete"
        assert "inputRequests" not in replay
        if receipt:
            assert replay["isError"] is False
            assert replay["structuredContent"] == receipt
        else:
            assert replay["isError"] is True
            assert replay["structuredContent"] == {
                "applied": False,
                "reason": "confirmation_expired",
                "workspace_url": env["store"].settings.canonical_web_url() + "/audit?round=" + view["current_round_id"],
            }
    after = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert after["revision"] == int(saved_in_web)
    assert len(after["operations"]) == int(saved_in_web)
    assert len(after["results"]) == 1


def test_removing_explanation_displays_its_content_scope_and_effect(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    applied = env["client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=change)
    assert applied.status_code == 200, applied.text
    remove = {"operation_id": str(uuid4()), "expected_revision": 1, "remove_appendix_ids": [change["operation_id"]], "intent": "save_only"}
    pending = rpc(env, token, "tools/call", {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": remove}})["result"]
    message = next(iter(pending["inputRequests"].values()))["params"]["message"]
    assert change["content"] in message and "CP.xlsx" in message and "不开始新的分析" in message
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 1


def test_form_applies_only_submitted_content_and_rejects_changed_replay(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    # Tool arguments do not constitute a server-correlated response.
    ordinary = rpc(env, token, "tools/call", {**args, "arguments": {**args["arguments"], "inputResponses": {"accept": True}}})["result"]
    assert ordinary["resultType"] == "input_required"
    key = next(iter(ordinary["inputRequests"]))
    edited = change["content"] + " 此说明只用于本文件。"
    answer = {key: {"action": "accept", "content": {"content": edited}}}
    broadened = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": "accept", "content": {"content": edited, "scope": {"document_ids": [env["uploaded"]["PFD"]["id"]]}}}}})
    assert broadened["error"]["message"] == "invalid_input_response"
    accepted = rpc(env, token, "tools/call", {**args, "inputResponses": answer})["result"]["structuredContent"]
    entry = accepted["appendix"][0]
    assert entry["content"] == edited and "candidate_id" not in entry
    assert entry["scope"]["document_ids"] == [env["uploaded"]["CP"]["id"]]
    assert accepted["analysis_round_id"] is None
    assert rpc(env, token, "tools/call", {**args, "inputResponses": answer})["result"]["structuredContent"] == accepted
    conflict = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}})
    assert conflict["error"]["message"] == "idempotency_conflict"
    assert len(env["store"].read_deep_feedback(env["principal"], env["round_id"])["operations"]) == 1


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_mrtr_close_preserves_unknown_and_renews_the_question_key(deep_feedback_env, action):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    first = rpc(env, token, "tools/call", args)["result"]
    key = next(iter(first["inputRequests"]))
    declined = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": action}}})["result"]
    assert declined["structuredContent"]["applied"] is False
    reopened = rpc(env, token, "tools/call", args)["result"]
    assert next(iter(reopened["inputRequests"])) != key
    stale = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}})
    assert stale["error"]["message"] == "invalid_input_response"
    after = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert after["revision"] == 0 and after["appendix"] == [] and len(after["results"]) == 1
    assert [finding["status"] for finding in after["results"][0]["findings"]] == ["UNABLE", "UNABLE", "PASS"]


def test_request_state_cannot_change_operation_or_caller(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args)["result"]
    key = next(iter(pending["inputRequests"]))
    answer = {key: {"action": "accept", "content": {"content": change["content"]}}}
    forged = rpc(env, token, "tools/call", {**args, "requestState": str(uuid4()), "inputResponses": answer})
    assert forged["error"]["message"] == "invalid_request_state"
    alternate = {**args, "arguments": {**args["arguments"], "change": {**change, "operation_id": str(uuid4())}}}
    rebound = rpc(env, token, "tools/call", {**alternate, "requestState": pending["requestState"], "inputResponses": answer})
    assert rebound["error"]["message"] == "invalid_request_state"
    # A second grant, even for the same user, cannot adopt the first grant's
    # requestState or server question key.
    second = access(env)
    foreign = rpc(env, second, "tools/call", {**args, "requestState": pending["requestState"], "inputResponses": answer})
    assert foreign["error"]["message"] == "invalid_request_state"
    cross_user = env["mcp_access"](person="bob")
    caps = {"extensions": {TASKS: {}}}
    denied = rpc(env, cross_user, "tasks/get", {"taskId": pending["requestState"]}, capabilities=caps)
    assert denied["error"]["message"] == "operation_not_found"
    cross_tenant = env["mcp_access"](person="carol")
    metadata = {PREFIX + "protocolVersion": VERSION, PREFIX + "clientCapabilities": {}}
    denied_read = env["wire_client"].post(
        "/mcp/deep",
        headers={"Authorization": "Bearer " + cross_tenant, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION, "Mcp-Method": "tools/call", "Mcp-Name": "read_audit"},
        json={"jsonrpc": "2.0", "id": "cross-tenant", "method": "tools/call", "params": {"name": "read_audit", "arguments": {"round_id": env["round_id"]}, "_meta": metadata}},
    )
    assert denied_read.status_code == 403 and denied_read.json()["error"]["message"] == "access_denied"
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0


def test_task_reconnect_api_restart_lost_reply_and_cancel_receipts(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    caps = {"elicitation": {"form": {}}, "extensions": {TASKS: {}}}
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    task_id = pending["taskId"]
    key = next(iter(pending["inputRequests"]))
    old_app = env["app"]
    old_http = env["wire_client"]
    env["restart_wire"]()
    assert env["app"] is not old_app and env["wire_client"] is not old_http
    restored = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert restored["inputRequests"] == pending["inputRequests"]
    update = {"taskId": task_id, "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}}
    # Deliberately discard an actual HTTP response after the server commits.
    # Client recovery has only the stable Task and operation identities.
    rpc(env, token, "tasks/update", update, capabilities=caps)
    env["restart_wire"]()
    ack = rpc(env, token, "tasks/update", update, capabilities=caps)["result"]
    assert {k: v for k, v in ack.items() if k != "_meta"} == {"resultType": "complete"}
    working = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert working["status"] == "working" and "result" not in working
    cancelled = rpc(env, token, "tasks/cancel", {"taskId": task_id}, capabilities=caps)["result"]
    assert {k: v for k, v in cancelled.items() if k != "_meta"} == {"resultType": "complete"}
    # Cancellation acknowledges intent; an accepted shared calculation is not
    # falsely reported stopped by a protocol receipt.
    assert rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]["status"] == "working"
    env["run_jobs"]()
    done = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert done["status"] == "completed"
    record = done["result"]["structuredContent"]
    assert len(record["audit"]["operations"]) == 1 and len(record["audit"]["results"]) == 2
    assert record["receipt"]["analysis_round_id"] == record["audit"]["current_round_id"]
    assert [finding["status"] for finding in record["audit"]["results"][-1]["findings"]] == ["PASS", "NG", "PASS"]
    assert all(result["inputs"] for result in record["audit"]["results"])
    assert "error" not in rpc(env, token, "tasks/cancel", {"taskId": task_id}, capabilities=caps)
    assert "error" not in rpc(env, token, "tasks/cancel", {"taskId": task_id}, capabilities=caps)


@pytest.mark.parametrize("elicitation", [None, {}, {"form": {}}, {"url": {}}, {"form": {}, "url": {}}])
@pytest.mark.parametrize("tasks", [False, True])
def test_each_request_uses_its_own_capabilities_and_web_fallback(deep_feedback_env, elicitation, tasks):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    caps = {}
    if elicitation is not None:
        caps["elicitation"] = elicitation
    if tasks:
        caps["extensions"] = {TASKS: {}}
    # A previous capability declaration never grants the next request a mode.
    rpc(env, token, "server/discover", capabilities={"elicitation": {"form": {}, "url": {}}, "extensions": {TASKS: {}}})
    result = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    assert (result["resultType"] == "task") is tasks
    if elicitation is None:
        assert "inputRequests" not in result
        if tasks:
            assert result["status"] == "working" and "工作区" in result["statusMessage"]
        else:
            assert result["structuredContent"]["workspace_url"].endswith("/audit?round=" + env["round_id"])
    else:
        question = next(iter(result["inputRequests"].values()))
        assert question["params"]["mode"] == ("url" if elicitation == {"url": {}} else "form")
    assert "io.modelcontextprotocol/ui" not in caps.get("extensions", {})
    audit = rpc(env, token, "tools/call", {"name": "read_audit", "arguments": {"round_id": env["round_id"]}}, capabilities={})["result"]["structuredContent"]
    assert audit == view


def test_wire_metadata_validation_and_no_cached_capability_authority(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    client = env["wire_client"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION, "Mcp-Method": "server/discover"}
    metadata = {PREFIX + "protocolVersion": VERSION, PREFIX + "clientCapabilities": {}}
    message = {"jsonrpc": "2.0", "id": "independent-client-1", "method": "server/discover", "params": {"_meta": metadata}}
    discovered = client.post("/mcp/deep", headers=headers, json=message)
    assert discovered.status_code == 200
    assert discovered.json()["result"]["supportedVersions"] == [VERSION]
    # clientInfo is optional in the final target specification.
    assert discovered.json()["result"]["resultType"] == "complete"
    for field in (PREFIX + "protocolVersion", PREFIX + "clientCapabilities"):
        invalid = {key: value for key, value in metadata.items() if key != field}
        rejected = client.post("/mcp/deep", headers=headers, json={**message, "params": {"_meta": invalid}})
        assert rejected.status_code == 400 and rejected.json()["error"]["code"] == -32602
    unsupported = client.post("/mcp/deep", headers={**headers, "MCP-Protocol-Version": "1900-01-01"}, json={**message, "params": {"_meta": {**metadata, PREFIX + "protocolVersion": "1900-01-01"}}})
    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["data"] == {"supported": [VERSION], "requested": "1900-01-01"}
    unknown = client.post("/mcp/deep", headers={**headers, "Mcp-Method": "unknown/method"}, json={**message, "method": "unknown/method"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == -32601
    host = client.post("/mcp/deep", headers={**headers, "Host": "untrusted.example"}, json=message)
    assert host.status_code == 421
    task_id = str(uuid4())
    missing_task_caps = client.post(
        "/mcp/deep",
        headers={**headers, "Mcp-Method": "tasks/get", "Mcp-Name": task_id},
        json={**message, "method": "tasks/get", "params": {"taskId": task_id, "_meta": metadata}},
    )
    assert missing_task_caps.status_code == 400 and missing_task_caps.json()["error"]["code"] == -32021


@pytest.mark.parametrize("interference", ["question_changed", "object_revoked", "scope_expanded", "delegation_scope_revoked"])
def test_native_confirmation_rechecks_shared_application_authority(deep_feedback_env, interference):
    env = deep_feedback_env
    token = access(env)
    round_id = env["round_id"]
    if interference == "object_revoked":
        # A shared application calculation has explicit object grants. The
        # fixture's initial legacy round intentionally uses tenant visibility.
        accepted = env["client"].post(
            f"/audit-rounds/{round_id}/rule-changes",
            json={
                "operation_id": str(uuid4()),
                "expected_revision": 0,
                "intent": "save_and_analyze",
                "confirmation_source": "synthetic",
            },
        )
        assert accepted.status_code == 200, accepted.text
        round_id = accepted.json()["analysis_round_id"]
        env["run_jobs"]()
    view = env["store"].read_deep_feedback(env["principal"], round_id)
    before = len(view["operations"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    if interference == "scope_expanded":
        change["scope"]["document_ids"].append(env["uploaded"]["PFD"]["id"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": round_id, "change": change}}
    pending = rpc(env, token, "tools/call", args)["result"]
    key = next(iter(pending["inputRequests"]))
    if interference == "question_changed":
        with env["store"]._conn() as conn:
            coverage = conn.execute("SELECT coverage FROM product.audit_rounds WHERE id=%s", (env["round_id"],)).fetchone()[0]
            coverage["clarifications"][0]["candidates"][0]["explanation"] += " 新内容"
            conn.execute("UPDATE product.audit_rounds SET coverage=%s WHERE id=%s", (Jsonb(coverage), env["round_id"]))
    elif interference == "object_revoked":
        # Revocation is an administrative fixture action. The API role must
        # retain its production restriction against deleting object grants.
        with connect(env["settings"], "migrator") as conn:
            conn.execute("DELETE FROM product.resource_grants WHERE resource_id=%s AND user_id=%s", (round_id, env["principal"].user_id))
    elif interference == "delegation_scope_revoked":
        with env["store"]._conn() as conn:
            conn.execute("UPDATE product.mcp_grants SET scopes=array_remove(scopes,'rules:write')")
    response = env["wire_client"].post(
        "/mcp/deep",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION, "Mcp-Method": "tools/call", "Mcp-Name": "apply_rule_change"},
        json={
            "jsonrpc": "2.0",
            "id": 999,
            "method": "tools/call",
            "params": {
                **args,
                "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}},
                "_meta": {PREFIX + "protocolVersion": VERSION, PREFIX + "clientCapabilities": {"elicitation": {"form": {}}}},
            },
        },
    )
    assert response.json()["error"]["message"] in {"invalid_input_response", "access_denied", "feedback_scope_conflict", "insufficient_scope"}
    with env["store"]._conn() as conn:
        assert conn.execute("SELECT count(*) FROM product.deep_rule_changes").fetchone()[0] == before


@pytest.mark.asyncio
async def test_legacy_sdk_endpoint_reads_the_same_deep_round(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    async with Client(env["wire_url"] + "/mcp", auth=token, timeout=30) as legacy:
        result = await legacy.call_tool("get_round", {"round_id": env["round_id"]})
        assert result.structured_content["status"] == "ok"
        record = result.structured_content["data"]["value"]
        assert [finding["status"] for finding in record["findings"]] == ["UNABLE", "UNABLE", "PASS"]
        assert record["id"] == env["round_id"]
    modern = rpc(env, token, "tools/call", {"name": "read_audit", "arguments": {"round_id": env["round_id"]}})["result"]["structuredContent"]
    assert modern["results"][0]["findings"] == record["findings"]
    assert modern["appendix"] == []


@pytest.mark.parametrize("phase", ["after_claim", "after_business_commit"])
@pytest.mark.parametrize("channel", ["mrtr", "task"])
def test_original_answer_recovers_after_real_commit_before_protocol_receipt(deep_feedback_env, monkeypatch, phase, channel):
    env = deep_feedback_env
    token = access(env)
    caps = {"elicitation": {"form": {}}}
    if channel == "task":
        caps["extensions"] = {TASKS: {}}
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    key = next(iter(pending["inputRequests"]))
    task_id = pending.get("taskId") or pending["requestState"]
    answer = {key: {"action": "accept", "content": {"content": change["content"]}}}
    method = "tasks/update" if channel == "task" else "tools/call"
    params = {"taskId": task_id, "inputResponses": answer} if channel == "task" else {**args, "requestState": task_id, "inputResponses": answer}
    adapter = env["app"].state.mcp_deep_adapter
    original = adapter.transport.claim_operation if phase == "after_claim" else adapter.product.apply_rule_change

    def interrupt_after_commit(*arguments, **kwargs):
        original(*arguments, **kwargs)
        # A failure after the real transaction commit leaves the next layer's
        # receipt unwritten. No parser, evaluator or business result is mocked.
        raise RuntimeError("synthetic process interruption at " + phase)

    with monkeypatch.context() as fault:
        owner, name = (adapter.transport, "claim_operation") if phase == "after_claim" else (adapter.product, "apply_rule_change")
        fault.setattr(owner, name, interrupt_after_commit)
        response = env["wire_client"].post(
            "/mcp/deep",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": VERSION,
                "Mcp-Method": method,
                "Mcp-Name": task_id if channel == "task" else "apply_rule_change",
            },
            json={
                "jsonrpc": "2.0",
                "id": "interrupted-submit",
                "method": method,
                "params": {
                    **params,
                    "_meta": {PREFIX + "protocolVersion": VERSION, PREFIX + "clientCapabilities": caps},
                },
            },
        )
        assert response.status_code == 500
    with env["store"]._conn() as conn:
        state, recorded = conn.execute("SELECT state,result FROM product.mcp_operations WHERE id=%s", (task_id,)).fetchone()
        assert state == "running" and recorded["input_responses"] == answer and "receipt" not in recorded
    partial = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert len(partial["operations"]) == int(phase == "after_business_commit")
    old_app = env["app"]
    old_client = env["wire_client"]
    env["restart_wire"]()
    assert env["app"] is not old_app and env["wire_client"] is not old_client
    if phase == "after_claim":
        if channel == "task":
            status = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
            assert status["status"] == "working" and "inputRequests" not in status and "result" not in status
        else:
            again = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
            assert next(iter(again["inputRequests"])) == key
    changed_answer = {key: {"action": "accept", "content": {"content": change["content"] + " 不同的测试编辑。"}}}
    conflict = rpc(env, token, method, {**params, "inputResponses": changed_answer}, capabilities=caps)
    assert conflict["error"]["message"] == "idempotency_conflict"
    recovered = rpc(env, token, method, params, capabilities=caps)
    assert "error" not in recovered, recovered
    receipt = env["store"].read_rule_change(env["principal"], env["round_id"], change["operation_id"])
    assert receipt and receipt["operation_id"] == change["operation_id"]
    if channel == "mrtr":
        assert recovered["result"]["structuredContent"] == receipt
    after = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert len(after["operations"]) == 1 and len(after["results"]) == 2
    assert len(after["appendix"]) == 1 and after["appendix"][0]["content"] == change["content"]
    assert after["current_round_id"] == receipt["analysis_round_id"]
    assert "error" not in rpc(env, token, method, params, capabilities=caps)
    env["run_jobs"]()
    actual = env["client"].get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [finding["status"] for finding in actual["findings"]] == ["PASS", "NG", "PASS"]
    with env["store"]._conn() as conn:
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE audit_round_id=%s AND kind='audit'", (receipt["analysis_round_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT result FROM product.mcp_operations WHERE id=%s", (task_id,)).fetchone()[0]["receipt"] == receipt


def test_first_accepted_response_is_frozen_before_claim_and_cannot_be_overwritten(deep_feedback_env, monkeypatch):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"], intent="save_only")
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args)["result"]
    key = next(iter(pending["inputRequests"]))
    first = {**args, "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}}
    entered, release = threading.Event(), threading.Event()
    transport = env["app"].state.mcp_deep_adapter.transport
    original = transport.claim_operation

    def pause_before_claim(*arguments, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release the accepted response"
        return original(*arguments, **kwargs)

    monkeypatch.setattr(transport, "claim_operation", pause_before_claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        accepted = pool.submit(rpc, env, token, "tools/call", first, None, "first-response")
        try:
            assert entered.wait(10)
            changed = {key: {"action": "accept", "content": {"content": change["content"] + " 并发的不同编辑。"}}}
            other = rpc(env, token, "tools/call", {**args, "inputResponses": changed}, rpc_id="different-response")
            assert other["error"]["message"] == "idempotency_conflict"
            closed = rpc(env, token, "tools/call", {**args, "inputResponses": {key: {"action": "decline"}}}, rpc_id="concurrent-close")
            assert closed["error"]["message"] == "idempotency_conflict"
        finally:
            release.set()
        result = accepted.result(timeout=10)
    assert "error" not in result, result
    saved = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert len(saved["operations"]) == 1 and saved["appendix"][0]["content"] == change["content"]


def test_task_persists_explicit_application_rejection_after_another_operation_wins(deep_feedback_env, monkeypatch):
    env = deep_feedback_env
    token = access(env)
    caps = {"elicitation": {"form": {}}, "extensions": {TASKS: {}}}
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    change = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": change}}
    pending = rpc(env, token, "tools/call", args, capabilities=caps)["result"]
    task_id = pending["taskId"]
    key = next(iter(pending["inputRequests"]))
    update = {"taskId": task_id, "inputResponses": {key: {"action": "accept", "content": {"content": change["content"]}}}}
    transport = env["app"].state.mcp_deep_adapter.transport
    original_claim = transport.claim_operation
    winner = {**change, "operation_id": str(uuid4()), "intent": "save_only"}

    def let_web_win_after_response_is_frozen(*arguments, **kwargs):
        claimed = original_claim(*arguments, **kwargs)
        with env["store"]._conn() as conn:
            recorded = conn.execute("SELECT result FROM product.mcp_operations WHERE id=%s", (task_id,)).fetchone()[0]
            assert recorded["input_responses"] == update["inputResponses"] and "receipt" not in recorded
        # The competing request executes the real shared application over HTTP.
        saved = env["wire_client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=winner)
        assert saved.status_code == 200, saved.text
        return claimed

    with monkeypatch.context() as conflict:
        conflict.setattr(transport, "claim_operation", let_web_win_after_response_is_frozen)
        rejected = rpc(env, token, "tasks/update", update, capabilities=caps)
    assert rejected["error"]["message"] == "feedback_revision_conflict"
    assert env["store"].read_rule_change(env["principal"], env["round_id"], change["operation_id"]) is None
    env["restart_wire"]()
    failed = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert failed["status"] == "failed" and failed["error"] == {"code": -32602, "message": "feedback_revision_conflict"}
    assert "result" not in failed and "inputRequests" not in failed
    replay = rpc(env, token, "tasks/update", update, capabilities=caps)
    assert replay["error"]["message"] == "feedback_revision_conflict"
    assert rpc(env, token, "tools/call", args, capabilities=caps)["result"]["status"] == "failed"
    after = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    assert len(after["operations"]) == 1 and after["operations"][0]["operation_id"] == winner["operation_id"]
    assert len(after["results"]) == 1
    with env["store"]._conn() as conn:
        assert conn.execute("SELECT state FROM product.mcp_operations WHERE id=%s", (task_id,)).fetchone()[0] == "failed"
        assert conn.execute("SELECT count(*) FROM engine.jobs WHERE kind='audit'").fetchone()[0] == 1
