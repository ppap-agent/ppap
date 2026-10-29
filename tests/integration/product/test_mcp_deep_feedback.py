"""MCP 2026 wire -> real application, native PG and semantic guest."""

from uuid import uuid4

from ppap_api.mcp_deep import PREFIX, TASKS, VERSION

from .conftest import change_payload


def rpc(env, token, method, params=None, capabilities=None, rpc_id=1):
    return env["mcp_rpc"](token, method, params, capabilities, rpc_id)


def access(env):
    return env["mcp_access"]()


def test_native_mrtr_confirmation_uses_same_application_and_recomputes(deep_feedback_env):
    env = deep_feedback_env
    token = access(env)
    view = env["store"].read_deep_feedback(env["principal"], env["round_id"])
    payload = change_payload(view, env["uploaded"])
    args = {"name": "apply_rule_change", "arguments": {"round_id": env["round_id"], "change": payload}}
    discovery = rpc(env, token, "server/discover")["result"]
    assert discovery["protocolVersion"] == VERSION
    pending = rpc(env, token, "tools/call", args)["result"]
    assert pending["resultType"] == "input_required"
    key = next(iter(pending["inputRequests"]))
    assert pending["inputRequests"][key]["method"] == "elicitation/create"
    assert env["store"].read_deep_feedback(env["principal"], env["round_id"])["revision"] == 0
    accepted = rpc(env, token, "tools/call", {**args, "requestState": pending["requestState"], "inputResponses": {key: {"action": "accept", "content": {"content": payload["content"]}}}}, rpc_id=2)
    assert "error" not in accepted, accepted
    receipt = accepted["result"]["structuredContent"]
    env["run_jobs"]()
    result = env["client"].get("/rounds/" + receipt["analysis_round_id"]).json()
    assert [r["status"] for r in result["findings"]] == ["PASS", "NG", "PASS"]
    replay = rpc(env, token, "tools/call", args, rpc_id=3)["result"]["structuredContent"]
    assert replay == receipt
    assert env["client"].get("/audit-rounds/" + env["round_id"] + "/deep-feedback").json()["revision"] == 1


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
    update = {"taskId": task_id, "inputResponses": {key: {"action": "accept", "content": {"content": payload["content"]}}}}
    assert "error" not in rpc(env, token, "tasks/update", update, capabilities=caps)
    assert "error" not in rpc(env, token, "tasks/update", update, capabilities=caps)
    done = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert done["status"] == "completed"
    assert done["result"]["structuredContent"]["receipt"]["analysis_round_id"] is None


def test_url_accept_does_not_apply_and_web_commits_same_operation(deep_feedback_env):
    env = deep_feedback_env
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
    proposal = env["client"].get(f"/audit-rounds/{env['round_id']}/mcp-rule-changes/{task_id}")
    assert proposal.status_code == 200, proposal.text
    web_change = proposal.json()["change"]
    assert web_change["operation_id"] == change["operation_id"]
    applied = env["client"].post(f"/audit-rounds/{env['round_id']}/rule-changes", json=web_change)
    assert applied.status_code == 200, applied.text
    env["run_jobs"]()
    done = rpc(env, token, "tasks/get", {"taskId": task_id}, capabilities=caps)["result"]
    assert done["status"] == "completed"
    assert done["result"]["structuredContent"]["receipt"] == applied.json()


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
    mismatched = env["client"].post("/mcp/deep", headers={**headers, "Mcp-Method": "tools/list"}, json=message)
    assert mismatched.status_code == 400 and mismatched.json()["error"]["code"] == -32020
    origin = env["client"].post("/mcp/deep", headers={**headers, "Origin": "https://untrusted.example"}, json=message)
    assert origin.status_code == 403
    malformed = env["client"].post("/mcp/deep", headers=headers, json={**message, "params": []})
    assert malformed.status_code == 200 and malformed.json()["error"]["message"] == "invalid_params"
    with env["store"]._conn() as conn:
        conn.execute("UPDATE product.mcp_grants SET revoked_at=now()")
    revoked = env["client"].post("/mcp/deep", headers=headers, json=message)
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
