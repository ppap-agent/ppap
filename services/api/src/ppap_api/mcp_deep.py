"""MCP 2026 Deep adapter; transport receipts reuse the existing MCP store.

The legacy SDK endpoint remains available at /mcp. This narrow endpoint uses
per-request capabilities and invokes the same feedback application as Web.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from ppap_core.feishu_usage import FeishuUsageError, audit_usage_points, query_tenant_ai_item
from ppap_core.mcp import MAX_MCP_BYTES, McpError
from ppap_product_store.mcp import McpStore
from psycopg.types.json import Jsonb

from ppap_api.mcp_auth import OAuthProvider

VERSION = "2026-07-28"
TASKS = "io.modelcontextprotocol/tasks"
PREFIX = "io.modelcontextprotocol/"
SERVER_INFO = {"name": "PPAP Deep", "version": "1"}


def complete(value):
    return {"resultType": "complete", "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": False}


class DeepMcp:
    def __init__(self, provider: OAuthProvider, transport: McpStore):
        self.provider, self.transport = provider, transport
        self.product = transport.product

    def proposal(self, principal, round_id, transport_id):
        with self.transport.connection() as conn:
            row = conn.execute("SELECT grant_id FROM product.mcp_operations WHERE id=%s AND tool='deep.apply_rule_change'", (str(UUID(transport_id)),)).fetchone()
        if not row:
            raise McpError("operation_not_found", 404)
        delegation = self.transport.delegation(str(row["grant_id"]))
        if delegation.session.principal.user_id != principal.user_id or delegation.session.principal.tenant_id != principal.tenant_id:
            raise McpError("operation_not_found", 404)
        operation = self.operation(delegation, transport_id)
        if operation["arguments"]["round_id"] != round_id or operation["state"] == "denied":
            raise McpError("operation_not_found", 404)
        if datetime.now(timezone.utc) - datetime.fromisoformat(operation["created_at"]) > timedelta(hours=1):
            raise McpError("confirmation_expired", 409)
        self.read(delegation, round_id)
        return {"round_id": round_id, "change": operation["arguments"]["change"]}

    def operation(self, delegation, task_id):
        with self.transport.connection() as conn:
            current = self.transport._delegation(conn, delegation.id)
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE id=%s AND grant_id=%s AND tool='deep.apply_rule_change'", (str(UUID(task_id)), delegation.id)).fetchone()
            if not row:
                raise McpError("operation_not_found", 404)
            current.require(row["required_scope"])
        return self.transport.operation_public(row)

    def read(self, delegation, round_id):
        delegation.require("audits:read")
        try:
            return self.product.read_deep_feedback(delegation.session.principal, round_id)
        except KeyError:
            raise McpError("access_denied", 403) from None

    def question(self, operation, view, capabilities):
        arguments = operation["arguments"]
        payload = arguments["change"]
        mode = capabilities.get("elicitation", {})
        if "elicitation" not in capabilities:
            return None
        snapshot = next((q for q in view["clarifications"] if q["id"] == payload.get("question_id")), None)
        selected_mode = "url" if "url" in mode and "form" not in mode else "form"
        epoch = (operation.get("result") or {}).get("question_epoch")
        key = (
            "confirm_"
            + hashlib.sha256(json.dumps([operation["id"], operation["created_at"], epoch, arguments["revision"], view["revision"], snapshot, selected_mode], sort_keys=True).encode()).hexdigest()
        )
        if "url" in mode and "form" not in mode:
            params = {
                "mode": "url",
                "message": "核对原件、修改内容和范围后提交；打开页面不表示保存完成。",
                "url": self.provider.settings.canonical_web_url() + "/audit?round=" + arguments["round_id"] + "&mcpRuleChange=" + operation["id"],
                "elicitationId": key,
            }
        else:
            inputs = [item for result in view["results"] if result["current"] for item in result["inputs"]]
            names = [item.get("filename") or item.get("original_filename") or "已授权原件" for item in inputs]
            by_id = {item["file_version_id"]: name for item, name in zip(inputs, names)}
            scope_names = [by_id.get(ident, "本次资料中的原件") for ident in payload.get("scope", {}).get("document_ids", [])]
            pairs = {(part["document_id"], part["rule_id"]) for part in payload.get("scope", {}).get("rule_targets", [])}
            removals = [part for part in view["appendix"] if part["id"] in payload.get("remove_appendix_ids", [])]
            if len(removals) != len(payload.get("remove_appendix_ids", [])):
                raise McpError("feedback_revision_conflict", 409)
            requirements = [
                by_id[item["file_version_id"]] + " · " + rule["id"] + "：" + rule["rule_to_check"]
                for item in inputs
                for rule in (item.get("rule_body") or (item.get("rule") or {}).get("body") or {}).get("rules", [])
                if (item["file_version_id"], rule["id"]) in pairs or not pairs and rule["id"] in payload.get("scope", {}).get("rule_ids", [])
            ]
            evidence = []
            if snapshot:
                evidence.extend("候选：" + candidate["label"] + "；依据与解释：" + candidate["explanation"] for candidate in snapshot["candidates"])
                for source in snapshot["sources"]:
                    locator = source["locator"]
                    position = " · ".join(str(locator[field]) for field in ("sheet", "table", "cell", "range", "row", "page", "line") if locator.get(field) is not None)
                    if not position:
                        position = json.dumps(locator, ensure_ascii=False, sort_keys=True)
                    evidence.append("原文依据：" + by_id.get(source["document_id"], source["document_id"]) + " · " + position + "；原文：" + (source.get("excerpt") or "请在工作区读取该位置原文"))
            effect = "保存说明，并对本工作区全部待审资料重新分析。" if payload["intent"] == "save_and_analyze" else "仅保存说明，不开始新的分析。"
            target_count = sum(item["role"] == "target" for item in inputs)
            cost = f"本次重新分析 {target_count} 份待审资料，标准用量 {audit_usage_points(target_count)} 点；按组织额度受理。" if self.product.settings.usage_charge_enabled else "本次计费关闭。"
            description = "\n".join(
                [
                    *(q["question"] for q in view["clarifications"] if q["id"] == payload.get("question_id")),
                    "资料：" + "、".join(names),
                    *evidence,
                    "解释仅作用于：" + "、".join(scope_names) if scope_names else "使用本次已保存的规则与说明。",
                    *requirements,
                    "修改：" + (payload.get("content") or "移除以下已确认说明" if removals else payload.get("content") or "按已保存说明开始分析"),
                    *["移除：" + part["content"] + "；原范围：" + "、".join(by_id.get(ident, "本次资料中的原件") for ident in part["scope"]["document_ids"]) for part in removals],
                    effect,
                    cost if payload["intent"] == "save_and_analyze" else "不产生审核用量。",
                ]
            )
            properties = (
                {"content": {"type": "string", "title": "补充说明", "default": payload["content"]}}
                if payload.get("content")
                else {"confirm": {"type": "boolean", "title": "确认移除说明及上述执行效果" if removals else "确认本次分析", "default": False}}
            )
            params = {
                "mode": "form",
                "message": "请核对本次修改、范围及执行效果：\n" + description,
                "requestedSchema": {"type": "object", "properties": properties, "required": list(properties)},
            }
        return key, {"method": "elicitation/create", "params": params}

    def task(self, delegation, operation, capabilities):
        view = self.read(delegation, operation["arguments"]["round_id"])
        now = datetime.now(timezone.utc).isoformat()
        task = {"taskId": operation["id"], "createdAt": operation.get("created_at", now), "lastUpdatedAt": operation.get("updated_at", now), "ttlMs": None, "pollIntervalMs": 1000}
        if operation["state"] == "denied":
            return {**task, "status": "cancelled"}
        receipt = (operation.get("result") or {}).get("receipt")
        if not receipt:
            receipt = self.product.read_rule_change(delegation.session.principal, operation["arguments"]["round_id"], operation["arguments"]["change"]["operation_id"])
        if receipt:
            analysis = receipt.get("analysis_round_id")
            if analysis:
                analysis_view = self.read(delegation, analysis)
                execution = next((r["execution"] for r in analysis_view["results"] if r["round_id"] == analysis), None)
                if execution in {"queued", "running"}:
                    return {**task, "status": "working"}
            return {**task, "status": "completed", "result": complete({"receipt": receipt, "audit": view})}
        if datetime.now(timezone.utc) - datetime.fromisoformat(operation["created_at"]) > timedelta(hours=1):
            return {
                **task,
                "status": "failed",
                "error": {"code": -32602, "message": "confirmation_expired", "data": {"workspace_url": self.provider.settings.canonical_web_url() + "/audit?round=" + view["current_round_id"]}},
            }
        if "input_responses" in (operation.get("result") or {}):
            return {**task, "status": "working", "statusMessage": "已收到本次回答，正在核对保存状态；可恢复原提交或在审核工作区读回。"}
        question = self.question(operation, view, capabilities)
        if question:
            return {**task, "status": "input_required", "inputRequests": {question[0]: question[1]}}
        return {**task, "status": "working", "statusMessage": "请在审核工作区核对并提交解释：" + self.provider.settings.canonical_web_url() + "/audit?round=" + view["current_round_id"]}

    @staticmethod
    def submitted_change(operation, response):
        content = response.get("content")
        change = dict(operation["arguments"]["change"])
        if change.get("content"):
            if not isinstance(content, dict) or set(content) != {"content"} or not isinstance(content["content"], str):
                raise McpError("invalid_input_response")
            if content["content"] != change["content"]:
                change.pop("candidate_id", None)
            change["content"] = content["content"]
        elif content != {"confirm": True}:
            raise McpError("invalid_input_response")
        return change

    def remember_response(self, delegation, operation, responses, *, closed=False):
        """Bind one accepted response before claim or application can commit."""
        with self.transport.connection() as conn:
            conn.execute("SELECT id FROM product.mcp_grants WHERE id=%s FOR UPDATE", (delegation.id,))
            current = self.transport._delegation(conn, delegation.id)
            current.require(operation["arguments"]["required_scope"])
            if operation["arguments"]["change"].get("content") or operation["arguments"]["change"].get("remove_appendix_ids"):
                current.require("rules:write")
            row = conn.execute("SELECT * FROM product.mcp_operations WHERE id=%s AND grant_id=%s FOR UPDATE", (operation["id"], delegation.id)).fetchone()
            if not row or row["state"] == "denied":
                raise McpError("task_cancelled", 409)
            recorded = row["result"] or {}
            if "input_responses" in recorded:
                if recorded["input_responses"] != responses:
                    raise McpError("idempotency_conflict", 409)
                return
            if recorded.get("question_epoch") != (operation.get("result") or {}).get("question_epoch") or closed and row["state"] != "ready":
                raise McpError("invalid_input_response", 409)
            recorded = {"question_epoch": str(uuid4())} if closed else {**recorded, "input_responses": responses}
            conn.execute("UPDATE product.mcp_operations SET result=%s,updated_at=now() WHERE id=%s", (Jsonb(recorded), operation["id"]))

    def settle(self, delegation, operation, responses, receipt):
        with self.transport.connection() as conn:
            conn.execute(
                "UPDATE product.mcp_operations SET state='succeeded',result=COALESCE(result,'{}'::jsonb)||%s,updated_at=now() WHERE id=%s AND grant_id=%s",
                (Jsonb({"receipt": receipt, "input_responses": responses}), operation["id"], delegation.id),
            )

    def apply(self, delegation, operation, responses, capabilities, state=None):
        if state is not None and state != operation["id"]:
            raise McpError("invalid_request_state", 409)
        arguments = operation["arguments"]
        current = self.transport.delegation(delegation.id)
        if arguments["change"].get("content") or arguments["change"].get("remove_appendix_ids"):
            current.require("rules:write")
        current.require(arguments["required_scope"])
        view = self.read(current, arguments["round_id"])
        recorded = operation.get("result") or {}
        if "input_responses" in recorded:
            if responses != recorded["input_responses"]:
                raise McpError("idempotency_conflict", 409)
            existing = self.product.read_rule_change(current.session.principal, arguments["round_id"], arguments["change"]["operation_id"])
            if existing:
                if "receipt" not in recorded:
                    # The shared action validates the exact final content and
                    # returns its durable receipt without another save/queue.
                    change = self.submitted_change(operation, next(iter(responses.values())))
                    existing = self.product.apply_rule_change(current.session.principal, arguments["round_id"], change)
                    self.settle(current, operation, responses, existing)
                return complete(existing)
        if datetime.now(timezone.utc) - datetime.fromisoformat(operation["created_at"]) > timedelta(hours=1):
            raise McpError("confirmation_expired", 409)
        question = self.question(operation, view, capabilities)
        if not question or not isinstance(responses, dict) or set(responses) != {question[0]}:
            raise McpError("invalid_input_response", 409)
        response = responses[question[0]]
        if not isinstance(response, dict):
            raise McpError("invalid_input_response")
        if response.get("action") in {"decline", "cancel"}:
            # Only closing an unanswered interaction renews its key. Claim and
            # settlement timestamps do not change that interaction's identity.
            self.remember_response(current, operation, responses, closed=True)
            return complete({"applied": False, "reason": response["action"], "audit": view})
        if response.get("action") != "accept":
            raise McpError("invalid_input_response")
        if question[1]["params"]["mode"] == "url":
            return complete({"applied": False, "audit": view})
        change = self.submitted_change(operation, response)
        self.remember_response(current, operation, responses)
        latest = self.operation(current, operation["id"])
        if latest["state"] == "denied":
            raise McpError("task_cancelled", 409)
        self.transport.claim_operation(current, operation["id"])
        if self.operation(current, operation["id"])["state"] == "denied":
            raise McpError("task_cancelled", 409)
        # Application owns idempotency/CAS and the save+enqueue transaction. A
        # lost response can safely replay this exact business operation.
        existing = self.product.read_rule_change(current.session.principal, arguments["round_id"], change["operation_id"])
        policy = None
        if not existing and change["intent"] == "save_and_analyze" and self.product.settings.usage_charge_enabled:
            try:
                policy = query_tenant_ai_item(self.provider.http, current.session.access_token, self.product.settings.feishu_ai_item_id)
            except FeishuUsageError:
                raise McpError("usage_policy_unavailable", 503) from None
        receipt = self.product.apply_rule_change(current.session.principal, arguments["round_id"], change, usage_policy=policy)
        self.settle(current, operation, responses, receipt)
        return complete(receipt)

    def dispatch(self, delegation, method, params):
        meta = params.get("_meta", {})
        if meta.get(PREFIX + "protocolVersion") != VERSION:
            raise McpError("unsupported_protocol_version")
        info = meta.get(PREFIX + "clientInfo")
        if PREFIX + "clientInfo" in meta and (not isinstance(info, dict) or not all(isinstance(info.get(field), str) for field in ("name", "version"))):
            raise McpError("invalid_client_info")
        capabilities = meta.get(PREFIX + "clientCapabilities")
        if not isinstance(capabilities, dict) or not isinstance(capabilities.get("extensions", {}), dict) or any(not isinstance(value, dict) for value in capabilities.get("extensions", {}).values()):
            raise McpError("missing_client_capabilities")
        mode = capabilities.get("elicitation")
        if "elicitation" in capabilities:
            if not isinstance(mode, dict) or mode and not ({"form", "url"} & mode.keys()):
                raise McpError("invalid_elicitation_capability")
            if any(not isinstance(value, dict) for name, value in mode.items() if name in {"form", "url"}):
                raise McpError("invalid_elicitation_capability")
        task_capable = TASKS in capabilities.get("extensions", {})
        if method == "server/discover":
            return {"resultType": "complete", "supportedVersions": [VERSION], "capabilities": {"tools": {}, "extensions": {TASKS: {}}}, "ttlMs": 0, "cacheScope": "private"}
        if method == "ping":
            return {"resultType": "complete"}
        if method == "tools/list":
            from ppap_api.deep_feedback import RuleChangeRequest

            change_schema = RuleChangeRequest.model_json_schema()
            definitions = change_schema.pop("$defs", {})
            return {
                "resultType": "complete",
                "ttlMs": 0,
                "cacheScope": "private",
                "tools": [
                    {"name": name, "description": description, "inputSchema": schema}
                    for name, description, schema in (
                        ("read_audit", "读取审核、解释问题及明确输入", {"type": "object", "properties": {"round_id": {"type": "string"}}, "required": ["round_id"], "additionalProperties": False}),
                        (
                            "start_analysis",
                            "明确开始当前规则和资料的新分析",
                            {
                                "type": "object",
                                "properties": {"round_id": {"type": "string"}, "operation_id": {"type": "string"}, "expected_revision": {"type": "integer"}},
                                "required": ["round_id", "operation_id", "expected_revision"],
                            },
                        ),
                        (
                            "apply_rule_change",
                            "核对后保存解释；明确选择时重新分析",
                            {
                                "type": "object",
                                "properties": {"round_id": {"type": "string"}, "change": change_schema},
                                "required": ["round_id", "change"],
                                "additionalProperties": False,
                                "$defs": definitions,
                            },
                        ),
                    )
                ],
            }
        if method in {"tasks/get", "tasks/update", "tasks/cancel"}:
            if not task_capable:
                raise McpError("missing_tasks_capability")
            operation = self.operation(delegation, params["taskId"])
            if operation["arguments"]["change"].get("content") or operation["arguments"]["change"].get("remove_appendix_ids"):
                delegation.require("rules:write")
            if method == "tasks/cancel":
                with self.transport.connection() as conn:
                    conn.execute("UPDATE product.mcp_operations SET state='denied',updated_at=now() WHERE id=%s AND state='ready'", (operation["id"],))
                return {"resultType": "complete"}
            if method == "tasks/update":
                if operation["state"] == "denied":
                    raise McpError("task_cancelled", 409)
                if params.get("inputResponses"):
                    # Unknown or superseded keys cannot answer the current
                    # question. The Tasks extension recommends ignoring them.
                    if "input_responses" in (operation.get("result") or {}):
                        self.apply(delegation, operation, params["inputResponses"], capabilities)
                    else:
                        question = self.question(operation, self.read(delegation, operation["arguments"]["round_id"]), capabilities)
                        if question and question[0] in params["inputResponses"]:
                            self.apply(delegation, operation, params["inputResponses"], capabilities)
                return {"resultType": "complete"}
            return {"resultType": "complete", **self.task(delegation, operation, capabilities)}
        if method != "tools/call":
            raise McpError("method_not_found", 404)
        arguments = params.get("arguments", {})
        if params.get("name") == "read_audit":
            return complete(self.read(delegation, arguments["round_id"]))
        if params.get("name") not in {"apply_rule_change", "start_analysis"}:
            raise McpError("tool_not_found", 404)
        change = (
            arguments["change"]
            if params["name"] == "apply_rule_change"
            else {"operation_id": arguments["operation_id"], "expected_revision": arguments["expected_revision"], "intent": "save_and_analyze"}
        )
        from ppap_api.deep_feedback import RuleChangeRequest

        change = RuleChangeRequest.model_validate(change).model_dump(exclude_none=True)
        if change.get("content") or change.get("remove_appendix_ids"):
            delegation.require("rules:write")
        required = "audits:execute" if change.get("intent") == "save_and_analyze" else "audits:write"
        delegation.require(required)
        view = self.read(delegation, arguments["round_id"])
        operation = self.transport.prepare_operation(
            delegation,
            change["operation_id"],
            "deep.apply_rule_change",
            {"round_id": arguments["round_id"], "revision": change["expected_revision"], "change": change, "required_scope": required},
            required,
            None,
        )
        if operation["state"] == "denied":
            raise McpError("task_cancelled", 409)
        if params.get("inputResponses"):
            return self.apply(delegation, operation, params["inputResponses"], capabilities, params.get("requestState"))
        if "receipt" in (operation.get("result") or {}):
            return complete(operation["result"]["receipt"])
        if task_capable:
            return {"resultType": "task", **self.task(delegation, operation, capabilities)}
        receipt = self.product.read_rule_change(delegation.session.principal, arguments["round_id"], change["operation_id"])
        if receipt:
            return complete(receipt)
        if datetime.now(timezone.utc) - datetime.fromisoformat(operation["created_at"]) > timedelta(hours=1):
            return {
                **complete({"applied": False, "reason": "confirmation_expired", "workspace_url": self.provider.settings.canonical_web_url() + "/audit?round=" + view["current_round_id"]}),
                "isError": True,
            }
        question = self.question(operation, view, capabilities)
        if not question:
            return complete({"applied": False, "audit": view, "workspace_url": self.provider.settings.canonical_web_url() + "/audit?round=" + view["current_round_id"]})
        return {"resultType": "input_required", "inputRequests": {question[0]: question[1]}, "requestState": operation["id"]}


def install_deep_mcp(app, provider, transport, session_of):
    adapter = DeepMcp(provider, transport)
    authority_url = urlsplit(provider.base)
    guard = TransportSecurityMiddleware(
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[authority_url.netloc],
            allowed_origins=[f"{authority_url.scheme}://{authority_url.netloc}", *provider.settings.web_origins(), *provider.settings.mcp_allowed_origins],
        )
    )

    app.state.mcp_deep_adapter = adapter

    @app.post("/mcp/deep", include_in_schema=False)
    async def deep_rpc(request: Request):
        rpc = {}
        try:
            rejected = await guard.validate_request(request, is_post=True)
            if rejected:
                return rejected
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                raise McpError("invalid_token", 401)
            delegation = await asyncio.to_thread(provider.delegation, authorization[7:])
            await asyncio.to_thread(transport.rate_limit, "mcp-deep:" + delegation.id, 120)
            chunks = bytearray()
            async for chunk in request.stream():
                chunks.extend(chunk)
                if len(chunks) > MAX_MCP_BYTES:
                    raise McpError("request_too_large", 413)
            raw = bytes(chunks)
            try:
                rpc = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise McpError("parse_error") from None
            if not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0" or type(rpc.get("id")) not in (str, int) or not isinstance(rpc.get("method"), str):
                raise McpError("invalid_request")
            params = rpc.get("params", {})
            if not isinstance(params, dict) or not isinstance(params.get("_meta"), dict):
                raise McpError("missing_request_metadata")
            meta = params["_meta"]
            if PREFIX + "protocolVersion" not in meta or PREFIX + "clientCapabilities" not in meta:
                raise McpError("missing_request_metadata")
            name = params.get("taskId") if rpc["method"].startswith("tasks/") else params.get("name") if rpc["method"] == "tools/call" else None
            header_name = request.headers.get("mcp-name")
            if header_name and header_name.startswith("=?base64?") and header_name.endswith("?="):
                header_name = base64.b64decode(header_name[9:-2], validate=True).decode("utf-8")
            if request.headers.get("mcp-method") != rpc["method"] or request.headers.get("mcp-protocol-version") != meta.get(PREFIX + "protocolVersion") or name is not None and header_name != name:
                raise McpError("header_mismatch", 400)
            if meta.get(PREFIX + "protocolVersion") != VERSION:
                raise McpError("unsupported_protocol_version", 400)
            result = await asyncio.to_thread(adapter.dispatch, delegation, rpc["method"], params)
            result.setdefault("_meta", {})[PREFIX + "serverInfo"] = SERVER_INFO
            return JSONResponse({"jsonrpc": "2.0", "id": rpc["id"], "result": result}, headers={"Cache-Control": "no-store"})
        except (McpError, ValueError, KeyError, TypeError, binascii.Error) as error:
            import re

            code = error.code if isinstance(error, McpError) else str(error) if type(error) is ValueError and re.fullmatch("[a-z_]+", str(error)) else "invalid_params"
            status = error.status if isinstance(error, McpError) else 400
            rpc_code = {
                "header_mismatch": -32020,
                "unsupported_protocol_version": -32022,
                "missing_tasks_capability": -32021,
                "method_not_found": -32601,
                "invalid_request": -32600,
                "parse_error": -32700,
            }.get(code, -32602)
            data: dict | None = (
                {"supported": [VERSION], "requested": request.headers.get("mcp-protocol-version", "")}
                if rpc_code == -32022
                else {"requiredCapabilities": {"extensions": {TASKS: {}}}}
                if rpc_code == -32021
                else None
            )
            error_body = {"code": rpc_code, "message": code, **({"data": data} if data else {})}
            headers = {"Cache-Control": "no-store"}
            if status == 401:
                resource = urlsplit(provider.resource)
                metadata = f"{resource.scheme}://{resource.netloc}/.well-known/oauth-protected-resource{resource.path}"
                headers["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata}"'
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rpc.get("id") if isinstance(rpc, dict) else None, "error": error_body},
                status_code=(
                    404
                    if rpc_code == -32601
                    else 400
                    if rpc_code in {-32020, -32021, -32022, -32600} or code in {"missing_request_metadata", "missing_client_capabilities", "invalid_client_info", "invalid_elicitation_capability"}
                    else status
                    if status in {401, 403, 413}
                    else 200
                ),
                headers=headers,
            )
