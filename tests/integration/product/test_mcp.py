"""Actual PostgreSQL + public OAuth/MCP wire contract. Feishu sign-in is separate live evidence."""

import concurrent.futures
import hashlib
import io
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from ppap_api import create_app
from ppap_application import build_product_store
from ppap_core.feishu import UserTokens
from ppap_core.fixtures import APP_ID, RULE_A
from ppap_core.identity import ProviderAuthError
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


def connection(client, store, *, person="alice", scopes="identity:read files:read files:write audits:read", exchange_now=True, browser_session=None):
    principal = store.principal_for_fixture(person)
    client.cookies.set("ppap_session", browser_session or store.create_session(principal))
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


def provider_tokens(label, *, expired=False):
    at = datetime.now(timezone.utc)
    return UserTokens("access-" + label, "refresh-" + label, at + timedelta(seconds=-1 if expired else 3600), at + timedelta(days=7))


def expire_provider(store, raw):
    session = store.load_session(raw)
    with store._conn() as conn:
        conn.execute("UPDATE product.provider_authorizations SET access_expires_at=now()-interval '1 second' WHERE id=%s", (session.provider_authorization_id,))
    return store.load_session(raw)


def test_web_and_multiple_mcp_clients_share_one_provider_refresh(mcp_app):
    _, store = mcp_app
    principal = store.principal_for_fixture("alice")
    original = provider_tokens("shared")
    raw = store.create_session(principal, original)
    second_browser = store.create_session(principal, original)
    assert store.load_session(raw).provider_authorization_id == store.load_session(second_browser).provider_authorization_id
    entered, release = threading.Event(), threading.Event()
    requests = []

    def provider(request):
        requests.append(json.loads(request.content)["refresh_token"])
        entered.set()
        assert release.wait(10), "refresh release was not delivered"
        return httpx.Response(200, json={"code": 0, "access_token": "access-rotated", "refresh_token": "refresh-rotated", "expires_in": 3600, "refresh_token_expires_in": 604800})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as client:
        first_id, first, _ = connection(client, store, browser_session=raw)
        _, second, _ = connection(client, store, browser_session=raw)
        expire_provider(store, raw)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            browser_request = pool.submit(client.get, "/me")
            try:
                assert entered.wait(5)
                busy = call(client, second["access_token"], "get_identity")
                assert busy["error"] == "provider_refresh_in_progress" and busy["retry_after_seconds"] == 5
                # Authentication remains valid; this is not an OAuth token failure.
                assert rpc(client, first["access_token"], "tools/list").status_code == 200
            finally:
                release.set()
            assert browser_request.result(timeout=10).status_code == 200
        assert call(client, first["access_token"], "get_identity")["status"] == "ok"
        assert call(client, second["access_token"], "get_identity")["status"] == "ok"
        assert store.load_session(second_browser).access_token == "access-rotated"
        repeated_login = store.create_session(
            principal, UserTokens("access-rotated", "refresh-rotated", datetime.now(timezone.utc) + timedelta(hours=1), datetime.now(timezone.utc) + timedelta(days=7))
        )
        assert store.load_session(repeated_login).provider_authorization_id == store.load_session(raw).provider_authorization_id
        assert requests == ["refresh-shared"]
        client.post("/oauth/revoke", data={"client_id": first_id, "token": first["access_token"]})
        assert rpc(client, first["access_token"], "tools/list").status_code == 401
        assert call(client, second["access_token"], "get_identity")["status"] == "ok"
        assert client.get("/me").status_code == 200
        store.revoke_session(raw)
        # Browser logout revokes that session; it does not revoke separately
        # confirmed MCP grants that share the underlying provider credentials.
        assert client.get("/me").status_code == 401
        assert call(client, second["access_token"], "get_identity")["status"] == "ok"


def test_unknown_provider_refresh_survives_restart_and_does_not_bind_to_new_login(mcp_app):
    _, store = mcp_app
    principal = store.principal_for_fixture("alice")
    raw = store.create_session(principal, provider_tokens("unknown"))
    requests = []

    def provider(request):
        requests.append(request)
        raise httpx.ReadTimeout("controlled lost token response", request=request)

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        with TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as client:
            _, tokens, _ = connection(client, store, browser_session=raw)
            old_owner = expire_provider(store, raw).provider_authorization_id
            assert call(client, tokens["access_token"], "get_identity")["error"] == "provider_reauthorization_required"
        restarted_store = build_product_store(store.settings, objects=store.objects, role="api")
        with TestClient(create_app(store.settings, auth_store=restarted_store, http=http), base_url="http://127.0.0.1:8080") as client:
            assert call(client, tokens["access_token"], "get_identity")["error"] == "provider_reauthorization_required"
            new_raw = restarted_store.create_session(principal, provider_tokens("new-login"))
            assert restarted_store.load_session(new_raw).provider_authorization_id != old_owner
            _, new_tokens, _ = connection(client, restarted_store, browser_session=new_raw)
            assert call(client, new_tokens["access_token"], "get_identity")["status"] == "ok"
            assert call(client, tokens["access_token"], "get_identity")["error"] == "provider_reauthorization_required"
            assert len(requests) == 1


def test_late_provider_refresh_cannot_overwrite_relogin_or_recover_timed_out_claim(mcp_app):
    _, store = mcp_app
    principal = store.principal_for_fixture("alice")
    old_raw = store.create_session(principal, provider_tokens("late", expired=True))
    old_session = store.load_session(old_raw)
    entered, release = threading.Event(), threading.Event()

    def refresh(token):
        assert token == "refresh-late"
        entered.set()
        assert release.wait(10)
        return provider_tokens("late-result")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(store.fresh_provider_session, old_session, refresh)
        try:
            assert entered.wait(5)
            new_raw = store.create_session(principal, provider_tokens("replacement"))
            with store._conn() as conn:
                conn.execute("UPDATE product.provider_authorizations SET refresh_started_at=now()-interval '2 minutes' WHERE id=%s", (old_session.provider_authorization_id,))
            # A second process observes a stale claim. It may require login,
            # but cannot reset the claim and make a second provider call.
            other = build_product_store(store.settings, objects=store.objects, role="api")
            with pytest.raises(ProviderAuthError, match="provider_reauthorization_required"):
                other.fresh_provider_session(old_session, lambda _: pytest.fail("unknown refresh was replayed"))
        finally:
            release.set()
        with pytest.raises(ProviderAuthError, match="provider_reauthorization_required"):
            pending.result(timeout=10)
    assert store.load_session(new_raw).access_token == "access-replacement"
    assert store.load_session(old_raw).access_token == "access-late"


def test_provider_refresh_completion_cannot_restore_revoked_mcp_grant(mcp_app):
    _, store = mcp_app
    raw = store.create_session(store.principal_for_fixture("alice"), provider_tokens("revoke"))
    entered, release = threading.Event(), threading.Event()

    def provider(request):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"code": 0, "access_token": "access-next", "refresh_token": "refresh-next", "expires_in": 3600, "refresh_token_expires_in": 604800})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, browser_session=raw)
        expire_provider(store, raw)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(call, client, tokens["access_token"], "get_identity")
            try:
                assert entered.wait(5)
                assert client.post("/oauth/revoke", data={"client_id": cid, "token": tokens["access_token"]}).status_code == 200
            finally:
                release.set()
            assert operation.result(timeout=10)["error"] == "invalid_token"
        assert rpc(client, tokens["access_token"], "tools/list").status_code == 401
        assert client.get("/me").status_code == 200


def test_concurrent_login_owning_rotated_credentials_does_not_create_two_refresh_owners(mcp_app):
    _, store = mcp_app
    principal = store.principal_for_fixture("alice")
    original = store.create_session(principal, provider_tokens("original", expired=True))
    entered, release = threading.Event(), threading.Event()
    rotated = provider_tokens("concurrent")

    def refresh(_):
        entered.set()
        assert release.wait(10)
        return rotated

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(store.fresh_provider_session, store.load_session(original), refresh)
        try:
            assert entered.wait(5)
            # Provider delivers the same new credential through a fresh login
            # before the older refresh response reaches this process.
            newer = store.create_session(principal, rotated)
        finally:
            release.set()
        with pytest.raises(ProviderAuthError, match="provider_reauthorization_required"):
            pending.result(timeout=10)
    assert store.fresh_provider_session(store.load_session(newer), lambda _: pytest.fail("fresh login unexpectedly refreshed")).access_token == rotated.access_token
    with pytest.raises(ProviderAuthError, match="provider_reauthorization_required"):
        store.fresh_provider_session(store.load_session(original), lambda _: pytest.fail("superseded credentials replayed"))


def test_shared_provider_binding_rejects_foreign_user_even_with_valid_foreign_credentials(mcp_app):
    app, store = mcp_app
    alice = store.create_session(store.principal_for_fixture("alice"), provider_tokens("owner-alice"))
    foreign = store.create_session(store.principal_for_fixture("carol"), provider_tokens("owner-carol"))
    foreign_owner = store.load_session(foreign).provider_authorization_id
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, browser_session=alice)
        with store._conn() as conn:
            conn.execute("UPDATE product.mcp_grants SET provider_authorization_id=%s WHERE client_id=%s", (foreign_owner, cid))
        assert rpc(client, tokens["access_token"], "tools/list").status_code == 401
        with store._conn() as conn:
            from ppap_core.identity import token_hash

            conn.execute("UPDATE product.sessions SET provider_authorization_id=%s WHERE token_hash=%s", (foreign_owner, token_hash(alice)))
        assert client.get("/me").status_code == 401


def test_pending_usage_uses_owner_mcp_authority_after_browser_logout(mcp_app, seed_usage_event_factory):
    app, store = mcp_app
    alice = store.principal_for_fixture("alice")
    bob = store.principal_for_fixture("bob")
    raw = store.create_session(alice, provider_tokens("payer"))
    alice_event = seed_usage_event_factory(store, alice, "test-item", 1, False, "pending")
    seed_usage_event_factory(store, bob, "test-item", 1, False, "pending")
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, _, _ = connection(client, store, browser_session=raw, scopes="identity:read usage:read")
        store.revoke_session(raw)
        # A usage reader cannot supply the payer's authorization to report it.
        assert list(store.pending_usage_sessions(APP_ID)) == []
        raw = store.create_session(alice, provider_tokens("payer-reauthorized"))
        cid, tokens, _ = connection(client, store, browser_session=raw, scopes="identity:read audits:execute usage:read")
        store.revoke_session(raw)
        pending = list(store.pending_usage_sessions(APP_ID))
        assert len(pending) == 1 and pending[0][0] == alice_event["id"]
        assert pending[0][1].principal.user_id == alice.user_id
        assert pending[0][1].access_token == "access-payer-reauthorized"
        assert client.post("/oauth/revoke", data={"client_id": cid, "token": tokens["access_token"]}).status_code == 200
        assert list(store.pending_usage_sessions(APP_ID)) == []
