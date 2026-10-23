"""Actual PostgreSQL + public OAuth/MCP wire contract. Feishu sign-in is separate live evidence."""

import concurrent.futures
import hashlib
import io
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from ppap_api import create_app
from ppap_application import build_product_store
from ppap_core.fixtures import APP_ID, RULE_A
from ppap_core.mcp import SCOPES, pkce_challenge
from ppap_core.settings import Settings
from ppap_worker import process_claimed


@pytest.fixture
def mcp_app(auth_store_factory):
    settings = Settings(mcp_enabled=True, feishu_app_id=APP_ID, usage_charge_enabled=False)
    store = auth_store_factory(settings)
    store.seed_isolated_dev()
    store = build_product_store(settings, objects=store.objects, role="api")
    app = create_app(settings, auth_store=store)
    return app, store


def connection(client, store, *, person="alice", scopes="identity:read files:read files:write audits:read", exchange_now=True):
    principal = store.principal_for_fixture(person)
    client.cookies.set("ppap_session", store.create_session(principal))
    registered = client.post(
        "/oauth/register",
        json={
            "client_name": "MCP contract test",
            "redirect_uris": ["http://127.0.0.1:3456/callback"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": " ".join(SCOPES),
        },
    )
    assert registered.status_code == 201, registered.text
    cid = registered.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    args = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": "http://127.0.0.1:3456/callback",
        "scope": scopes,
        "state": "contract-state",
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "resource": "http://127.0.0.1:8080/mcp",
    }
    started = client.get("/oauth/authorize", params=args, follow_redirects=False)
    assert started.status_code == 302, started.text
    path = urlsplit(started.headers["location"]).path
    consent = client.get(path)
    assert consent.status_code == 200, consent.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text)[1]
    decided = client.post(path, data={"csrf": csrf, "decision": "approve", "scope": scopes.split()}, follow_redirects=False)
    assert decided.status_code == 303, decided.text
    returned = parse_qs(urlsplit(decided.headers["location"]).query)
    assert returned["state"] == ["contract-state"]
    exchange = {"grant_type": "authorization_code", "client_id": cid, "redirect_uri": args["redirect_uri"], "code": returned["code"][0], "code_verifier": verifier, "resource": args["resource"]}
    if not exchange_now:
        return cid, None, exchange
    token = client.post("/oauth/token", data=exchange)
    assert token.status_code == 200, token.text
    return cid, token.json(), exchange


def rpc(client, token, method, params=None):
    return client.post(
        "/mcp",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_oauth_discovery_pkce_rotation_and_revocation(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert meta["resource"] == "http://127.0.0.1:8080/mcp"
        assert meta["authorization_servers"] == ["http://127.0.0.1:8080/oauth"]
        assert "none" in client.get("/.well-known/oauth-authorization-server/oauth").json()["token_endpoint_auth_methods_supported"]
        assert rpc(client, "invalid", "tools/list").status_code == 401
        cid, tokens, exchange = connection(client, store)
        assert client.post("/oauth/token", data=exchange).status_code == 400  # single use code
        call = rpc(client, tokens["access_token"], "tools/call", {"name": "get_identity", "arguments": {}})
        assert call.status_code == 200, call.text
        value = call.json()["result"]["structuredContent"]
        assert value["status"] == "ok", value
        assert value["data"]["value"]["user_id"] == store.principal_for_fixture("alice").user_id
        refresh = {"grant_type": "refresh_token", "client_id": cid, "refresh_token": tokens["refresh_token"], "resource": exchange["resource"]}
        rotated = client.post("/oauth/token", data=refresh)
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["refresh_token"] != tokens["refresh_token"]
        assert client.post("/oauth/token", data=refresh).status_code == 400
        assert rpc(client, rotated.json()["access_token"], "tools/list").status_code == 401  # family revoked on reuse


def test_tools_use_live_scopes_and_existing_object_permissions(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes="identity:read")
        result = rpc(client, tokens["access_token"], "tools/call", {"name": "list_tasks", "arguments": {}}).json()["result"]["structuredContent"]
        assert result["status"] == "error" and result["error"] == "insufficient_scope"
        cid, tokens, _ = connection(client, store)
        assert client.post("/oauth/revoke", data={"client_id": cid, "token": tokens["access_token"]}).status_code == 200
        assert rpc(client, tokens["access_token"], "tools/list").status_code == 401


def test_invalid_resource_and_concurrent_code_exchange(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, _, exchange = connection(client, store, exchange_now=False)
        wrong = client.post("/oauth/token", data={**exchange, "resource": "https://other.example/mcp"})
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_target"
        # Competing first exchanges must have one winner, not two tokens.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: client.post("/oauth/token", data=exchange).status_code, range(2)))
        assert sorted(replies) == [200, 400]


def test_browser_cookie_alone_cannot_call_mcp(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        connection(client, store)
        response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401


def call(client, token, name, **arguments):
    response = rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    return result["structuredContent"]


def upload(client, token, content=None):
    if content is None:
        stream = io.BytesIO()
        book = Workbook()
        book.active.append(["控制计划", "测试客户", "零件号 P-1"])
        book.save(stream)
        book.close()
        content = stream.getvalue()
    rid = str(uuid4())
    body = {
        "original_filename": "控制计划.xlsx",
        "byte_size": len(content),
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    created = call(client, token, "create_file", request_id=rid, body=body)
    assert created["status"] == "ok", created
    assert call(client, token, "create_file", request_id=rid, body=body)["data"] == created["data"]
    changed = call(client, token, "create_file", request_id=rid, body={**body, "original_filename": "different.xlsx"})
    assert changed["error"] == "request_conflict"
    version = created["data"]
    ids = {"file_id": version["file_id"], "version_id": version["id"]}
    transfer = call(client, token, "create_upload_transfer", **ids)
    assert transfer["status"] == "ok", transfer
    headers = {"Authorization": "Bearer " + token}
    assert client.put(transfer["data"]["url"], content=content).status_code == 401
    assert client.put(transfer["data"]["url"], headers=headers, content=content).status_code == 200
    finalized = call(client, token, "finalize_file", **ids, request_id=str(uuid4()), body={"sha256": body["sha256"]})
    assert finalized["status"] == "ok", finalized
    return finalized["data"], content


def approve(client, operation):
    assert operation["status"] == "confirmation_required", operation
    page = client.get(operation["confirmation_url"])
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    assert client.post(operation["confirmation_url"], data={"csrf": "wrong", "decision": "approve"}).status_code == 403
    assert client.post(operation["confirmation_url"], data={"csrf": csrf, "decision": "approve"}).status_code == 200


def test_real_file_bytes_resources_object_authority_and_revocation(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        version, content = upload(client, token)
        ids = {"file_id": version["file_id"], "version_id": version["id"]}
        uri = f"ppap://files/{version['file_id']}/versions/{version['id']}?pointer=%2Fsha256"
        resource = rpc(client, token, "resources/read", {"uri": uri}).json()
        assert json.loads(resource["result"]["contents"][0]["text"])["value"] == hashlib.sha256(content).hexdigest()
        download = call(client, token, "download_file", **ids)["data"]["url"]
        assert client.get(download, headers={"Authorization": "Bearer " + token}).content == content
        _, other, _ = connection(client, store, person="carol")
        assert call(client, other["access_token"], "get_file", **ids)["status"] == "error"
        assert client.get(download, headers={"Authorization": "Bearer " + other["access_token"]}).status_code == 404
        assert "error" in rpc(client, other["access_token"], "resources/read", {"uri": uri}).json()
        assert client.post("/oauth/revoke", data={"client_id": cid, "token": tokens["refresh_token"]}).status_code == 200
        assert client.get(download, headers={"Authorization": "Bearer " + token}).status_code == 401
        assert rpc(client, token, "resources/read", {"uri": uri}).status_code == 401


def test_pkce_failure_does_not_consume_code_and_unregistered_redirect_is_rejected(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, _, exchange = connection(client, store, exchange_now=False)
        assert client.post("/oauth/token", data={**exchange, "code_verifier": secrets.token_urlsafe(48)}).status_code == 400
        assert client.post("/oauth/token", data=exchange).status_code == 200
        rejected = client.get(
            "/oauth/authorize",
            params={
                "client_id": cid,
                "response_type": "code",
                "redirect_uri": "https://attacker.example/callback",
                "scope": "identity:read",
                "resource": exchange["resource"],
                "code_challenge": pkce_challenge(exchange["code_verifier"]),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert rejected.status_code == 400
        assert "location" not in rejected.headers


def test_mcp_precheck_rule_publication_prepare_and_human_confirm_once(mcp_app):
    app, store = mcp_app
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _ = upload(client, token)
        vid = str(uuid4())
        validation = call(client, token, "validate_file", request_id=vid, body={"request_id": vid, "source_file_id": file["file_id"], "source_file_version_id": file["id"]})
        assert validation["status"] == "ok", validation
        job = worker.claim_job("mcp-precheck")
        process_claimed(worker, job, "mcp-precheck")
        assert call(client, token, "get_validation", validation_id=vid)["data"]["value"]["status"] == "succeeded"
        rid = str(uuid4())
        rule_body = {
            "kind": "engine",
            "type": "控制计划",
            "rule_profile": "v3",
            "customers": ["测试客户"],
            "models": [],
            "parts": [],
            "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
        }
        publish = {"request_id": rid, "body": {"title": "MCP 测试规则", "body": rule_body, "source_file_version_id": file["id"]}}
        pending = call(client, token, "publish_rule", **publish)
        approve(client, pending)
        rule = call(client, token, "publish_rule", **publish)
        assert rule["status"] == "ok", rule
        assert call(client, token, "publish_rule", **publish)["data"]["id"] == rule["data"]["id"]
        request_id = str(uuid4())
        args = {
            "request_id": request_id,
            "body": {
                "request_id": request_id,
                "analysis_mode": "standard",
                "customer_preference": None,
                "files": [{"file_version_id": file["id"], "validation_id": vid, "selection": "selected", "removal_reason": None}],
            },
        }
        prepared = call(client, token, "prepare_task", **args)
        assert prepared["status"] == "ok", prepared
        task = prepared["data"]
        assert call(client, token, "prepare_task", **args)["data"]["id"] == task["id"]

        class MetadataAI:
            def structured_completion(self, **request):
                if request["operation"] == "document_classification":
                    return "控制计划"
                return json.dumps({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"})

        # Only external model metadata is controlled. Actual validation,
        # recognition, binding, acceptance and PostgreSQL jobs execute here.
        process_claimed(worker, worker.claim_job("mcp-match"), "mcp-match", rule_ai=MetadataAI())
        task = call(client, token, "get_task", task_id=task["id"])["data"]["value"]
        assert task["state"] == "pending_confirm" and task["round_id"] is None
        confirm = {"task_id": task["id"], "request_id": str(uuid4()), "expected_revision": task["input_revision"]}
        proposal = call(client, token, "confirm_task", **confirm)
        assert call(client, token, "get_operation", request_id=confirm["request_id"])["confirmation_url"] == proposal["confirmation_url"]
        approve(client, proposal)
        accepted = call(client, token, "confirm_task", **confirm)
        assert accepted["status"] == "ok", accepted
        assert accepted["data"]["round_id"]
        assert call(client, token, "confirm_task", **confirm)["data"] == accepted["data"]
        assert worker.claim_job("mcp-audit").kind == "audit"
        assert worker.claim_job("no-second-audit") is None


def test_review_receipt_commits_with_effect_even_when_transport_loses_reply(mcp_app, monkeypatch):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _ = upload(client, token)
        draft = client.post("/drafts", json={"items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": RULE_A}]}).json()
        accepted = client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert accepted.status_code == 201, accepted.text
        round_id = accepted.json()["id"]
        process_claimed(store, store.claim_job("presence-audit"), "presence-audit")
        before = client.get(f"/rounds/{round_id}").json()
        finding = before["findings"][0]
        args = {"round_id": round_id, "request_id": str(uuid4()), "body": {"item_key": finding["item_key"], "judgment": "同意", "rationale": "已核对原件", "next_step": "", "version": 0}}
        pending = call(client, token, "submit_review", **args)
        assert client.get(f"/rounds/{round_id}").json()["findings"][0]["review"] is None
        approve(client, pending)
        original = app.state.mcp_gateway.request

        async def lost_reply(*a, **kw):
            response = await original(*a, **kw)
            if len(a) >= 2 and a[1] == "PUT":
                raise ConnectionError("response discarded after the actual commit")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "submit_review", **args)["status"] == "unknown"
        receipt = call(client, token, "get_operation", request_id=args["request_id"])
        assert receipt["status"] == "ok", receipt
        assert receipt["provenance"]["client_id"] == cid
        assert receipt["provenance"]["confirmed_by"] == store.principal_for_fixture("alice").user_id
        assert receipt["data"]["findings"][0]["review"]["version"] == 1
        assert call(client, token, "submit_review", **args)["data"] == receipt["data"]
        current = client.get(f"/rounds/{round_id}").json()["findings"][0]
        assert current["status"] == finding["status"] and current["review"]["version"] == 1
        next_args = {**args, "request_id": str(uuid4()), "body": {**args["body"], "version": 1, "rationale": "第二次核对"}}
        approve(client, call(client, token, "submit_review", **next_args))
        # A concurrent Web edit wins before the delegated write. The old
        # confirmation cannot authorize overriding that newer human judgment.
        edited = client.put(f"/rounds/{round_id}/reviews", json={**args["body"], "version": 1, "judgment": "需修改", "rationale": "网页复核已修订"})
        assert edited.status_code == 200
        assert call(client, token, "submit_review", **next_args)["error"] == "confirmation_content_changed"
        assert client.get(f"/rounds/{round_id}").json()["findings"][0]["review"]["version"] == 2


def test_registry_pagination_annotations_and_browser_revoke(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        tools, params = {}, {}
        while True:
            result = rpc(client, token, "tools/list", params).json()["result"]
            assert len(result["tools"]) <= 25
            for tool in result["tools"]:
                assert tool["name"] not in tools
                assert tool["inputSchema"]["type"] == "object"
                assert tool["outputSchema"]["type"] == "object"
                tools[tool["name"]] = tool
            if not result.get("nextCursor"):
                break
            params = {"cursor": result["nextCursor"]}
        assert {"prepare_task", "confirm_task", "submit_review", "share_reports", "retry_share_target", "download_report", "get_usage"} <= tools.keys()
        assert tools["list_feishu_files"]["annotations"]["readOnlyHint"] is True
        assert tools["share_reports"]["annotations"]["readOnlyHint"] is False
        assert tools["share_reports"]["annotations"]["openWorldHint"] is True
        templates = rpc(client, token, "resources/templates/list").json()["result"]["resourceTemplates"]
        assert len(templates) == 5 and all(t["mimeType"] == "application/json" for t in templates)
        prompts = rpc(client, token, "prompts/list").json()["result"]["prompts"]
        assert {p["name"] for p in prompts} == {"summarize_round", "compare_rounds"}
        management = client.get("/mcp/connections")
        assert management.status_code == 200
        target = re.search(r'<form method="post" action="([^"]+)"', management.text)[1]
        csrf = re.search(r'name="csrf" value="([^"]+)"', management.text)[1]
        assert client.post(target, data={"csrf": "wrong"}).status_code == 403
        assert rpc(client, token, "tools/list").status_code == 200
        assert client.post(target, data={"csrf": csrf}, follow_redirects=False).status_code == 303
        assert rpc(client, token, "tools/list").status_code == 401


def test_bearer_token_and_write_receipt_survive_new_api_instance(mcp_app):
    app, store = mcp_app
    args = {"request_id": str(uuid4()), "body": {"original_filename": "resume.txt", "byte_size": 4, "content_type": "text/plain", "sha256": hashlib.sha256(b"test").hexdigest()}}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        before = call(client, token, "create_file", **args)
        assert before["status"] == "ok"
    # No browser session, SDK session or in-process receipt survives this restart.
    restarted = create_app(store.settings, auth_store=build_product_store(store.settings, objects=store.objects, role="api"))
    with TestClient(restarted, base_url="http://127.0.0.1:8080") as client:
        receipt = call(client, token, "get_operation", request_id=args["request_id"])
        assert receipt["status"] == "ok" and receipt["data"] == before["data"]
        assert call(client, token, "create_file", **args)["data"] == before["data"]
        assert call(client, token, "get_upload", request_id=args["request_id"])["data"]["value"]["file"]["id"] == before["data"]["id"]


def test_mcp_origin_and_body_boundaries_and_origin_root_discovery(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        headers = {"Authorization": "Bearer " + tokens["access_token"], "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", headers={**headers, "Origin": "https://attacker.example"}, json={}).status_code == 403
        assert client.post("/mcp", headers=headers, content=b" " * (1024 * 1024 + 1)).status_code == 413
        assert rpc(client, tokens["access_token"], "tools/list").status_code == 200
        preflight = client.options(
            "/oauth/token", headers={"Origin": "https://client.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization,Content-Type"}
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"
        discovery = client.get("/.well-known/oauth-protected-resource/mcp", headers={"Origin": "https://client.example"})
        # Starlette may echo the origin when the browser sends a Cookie; it
        # still must not grant credentialed cross-origin reads.
        assert discovery.headers["access-control-allow-origin"] in {"*", "https://client.example"}
        assert "access-control-allow-credentials" not in discovery.headers
    store.settings.public_api_url = "https://ppap.example/ppap"
    public = create_app(store.settings, auth_store=store)
    with TestClient(public, base_url="https://ppap.example") as client:
        auth = client.get("/.well-known/oauth-authorization-server/ppap/oauth").json()
        assert auth["issuer"] == "https://ppap.example/ppap/oauth"
        assert auth["token_endpoint"] == "https://ppap.example/ppap/oauth/token"
        resource = client.get("/.well-known/oauth-protected-resource/ppap/mcp").json()
        assert resource["resource"] == "https://ppap.example/ppap/mcp"


def test_concurrent_refresh_rotates_once_and_revokes_the_reused_family(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, exchange = connection(client, store)
        body = {"grant_type": "refresh_token", "client_id": cid, "refresh_token": tokens["refresh_token"], "resource": exchange["resource"]}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: client.post("/oauth/token", data=body), range(2)))
        assert sorted(r.status_code for r in replies) == [200, 400]
        winner = next(r.json() for r in replies if r.status_code == 200)
        assert rpc(client, winner["access_token"], "tools/list").status_code == 401
