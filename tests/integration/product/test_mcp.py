"""Actual PostgreSQL + public OAuth/MCP wire contract. Feishu sign-in is separate live evidence."""

import asyncio
import concurrent.futures
import hashlib
import io
import json
import re
import secrets
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from api import create_app
from documents.ai import AIResult
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.feishu import UserTokens
from product.fixtures import APP_ID, RULE_A
from product.identity import ProviderAuthError
from product.mcp import SCOPES, pkce_challenge
from worker import process_claimed


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
    assert decided.status_code == 200, decided.text
    returned = parse_qs(urlsplit(unescape(re.search(r'<a href="([^"]+)"', decided.text)[1])).query)
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


def upload(client, token, content=None, *, file_id=None, filename="控制计划.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
    if content is None:
        stream = io.BytesIO()
        book = Workbook()
        book.active.append(["控制计划", "测试客户", "零件号 P-1"])
        book.save(stream)
        book.close()
        content = stream.getvalue()
    rid = str(uuid4())
    body = {
        "original_filename": filename,
        "byte_size": len(content),
        "content_type": content_type,
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    tool, extra = ("create_file_version", {"file_id": file_id}) if file_id else ("create_file", {})
    created = call(client, token, tool, request_id=rid, body=body, **extra)
    assert created["status"] == "ok", created
    assert call(client, token, tool, request_id=rid, body=body, **extra)["data"] == created["data"]
    changed = call(client, token, tool, request_id=rid, body={**body, "original_filename": "different.xlsx"}, **extra)
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


def reviewed_round(client, store, token, core_system):
    core_system.adopt_presence(store)
    file, _ = upload(client, token)
    draft = client.post("/drafts", json={"items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": RULE_A}]}).json()
    bound = client.put(f"/drafts/{draft['id']}", json={"writeback_target": {"provider": "feishu", "object_kind": "docx", "object_ref": "approved-document"}})
    assert bound.status_code == 200, bound.text
    accepted = client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201, accepted.text
    round_id = accepted.json()["id"]
    process_claimed(store, store.claim_job("presence-audit"), "presence-audit")
    finding = client.get(f"/rounds/{round_id}").json()["findings"][0]
    review = {"item_key": finding["item_key"], "judgment": "同意", "rationale": "原批准复核", "next_step": "", "version": 0}
    saved = client.put(f"/rounds/{round_id}/reviews", json=review)
    assert saved.status_code == 200, saved.text
    return round_id, {**review, "version": 1, "rationale": "并发网页修改"}


@pytest.mark.parametrize("fault", ["business_reply", "object_reply", "before_object", "source_reply"])
def test_url_import_reads_original_version_and_recovers_without_replacing_content(mcp_app, monkeypatch, fault):
    _, store = mcp_app
    source = "https://imports.example.test/source.xlsx?signature=controlled-private-value"
    original_bytes = b"original captured bytes"
    remote_bytes, fetches, failures = [original_bytes], [], []
    monkeypatch.setattr("product.files.public_addresses", lambda host, port: ("93.184.216.34",))

    def provider(request):
        assert request.method == "GET" and request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "imports.example.test" and "authorization" not in request.headers
        fetches.append(request.url.path)
        if fault == "source_reply" and len(fetches) == 1:
            raise httpx.ReadTimeout("controlled source reply loss", request=request)
        return httpx.Response(200, content=remote_bytes[0], headers={"Content-Type": "application/octet-stream"})

    put = store.objects.put_exclusive

    def object_fault(key, body, content_type):
        if fault in {"object_reply", "before_object"} and not failures:
            failures.append(key)
            if fault == "object_reply":
                put(key, body, content_type)
            raise OSError("controlled object write interruption")
        return put(key, body, content_type)

    monkeypatch.setattr(store.objects, "put_exclusive", object_fault)
    request_id = str(uuid4())
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        app = create_app(store.settings, auth_store=store, http=http)
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            cid, tokens, _ = connection(client, store)
            token = tokens["access_token"]
            if fault == "business_reply":
                original = app.state.mcp_gateway.request

                async def lost_reply(*a, **kw):
                    response = await original(*a, **kw)
                    if a[1:3] == ("POST", "/files/from-url"):
                        raise ConnectionError("controlled reply loss after import commit")
                    return response

                monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
            result = call(client, token, "import_url", request_id=request_id, url=source)
            assert result["status"] == "unknown", result
            before = call(client, token, "get_upload", request_id=request_id)["data"]["value"]
            assert before["state"] == ("ready" if fault == "business_reply" else "uploading")
            assert before["resume_allowed"] is (fault != "business_reply")
            assert "controlled-private-value" not in json.dumps(before)
            if fault == "source_reply":
                assert before["file"] is None
            else:
                assert before["file"]["id"] == request_id

        # Recreate the API and use only the original token/request. Nothing in
        # the new process gets an in-memory map of imported files or sources.
        remote_bytes[0] = b"source changed after the first attempt"
        with TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as restored:
            replay = call(restored, token, "import_url", request_id=request_id, url=source)
            assert replay["status"] in {"running", "unknown"} and len(fetches) == 1
            conflict = call(restored, token, "import_url", request_id=request_id, url=source + "-different")
            assert conflict["error"] == "request_conflict"
            resume = {"request_id": str(uuid4()), "upload_request_id": request_id}
            continued = call(restored, token, "resume_url_import", **resume)
            after = call(restored, token, "get_upload", request_id=request_id)["data"]["value"]
            if fault == "before_object":
                assert continued["error"] == "upload_content_conflict", continued
                assert after["state"] == "failed" and after["resume_allowed"] is False
                assert call(restored, token, "resume_url_import", request_id=str(uuid4()), upload_request_id=request_id)["error"] == "upload_not_resumable"
            else:
                assert continued["status"] == "ok" and after["state"] == "ready", (continued, after)
                file = after["file"]
                assert file["id"] == request_id
                transfer = call(restored, token, "download_file", file_id=file["file_id"], version_id=file["id"])
                downloaded = restored.get(transfer["data"]["url"], headers={"Authorization": "Bearer " + token})
                expected = remote_bytes[0] if fault == "source_reply" else original_bytes
                assert downloaded.status_code == 200 and downloaded.content == expected
            assert len(fetches) == (2 if fault in {"source_reply", "before_object"} else 1)
            count = len(fetches)
            assert call(restored, token, "resume_url_import", **resume) == continued
            for person in ("bob", "carol"):
                _, other, _ = connection(restored, store, person=person)
                assert call(restored, other["access_token"], "get_upload", request_id=request_id)["error"] == "access_denied"
                assert call(restored, other["access_token"], "resume_url_import", request_id=str(uuid4()), upload_request_id=request_id)["error"] == "access_denied"
            assert len(fetches) == count
            assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
            assert rpc(restored, token, "tools/call", {"name": "get_upload", "arguments": {"request_id": request_id}}).status_code == 401


def test_overlapping_url_resume_observes_one_import_without_starting_a_second_fetch(mcp_app, monkeypatch):
    _, store = mcp_app
    entered, release = threading.Event(), threading.Event()
    fetches = []
    monkeypatch.setattr("product.files.public_addresses", lambda host, port: ("93.184.216.34",))

    def provider(request):
        fetches.append(request.url.path)
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, content=b"captured once", headers={"Content-Type": "text/plain"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        request_id = str(uuid4())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(call, client, token, "import_url", request_id=request_id, url="https://imports.example.test/source.txt")
            try:
                assert entered.wait(10)
                receipt = call(client, token, "get_upload", request_id=request_id)["data"]["value"]
                assert receipt["state"] == "uploading" and receipt["file"] is None
                resumed = call(client, token, "resume_url_import", request_id=str(uuid4()), upload_request_id=request_id)
                assert resumed["status"] == "ok" and resumed["data"]["status"] == "staging", resumed
                assert len(fetches) == 1
            finally:
                release.set()
            assert original.result(timeout=10)["data"]["status"] == "ready"
        assert call(client, token, "get_upload", request_id=request_id)["data"]["value"]["file"]["id"] == request_id
        missing = call(client, token, "resume_url_import", request_id=str(uuid4()), upload_request_id=str(uuid4()))
        assert missing["error"] == "url_import_not_found" and len(fetches) == 1


@pytest.mark.parametrize("redirect", [False, True])
def test_rejected_url_source_is_failed_and_cannot_be_resumed(mcp_app, monkeypatch, redirect):
    _, store = mcp_app
    fetches = []
    if redirect:
        monkeypatch.setattr("product.files.public_addresses", lambda host, port: ("93.184.216.34",))

    def provider(request):
        fetches.append(request.url.path)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        request_id = str(uuid4())
        result = call(client, token, "import_url", request_id=request_id, url="https://imports.example.test/source" if redirect else "http://127.0.0.1/private")
        assert result["error"] == ("redirect_not_allowed" if redirect else "blocked_host"), result
        receipt = call(client, token, "get_upload", request_id=request_id)["data"]["value"]
        assert receipt["state"] == "failed" and receipt["file"] is None and receipt["resume_allowed"] is False
        assert call(client, token, "resume_url_import", request_id=str(uuid4()), upload_request_id=request_id)["error"] == "upload_not_resumable"
        assert len(fetches) == (1 if redirect else 0)


@pytest.mark.parametrize("extension", ["json", "xlsx", "docx"])
def test_rule_parse_recovers_original_job_after_reply_loss_and_api_restart(mcp_app, monkeypatch, extension):
    app, store = mcp_app
    body = {
        "kind": "engine",
        "type": "控制计划",
        "rule_profile": "v3",
        "customers": ["测试客户"],
        "models": [],
        "parts": [],
        "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
    }
    stream = io.BytesIO()
    if extension == "xlsx":
        document = Workbook()
        document.active.append(["规则编号", "审核要求"])
        document.active.append(["1", "必须填写零件号"])
        document.save(stream)
        document.close()
    elif extension == "docx":
        from docx import Document

        document = Document()
        document.add_paragraph("控制计划规则：必须填写零件号")
        document.save(stream)
    else:
        stream.write(json.dumps(body, ensure_ascii=False).encode())
    content = stream.getvalue()
    parse_id = str(uuid4())
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        source, _ = upload(client, token, content, filename="控制计划规则." + extension, content_type="application/octet-stream")
        arguments = {"request_id": parse_id, "body": {"request_id": parse_id, "source_file_id": source["file_id"], "source_file_version_id": source["id"], "document_type": "控制计划"}}
        original_request = app.state.mcp_gateway.request

        async def lost_reply(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            if args[1:3] == ("POST", "/rules/parse"):
                raise ConnectionError("controlled loss after durable parse acceptance")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "parse_rule", **arguments)["status"] == "unknown"
    requests = []

    class ParseAI:
        def __call__(self, operation, **request):
            requests.append({"operation": operation, **request})
            return AIResult({"document_type": "控制计划"}) if operation == "document_classification" else AIResult(body)

    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        pending = call(restored, token, "get_rule_parse", parse_id=parse_id)["data"]["value"]
        assert pending["id"] == parse_id and pending["status"] == "queued"
        assert call(restored, token, "parse_rule", **arguments)["status"] in {"running", "unknown"}
        job = worker.claim_job("mcp-rule-parse")
        assert job.id == parse_id and job.kind == "rule_parse"
        assert worker.claim_job("no-duplicate-parser") is None
        process_claimed(worker, job, "mcp-rule-parse", rule_ai=ParseAI())
        parsed = call(restored, token, "get_rule_parse", parse_id=parse_id)["data"]["value"]
        assert parsed["status"] == "succeeded" and parsed["result"]["body"] == body
        assert parsed["result"]["source_file_version_id"] == source["id"]
        if extension == "json":
            assert requests == []
        else:
            assert sum(row["operation"] == "rule_extraction" for row in requests) == 1
            assert "必须填写零件号" in requests[-1]["prompt"]
        sources = call(restored, token, "get_rule_sources", file_id=source["file_id"], version_id=source["id"])["data"]["value"]
        assert any("必须填写零件号" in unit["excerpt"] for unit in sources["units"])
        # Parsing alone does not publish its candidate.
        with store._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.rule_versions WHERE source_file_version_id = %s", (source["id"],)).fetchone()[0] == 0
        for person in ("bob", "carol"):
            _, other, _ = connection(restored, store, person=person, scopes=" ".join(SCOPES))
            assert call(restored, other["access_token"], "get_rule_parse", parse_id=parse_id)["error"] == "access_denied"
            assert call(restored, other["access_token"], "get_rule_sources", file_id=source["file_id"], version_id=source["id"])["error"] == "access_denied"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert rpc(restored, token, "tools/call", {"name": "get_rule_parse", "arguments": {"parse_id": parse_id}}).status_code == 401


@pytest.mark.parametrize("revision", [False, True])
def test_prepare_task_reads_original_identity_after_reply_loss_and_restart(mcp_app, monkeypatch, revision):
    app, store = mcp_app
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    request_id = str(uuid4())
    task_id = request_id
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _ = upload(client, token)
        validation_id = str(uuid4())
        validation = {"request_id": validation_id, "source_file_id": file["file_id"], "source_file_version_id": file["id"]}
        assert call(client, token, "validate_file", request_id=validation_id, body=validation)["status"] == "ok"
        process_claimed(worker, worker.claim_job("prepare-precheck"), "prepare-precheck")
        body = {
            "request_id": request_id,
            "analysis_mode": "standard",
            "customer_preference": None,
            "files": [{"file_version_id": file["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None}],
        }
        if revision:
            initial = call(client, token, "prepare_task", request_id=request_id, body=body)
            assert initial["status"] == "ok" and initial["data"]["id"] == task_id
            request_id = str(uuid4())
            body = {**body, "request_id": request_id, "task_id": task_id, "expected_revision": initial["data"]["input_revision"]}
        original_request = app.state.mcp_gateway.request

        async def lost_reply(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            if args[1:3] == ("POST", "/audit-tasks"):
                assert response.status_code == 202
                raise ConnectionError("controlled loss after recognition was committed")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "prepare_task", request_id=request_id, body=body)["status"] == "unknown"
    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        task = call(restored, token, "get_task", task_id=task_id)["data"]["value"]
        assert task["id"] == task_id and task["input_revision"] == int(revision)
        assert task["round_id"] is None
        recognition = call(restored, token, "get_recognition", task_id=task_id, request_id=request_id)
        assert recognition["status"] == "ok", recognition
        assert call(restored, token, "prepare_task", request_id=request_id, body=body)["status"] in {"running", "unknown"}
        assert call(restored, token, "get_task", task_id=task_id)["data"]["value"] == task
        with store._conn() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM engine.jobs WHERE kind = 'file_analysis' AND payload->>'task_id' = %s AND payload->>'task_revision' = %s",
                    (task_id, str(int(revision))),
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT count(*) FROM product.audit_drafts WHERE id = %s", (task_id,)).fetchone()[0] == 1
        for person in ("bob", "carol"):
            _, other, _ = connection(restored, store, person=person, scopes=" ".join(SCOPES))
            assert call(restored, other["access_token"], "get_task", task_id=task_id)["status"] == "error"
            assert call(restored, other["access_token"], "get_recognition", task_id=task_id, request_id=request_id)["status"] == "error"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert rpc(restored, token, "tools/call", {"name": "get_task", "arguments": {"task_id": task_id}}).status_code == 401


def completed_mcp_task(client, store, token, core_system, *, publish_new_rule=True, prepare_draft=None, fail_pdf=False):
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    file, _ = upload(client, token)
    validation_id = str(uuid4())
    checked = call(client, token, "validate_file", request_id=validation_id, body={"request_id": validation_id, "source_file_id": file["file_id"], "source_file_version_id": file["id"]})
    assert checked["status"] == "ok", checked
    process_claimed(worker, worker.claim_job("share-precheck"), "share-precheck")
    publish = {
        "request_id": str(uuid4()),
        "body": {
            "title": "分享测试规则",
            "source_file_version_id": file["id"],
            "body": {
                "kind": "engine",
                "type": "控制计划",
                "rule_profile": "v3",
                "customers": ["测试客户"],
                "models": [],
                "parts": [],
                "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
            },
        },
    }
    if publish_new_rule:
        source, _ = upload(client, token, json.dumps(publish["body"]["body"], ensure_ascii=False).encode(), filename="审核规则.json", content_type="application/json")
        parse_id = str(uuid4())
        parsing = {"request_id": parse_id, "body": {"request_id": parse_id, "source_file_id": source["file_id"], "source_file_version_id": source["id"], "document_type": "控制计划"}}
        assert call(client, token, "parse_rule", **parsing)["status"] == "ok"
        assert call(client, token, "parse_rule", **parsing)["status"] == "ok"
        job = worker.claim_job("share-rule-parse")
        assert job.kind == "rule_parse" and job.id == parse_id
        process_claimed(worker, job, "share-rule-parse")
        parsed = call(client, token, "get_rule_parse", parse_id=parse_id)["data"]["value"]
        assert parsed["status"] == "succeeded" and parsed["result"]["source_file_version_id"] == source["id"]
        assert worker.claim_job("no-duplicate-rule-parse") is None
        sources = call(client, token, "get_rule_sources", file_id=source["file_id"], version_id=source["id"])["data"]["value"]
        unit = sources["units"][0]
        clause = {
            "id": "1",
            "title": "身份",
            "text": unit["excerpt"],
            "source": source["original_filename"],
            "original": unit["excerpt"],
            "location": unit["location"],
            "checked": True,
            "provenance": {**unit, "fileVersionId": source["id"]},
        }
        publish["body"] = {**parsed["result"], "case_context": {"registration": json.dumps({"basis": {"source": {"filename": source["original_filename"]}, "clauses": [clause]}})}}
        approve(client, call(client, token, "publish_rule", **publish))
        published = call(client, token, "publish_rule", **publish)
        assert published["status"] == "ok", published
        core_system.adopt_presence(store, published["data"]["id"])
    request_id = str(uuid4())
    inputs = {
        "request_id": request_id,
        "analysis_mode": "standard",
        "customer_preference": None,
        "files": [{"file_version_id": file["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None}],
    }
    if prepare_draft is not None:
        inputs = prepare_draft(inputs)
    prepared = call(
        client,
        token,
        "prepare_task",
        request_id=request_id,
        body=inputs,
    )
    assert prepared["status"] == "ok", prepared

    class MetadataAI:
        def __call__(self, operation, **request):
            return AIResult({"document_type": "控制计划"}) if operation == "document_classification" else AIResult({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"})

    process_claimed(worker, worker.claim_job("share-match"), "share-match", rule_ai=MetadataAI())
    task_id = prepared["data"]["id"]
    pending = call(client, token, "get_task", task_id=task_id)["data"]["value"]
    confirm = {"task_id": task_id, "request_id": str(uuid4()), "expected_revision": 0, "expected_confirmation_basis_digest": pending["confirmation_basis_digest"]}
    approve(client, call(client, token, "confirm_task", **confirm))
    accepted = call(client, token, "confirm_task", **confirm)
    assert accepted["status"] == "ok", accepted
    guest_calls = core_system.calls
    process_claimed(worker, worker.claim_job("share-audit"), "share-audit")
    assert core_system.calls == guest_calls + 1
    job = worker.claim_job("share-pdf")
    assert job.kind == "report_pdf"
    if fail_pdf:
        assert worker.complete_job(job, "share-pdf", {"error": "controlled_pdf_failure"}, "failed")
    else:
        process_claimed(worker, job, "share-pdf")
    task = call(client, token, "get_task", task_id=task_id)["data"]["value"]
    assert task["state"] == "completed", task
    row = task["files"][0]
    report = row["audit"]["report"]
    assert report["state"] == ("failed" if fail_pdf else "ready"), report
    reference = {"task_id": task_id, "round_id": task["round_id"], "file_version_id": file["id"], "result_attempt": row["audit"]["result_attempt"], "report_id": report["report_id"]}
    return worker, task, reference, report["artifact"]


def test_mcp_pdf_and_zip_recovery_preserve_original_results_after_lost_replies(mcp_app, core_system, monkeypatch):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        worker, first, reference, artifact = completed_mcp_task(client, store, token, core_system, fail_pdf=True)
        assert artifact is None
        _, second, second_reference, _ = completed_mcp_task(client, store, token, core_system, publish_new_rule=False)
        rounds = {row["round_id"]: call(client, token, "get_round", round_id=row["round_id"])["data"]["value"] for row in (first, second)}
        regeneration = {"request_id": str(uuid4()), "reference": reference}
        approve(client, call(client, token, "regenerate_report", **regeneration))
        original = app.state.mcp_gateway.request

        async def lose_regeneration_reply(*args, **kwargs):
            response = await original(*args, **kwargs)
            if args[1:3] == ("POST", f"/audit-tasks/{reference['task_id']}/reports/{reference['report_id']}/regenerations"):
                assert response.status_code == 201
                raise ConnectionError("controlled loss after PDF regeneration accepted")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lose_regeneration_reply)
        assert call(client, token, "regenerate_report", **regeneration)["status"] == "unknown"
    next_app = create_app(store.settings, auth_store=store)
    with TestClient(next_app, base_url="http://127.0.0.1:8080") as restarted:
        restarted.cookies.set("ppap_session", store.create_session(store.principal_for_fixture("alice")))
        receipt = call(restarted, token, "get_report_regeneration", **regeneration)["data"]["value"]
        assert receipt["receipt"]["request_id"] == regeneration["request_id"]
        assert receipt["reference"] == reference and receipt["report"]["state"] == "pending"
        assert call(restarted, token, "regenerate_report", **regeneration)["status"] in {"running", "unknown"}
        pdf_job = worker.claim_job("recover-mcp-pdf")
        assert pdf_job.id == reference["report_id"] and pdf_job.attempt == 2
        assert worker.claim_job("no-second-pdf") is None
        process_claimed(worker, pdf_job, "recover-mcp-pdf")
        ready = call(restarted, token, "get_report_regeneration", **regeneration)["data"]["value"]
        assert ready["report"]["state"] == "ready"
        pdfs = []
        for ref in (reference, second_reference):
            task = call(restarted, token, "get_task", task_id=ref["task_id"])["data"]["value"]
            artifact = task["files"][0]["audit"]["report"]["artifact"]
            url = call(restarted, token, "download_report", reference=ref, artifact_id=artifact["artifact_id"])["data"]["url"]
            response = restarted.get(url, headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200 and response.content.startswith(b"%PDF-")
            pdfs.append(response.content)
        package_request = str(uuid4())
        reports = [reference, second_reference]
        created = call(restarted, token, "create_report_package", request_id=package_request, reports=reports)
        assert created["status"] == "ok"
        zip_job = worker.claim_job("fail-mcp-zip")
        assert zip_job.kind == "report_zip"
        assert worker.complete_job(zip_job, "fail-mcp-zip", {"error": "controlled_zip_failure"}, "failed")
        failed = call(restarted, token, "get_report_package", request_id=package_request)["data"]["value"]
        assert failed["state"] == "failed" and failed["retry_allowed"]
        retry = {"source_request_id": package_request, "request_id": str(uuid4()), "package_id": failed["package_id"], "expected_attempt": failed["attempt"]}
        approve(restarted, call(restarted, token, "retry_report_package", **retry))
        original_retry = next_app.state.mcp_gateway.request

        async def lose_package_retry_reply(*args, **kwargs):
            response = await original_retry(*args, **kwargs)
            if args[1:3] == ("POST", f"/audit-report-packages/{package_request}/retries"):
                assert response.status_code == 202
                raise ConnectionError("controlled loss after ZIP recovery accepted")
            return response

        monkeypatch.setattr(next_app.state.mcp_gateway, "request", lose_package_retry_reply)
        assert call(restarted, token, "retry_report_package", **retry)["status"] == "unknown"
    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        pending = call(restored, token, "get_report_package", request_id=retry["request_id"])["data"]["value"]
        assert pending["package_id"] == failed["package_id"] and pending["reports"] == failed["reports"]
        assert pending["state"] == "pending"
        assert call(restored, token, "retry_report_package", **retry)["status"] in {"running", "unknown"}
        job = worker.claim_job("recover-mcp-zip")
        assert job.id == zip_job.id and job.attempt == zip_job.attempt + 1
        assert worker.claim_job("no-second-zip") is None
        process_claimed(worker, job, "recover-mcp-zip")
        package = call(restored, token, "get_report_package", request_id=retry["request_id"])["data"]["value"]
        assert package["state"] == "ready" and not package["retry_allowed"]
        transfer = call(restored, token, "download_report_package", request_id=retry["request_id"], package_id=package["package_id"], artifact_id=package["artifact"]["artifact_id"])
        url = transfer["data"]["url"]
        downloaded = restored.get(url, headers={"Authorization": "Bearer " + token})
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert sorted(archive.read(name) for name in archive.namelist() if name.endswith(".pdf")) == sorted(pdfs)
        for round_id, frozen in rounds.items():
            assert call(restored, token, "get_round", round_id=round_id)["data"]["value"] == frozen
        assert call(restored, token, "list_usage_events")["data"]["value"]["events"] == []
        assert len(call(restored, token, "list_tasks")["data"]["value"]["tasks"]) == 2
        for person in ("bob", "carol"):
            _, other, _ = connection(restored, store, person=person, scopes=" ".join(SCOPES))
            assert call(restored, other["access_token"], "get_report_regeneration", **regeneration)["status"] == "error"
            assert call(restored, other["access_token"], "get_report_package", request_id=retry["request_id"])["status"] == "error"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert restored.get(url, headers={"Authorization": "Bearer " + token}).status_code == 401


def test_mcp_preparation_carries_writeback_target_through_restart_acceptance_and_lost_send_reply(mcp_app, core_system, monkeypatch):
    """Use only MCP business tools from preparation to writeback; HTTP effects are controlled."""
    _, store = mcp_app
    target = {"provider": "feishu", "object_kind": "docx", "object_ref": "mcp-prepared-document"}
    effects = []

    def provider(request):
        assert request.method == "POST"
        assert target["object_ref"] in request.url.path
        effects.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"code": 0, "data": {}})

    now = datetime.now(timezone.utc)
    session = store.create_session(store.principal_for_fixture("alice"), UserTokens("controlled-access", "controlled-refresh", now + timedelta(hours=1), now + timedelta(days=7)))
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        app = create_app(store.settings, auth_store=store, http=http)
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES), browser_session=session)
            token = tokens["access_token"]

            def prepare_draft(inputs):
                preparation = {"version": 1, "predecessor_id": None, "uploads": [], **{key: inputs[key] for key in ("files", "analysis_mode", "customer_preference")}}
                draft_id = str(uuid4())
                created = call(client, token, "create_draft", request_id=draft_id, body={"upload_preparation": preparation})
                assert created["status"] == "ok" and created["data"]["id"] == draft_id
                save = {"draft_id": draft_id, "request_id": str(uuid4()), "body": {"upload_preparation": preparation, "expected_revision": 0, "writeback_target": target}}
                original = app.state.mcp_gateway.request

                async def lose_saved_reply(*args, **kwargs):
                    response = await original(*args, **kwargs)
                    if args[1:3] == ("PUT", f"/drafts/{draft_id}"):
                        assert response.status_code == 200
                        raise ConnectionError("controlled loss after preparation and target committed")
                    return response

                with monkeypatch.context() as patch:
                    patch.setattr(app.state.mcp_gateway, "request", lose_saved_reply)
                    assert call(client, token, "save_draft", **save)["status"] == "unknown"
                with TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as restarted:
                    saved = call(restarted, token, "get_draft", draft_id=draft_id)["data"]["value"]
                    assert saved["preparation_revision"] == 1 and saved["writeback"]["available"]
                    assert "object_ref" not in saved["writeback"]
                    assert call(restarted, token, "save_draft", **save)["status"] in {"running", "unknown"}
                    assert call(restarted, token, "get_draft", draft_id=draft_id)["data"]["value"] == saved
                stale = call(client, token, "save_draft", **{**save, "request_id": str(uuid4()), "body": {**save["body"], "writeback_target": {**target, "object_ref": "stale-document"}}})
                assert stale["status"] == "error" and stale["error"] == "draft_revision_conflict"
                return {**inputs, "source_draft_id": draft_id, "expected_draft_revision": 1}

            _, task, _, _ = completed_mcp_task(client, store, token, core_system, prepare_draft=prepare_draft)
            round_id = task["round_id"]
            assert store.round_writeback(round_id) == target
            original_round = call(client, token, "get_round", round_id=round_id)["data"]["value"]
            assert original_round["writeback"]["available"]
            assert effects == []  # Saving, promoting and accepting never write externally.
            arguments = {"round_id": round_id, "request_id": str(uuid4())}
            approve(client, call(client, token, "writeback_feishu_report", **arguments))
            original = app.state.mcp_gateway.request

            async def lose_writeback_reply(*args, **kwargs):
                response = await original(*args, **kwargs)
                if args[1:3] == ("POST", f"/integrations/feishu/rounds/{round_id}/writeback"):
                    assert response.status_code == 200
                    raise ConnectionError("controlled loss after provider acknowledged writeback")
                return response

            monkeypatch.setattr(app.state.mcp_gateway, "request", lose_writeback_reply)
            assert call(client, token, "writeback_feishu_report", **arguments)["status"] == "unknown"
            assert effects and round_id in json.dumps(effects)
            sent = list(effects)
        with TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as restarted:
            assert call(restarted, token, "get_operation", request_id=arguments["request_id"])["status"] in {"running", "unknown"}
            assert call(restarted, token, "writeback_feishu_report", **arguments)["status"] in {"running", "unknown"}
            assert effects == sent
            assert store.round_writeback(round_id) == target
            assert call(restarted, token, "get_round", round_id=round_id)["data"]["value"] == original_round
            for person in ("bob", "carol"):
                _, other, _ = connection(restarted, store, person=person, scopes=" ".join(SCOPES))
                assert call(restarted, other["access_token"], "get_draft", draft_id=task["id"])["status"] == "error"
                assert call(restarted, other["access_token"], "writeback_feishu_report", **arguments)["status"] == "error"
            assert restarted.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
            assert rpc(restarted, token, "tools/call", {"name": "writeback_feishu_report", "arguments": arguments}).status_code == 401
            assert effects == sent


@pytest.mark.parametrize("suite_choice", ["original", "newer"])
def test_mcp_review_replacement_and_linked_reaudit_preserve_frozen_suite_history(mcp_app, core_system, monkeypatch, suite_choice):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        worker, first_task, first_ref, _ = completed_mcp_task(client, store, token, core_system)
        round_id = first_task["round_id"]
        before = call(client, token, "get_round", round_id=round_id)["data"]["value"]
        old_file = first_task["files"][0]["file"]
        old_rule_id = first_task["files"][0]["audit"]["rule"]["id"]
        old_rule = call(client, token, "get_rule", rule_version_id=old_rule_id)["data"]["value"]
        old_transfer = call(client, token, "download_file", file_id=old_file["file_id"], version_id=old_file["id"])["data"]["url"]
        original_bytes = client.get(old_transfer, headers={"Authorization": "Bearer " + token}).content
        assert hashlib.sha256(original_bytes).hexdigest() == old_file["sha256"]
        review = {
            "round_id": round_id,
            "request_id": str(uuid4()),
            "body": {
                "item_key": before["findings"][0]["item_key"],
                "judgment": "需修改",
                "rationale": "已核对原版本，需要补件",
                "next_step": "补交新版控制计划",
                "version": 0,
                "citations": [{"file_version_id": old_file["id"], "location": "Sheet!A1", "excerpt": "控制计划"}],
            },
        }
        approve(client, call(client, token, "submit_review", **review))
        reviewed = call(client, token, "submit_review", **review)
        assert reviewed["status"] == "ok" and reviewed["provenance"]["client_id"] == cid
        assert reviewed["data"]["findings"][0]["status"] == before["findings"][0]["status"]
        frozen_old = call(client, token, "get_round", round_id=round_id)["data"]["value"]
        assert frozen_old["findings"][0]["review"]["citations"] == review["body"]["citations"]
        # Rule-version provenance stays immutable. Editing an adopted Checker
        # goes through a Suite candidate, not a stale rule publication that
        # would overwrite the current Suite head.
        from product.store.suite_releases import SuiteReleaseStore

        principal = store.principal_for_fixture("alice")
        releases = SuiteReleaseStore(store.settings, store.objects, role="api")
        old_suite_id = before["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"]
        suite_key = before["inputs"][0]["rule"]["body"]["suite_key"]
        selected = releases.active(principal, suite_key)
        rules = json.loads(releases.suites.resolve_suite(old_suite_id, principal.tenant_id).files["rules.json"])
        changed_text = "必须填写修订后的零件号"
        rules["rules"][0]["rule_to_check"] = changed_text
        candidate = releases.save_candidate(principal, old_suite_id, {"rules.json": json.dumps(rules, ensure_ascii=False)})
        chosen_suite_id = old_suite_id
        if suite_choice == "newer":
            releases.publish(principal, suite_key, candidate["id"], selected["revision"], str(uuid4()), force_unverified=True)
            chosen_suite_id = candidate["id"]
        chosen_rule = old_rule_id
        prepared = call(client, token, "get_reaudit_inputs", round_id=round_id)["data"]["value"]
        old_input = prepared["files"][0]
        assert old_input["preferred_rule_version_id"] == old_rule_id
        assert {row["id"] for row in old_input["rule_options"]} == {old_rule_id}

        content = io.BytesIO()
        book = Workbook()
        book.active.append(["控制计划", "测试客户", "零件号 P-1", "补件修订"])
        book.save(content)
        book.close()
        replacement, _ = upload(client, token, content.getvalue(), file_id=old_file["file_id"])
        assert replacement["id"] != old_file["id"] and replacement["sha256"] != old_file["sha256"]
        validation_id = str(uuid4())
        validated = call(
            client, token, "validate_file", request_id=validation_id, body={"request_id": validation_id, "source_file_id": replacement["file_id"], "source_file_version_id": replacement["id"]}
        )
        assert validated["status"] == "ok", validated
        process_claimed(worker, worker.claim_job("replacement-check"), "replacement-check")
        request_id = str(uuid4())
        body = {
            "request_id": request_id,
            "analysis_mode": "standard",
            "customer_preference": prepared["customer_preference"],
            "predecessor_id": round_id,
            "files": [
                {"file_version_id": old_file["id"], "validation_id": old_input["validation_id"], "selection": "removed", "removal_reason": "本次使用补件替换"},
                {
                    "file_version_id": replacement["id"],
                    "validation_id": validation_id,
                    "selection": "selected",
                    "removal_reason": None,
                    "replacement_of_file_version_id": old_file["id"],
                    "preferred_rule_version_id": chosen_rule,
                },
            ],
        }
        linked = call(client, token, "prepare_task", request_id=request_id, body=body)
        assert linked["status"] == "ok", linked
        assert call(client, token, "prepare_task", request_id=request_id, body=body)["data"] == linked["data"]

        class MetadataAI:
            def __call__(self, operation, **request):
                return AIResult({"document_type": "控制计划"}) if operation == "document_classification" else AIResult({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"})

        process_claimed(worker, worker.claim_job("replacement-match"), "replacement-match", rule_ai=MetadataAI())
        next_task_id = linked["data"]["id"]
        matched = call(client, token, "get_task", task_id=next_task_id)["data"]["value"]
        assert matched["predecessor_id"] == round_id
        assert matched["files"][1]["match"]["rule"]["id"] == chosen_rule
        confirm = {"task_id": next_task_id, "request_id": str(uuid4()), "expected_revision": matched["input_revision"], "expected_confirmation_basis_digest": matched["confirmation_basis_digest"]}
        approve(client, call(client, token, "confirm_task", **confirm))
        original_request = app.state.mcp_gateway.request

        async def lose_acceptance_reply(*a, **kw):
            response = await original_request(*a, **kw)
            if a[1:3] == ("POST", f"/audit-tasks/{next_task_id}/confirm"):
                raise ConnectionError("controlled reply loss after reaudit acceptance")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lose_acceptance_reply)
        assert call(client, token, "confirm_task", **confirm)["status"] == "unknown"

    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        # The persistent Task is the acceptance authority even if the MCP reply
        # was lost. A second invocation must not enqueue another audit.
        accepted = call(restored, token, "get_task", task_id=next_task_id)["data"]["value"]
        next_round_id = accepted["round_id"]
        assert next_round_id and next_round_id != round_id
        assert call(restored, token, "confirm_task", **confirm)["status"] in {"running", "unknown"}
        job = worker.claim_job("replacement-audit")
        assert job.round_id == next_round_id and worker.claim_job("no-duplicate-audit") is None
        guest_calls = core_system.calls
        process_claimed(worker, job, "replacement-audit")
        assert core_system.calls == guest_calls + 1
        process_claimed(worker, worker.claim_job("replacement-pdf"), "replacement-pdf")
        finished = call(restored, token, "get_task", task_id=next_task_id)["data"]["value"]
        assert finished["state"] == "completed"
        current = call(restored, token, "get_round", round_id=next_round_id)["data"]["value"]
        assert current["predecessor_id"] == round_id
        assert current["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"] == chosen_suite_id
        expected_text = changed_text if suite_choice == "newer" else before["inputs"][0]["rule"]["body"]["rules"][0]["rule_to_check"]
        assert current["inputs"][0]["rule"]["body"]["rules"][0]["rule_to_check"] == expected_text
        assert [(row["file_version_id"], row["rule_version_id"]) for row in current["inputs"]] == [(replacement["id"], chosen_rule)]
        assert all(finding["review"] is None for finding in current["findings"])
        assert call(restored, token, "get_round", round_id=round_id)["data"]["value"] == frozen_old
        assert call(restored, token, "get_rule", rule_version_id=old_rule_id)["data"]["value"] == old_rule
        assert restored.get(old_transfer, headers={"Authorization": "Bearer " + token}).content == original_bytes
        usage = call(restored, token, "list_usage_events")["data"]["value"]
        assert usage["events"] == []  # This fixture explicitly disables charging.
        tasks = call(restored, token, "list_tasks")["data"]["value"]["tasks"]
        assert {task["id"] for task in tasks} == {first_task["id"], next_task_id}
        for person in ("bob", "carol"):
            _, other, _ = connection(restored, store, person=person, scopes=" ".join(SCOPES))
            assert call(restored, other["access_token"], "get_reaudit_inputs", round_id=round_id)["error"] == "access_denied"
            assert call(restored, other["access_token"], "get_round", round_id=next_round_id)["error"] == "access_denied"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert restored.get(old_transfer, headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize(("kind", "edit_at"), [("link", "none"), ("pdf", "none"), ("zip", "none"), ("link", "before_capture"), ("link", "after_capture")])
def test_mcp_report_delivery_freezes_approved_sources_and_retries_only_failed(mcp_app, core_system, monkeypatch, kind, edit_at):
    """Real PG/Core, workers, PDF/ZIP and MCP; Feishu HTTP and guest OS are controlled."""
    _, store = mcp_app
    principal = store.principal_for_fixture("alice")
    store.settings.feishu_app_secret = "controlled-secret"
    store.save_app_ticket(APP_ID, "controlled-ticket")
    messages, attachments = [], []

    def provider(request):
        path = request.url.path
        if path.endswith("/authen/v1/user_info"):
            data = {"open_id": principal.subject, "tenant_key": principal.tenant_key, "name": "受控接收人"}
        elif path.endswith("/auth/v3/app_access_token"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "controlled-app"})
        elif path.endswith("/auth/v3/tenant_access_token"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "controlled-tenant"})
        elif "/contact/v3/users/" in path:
            data = {
                "user": {
                    "open_id": path.rsplit("/", 1)[1],
                    "tenant_key": principal.tenant_key,
                    "name": "受控接收人",
                    "status": {
                        "is_activated": True,
                        "is_resigned": False,
                        "is_frozen": False,
                        "is_exited": False,
                        "is_unjoin": False,
                    },
                }
            }
        elif path.endswith("/im/v1/files"):
            attachments.append(request.content)
            data = {"file_key": "controlled-file-key"}
        elif path.endswith("/im/v1/messages"):
            message = json.loads(request.content)
            messages.append(message)
            target = message["receive_id"]
            if target == "ou_unknown":
                raise httpx.ReadTimeout("controlled lost provider reply", request=request)
            if target == "ou_failed" and sum(m["receive_id"] == target for m in messages) == 1:
                return httpx.Response(400, json={"code": 230013})
            data = {"message_id": "controlled-message-" + str(len(messages))}
        else:
            raise AssertionError("unexpected provider path: " + path)
        return httpx.Response(200, json={"code": 0, "data": data})

    now = datetime.now(timezone.utc)
    session = store.create_session(principal, UserTokens("controlled-access", "controlled-refresh", now + timedelta(hours=1), now + timedelta(days=7)))
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        app = create_app(store.settings, auth_store=store, http=http)
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES), browser_session=session)
            token = tokens["access_token"]
            worker, task, reference, artifact = completed_mcp_task(client, store, token, core_system)
            pdf_transfer = call(client, token, "download_report", reference=reference, artifact_id=artifact["artifact_id"])
            assert pdf_transfer["status"] == "ok", pdf_transfer
            pdf = client.get(pdf_transfer["data"]["url"], headers={"Authorization": "Bearer " + token})
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
            content = {"kind": kind, "artifact": artifact if kind == "pdf" else None, "package_id": None}
            reports = [reference]
            if kind == "zip":
                _, _, second_reference, _ = completed_mcp_task(client, store, token, core_system, publish_new_rule=False)
                reports.append(second_reference)
                package_request = str(uuid4())
                created = call(client, token, "create_report_package", request_id=package_request, reports=reports)
                assert created["status"] == "ok", created
                process_claimed(worker, worker.claim_job("share-zip"), "share-zip")
                package = call(client, token, "get_report_package", request_id=package_request)["data"]["value"]
                assert package["state"] == "ready", package
                content.update(artifact=package["artifact"], package_id=package["package_id"])
                transfer = call(client, token, "download_report_package", request_id=package_request, package_id=package["package_id"], artifact_id=package["artifact"]["artifact_id"])
                archive = client.get(transfer["data"]["url"], headers={"Authorization": "Bearer " + token})
                assert archive.status_code == 200
                with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
                    assert any(zipped.read(name) == pdf.content for name in zipped.namelist() if name.endswith(".pdf"))

            request_id = str(uuid4())
            targets = [{"kind": "user", "id": value} for value in (principal.subject, "ou_failed", "ou_unknown")]
            args = {"request_id": request_id, "body": {"request_id": request_id, "organization_id": principal.tenant_key, "reports": reports, "targets": targets, "content": content}}
            approve(client, call(client, token, "share_reports", **args))
            edits = []

            def edit():
                if not edits:
                    finding = client.get(f"/rounds/{task['round_id']}").json()["findings"][0]
                    response = client.put(
                        f"/rounds/{task['round_id']}/reviews", json={"item_key": finding["item_key"], "judgment": "需修改", "rationale": "分享期间网页复核", "next_step": "", "version": 0}
                    )
                    assert response.status_code == 200, response.text
                    edits.append(response.json())

            if edit_at == "before_capture":
                original = app.state.mcp_gateway.request

                async def edit_before_share(*a, **kw):
                    if kw.get("operation_id") and a[2] == "/audit-report-shares":
                        await asyncio.to_thread(edit)
                    return await original(*a, **kw)

                monkeypatch.setattr(app.state.mcp_gateway, "request", edit_before_share)
            elif edit_at == "after_capture":
                from product.store.report_sharing import ReportShareStore

                original_accept = ReportShareStore.accept

                def edit_after_share(self, *a, **kw):
                    accepted = original_accept(self, *a, **kw)
                    edit()
                    return accepted

                monkeypatch.setattr(ReportShareStore, "accept", edit_after_share)

            sent = call(client, token, "share_reports", **args)
            if edit_at == "before_capture":
                assert sent["status"] == "error" and sent["error"] == "confirmation_content_changed", sent
                assert messages == [] and attachments == [] and len(edits) == 1
                return
            assert sent["status"] == "ok", sent
            assert [item["state"] for item in sent["data"]["deliveries"]] == ["sent", "failed", "unknown"]
            assert len(messages) == 3 and len(attachments) == (0 if kind == "link" else 1)
            if kind == "pdf":
                assert pdf.content in attachments[0]
            elif kind == "zip":
                assert archive.content in attachments[0]
            if edit_at == "after_capture":
                assert len(edits) == 1
                assert "分享期间网页复核" not in json.dumps(messages, ensure_ascii=False)
            assert call(client, token, "share_reports", **args)["data"] == sent["data"]
            receipt = call(client, token, "get_share_receipt", request_id=request_id)["data"]["value"]
            assert receipt["deliveries"] == sent["data"]["deliveries"] and len(messages) == 3
            retry = {"share_request_id": request_id, "request_id": str(uuid4()), "target": targets[1]}
            approve(client, call(client, token, "retry_share_target", **retry))
            recovered = call(client, token, "retry_share_target", **retry)
            assert recovered["status"] == "ok", recovered
            assert [item["state"] for item in recovered["data"]["deliveries"]] == ["sent", "sent", "unknown"]
            assert len(messages) == 4 and messages[-1]["receive_id"] == "ou_failed"
            assert call(client, token, "retry_share_target", **retry)["data"] == recovered["data"]
            for target in (targets[0], targets[2]):
                refused = {"share_request_id": request_id, "request_id": str(uuid4()), "target": target}
                approve(client, call(client, token, "retry_share_target", **refused))
                assert call(client, token, "retry_share_target", **refused)["error"] == "share_attempt_unconfirmed"
            assert len(messages) == 4


@pytest.mark.parametrize("tool", ["create_feishu_report", "writeback_feishu_report"])
@pytest.mark.parametrize("edit_at", ["before_capture", "after_capture"])
def test_external_report_uses_approved_content_at_effect_boundary(mcp_app, core_system, monkeypatch, tool, edit_at):
    """The gateway check alone cannot close a concurrent Web-edit race."""
    _, store = mcp_app
    effects, captured_rounds = [], []

    def provider(request):
        assert request.method == "POST", request.url.path
        effects.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"code": 0, "data": {"document": {"document_id": "approved-report"}}})

    now = datetime.now(timezone.utc)
    session = store.create_session(store.principal_for_fixture("alice"), UserTokens("controlled-access", "controlled-refresh", now + timedelta(hours=1), now + timedelta(days=7)))
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        app = create_app(store.settings, auth_store=store, http=http)
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES), browser_session=session)
            token = tokens["access_token"]
            round_id, new_review = reviewed_round(client, store, token, core_system)
            arguments = {"round_id": round_id, "request_id": str(uuid4())}
            approve(client, call(client, token, tool, **arguments))
            edits = []

            def edit():
                if not edits:
                    saved = client.put(f"/rounds/{round_id}/reviews", json=new_review)
                    assert saved.status_code == 200, saved.text
                    edits.append(saved.json())

            if edit_at == "before_capture":
                original = app.state.mcp_gateway.request

                async def edit_after_gateway_check(*args, **kwargs):
                    if kwargs.get("operation_id") and args[1] == "POST":
                        await asyncio.to_thread(edit)
                    return await original(*args, **kwargs)

                monkeypatch.setattr(app.state.mcp_gateway, "request", edit_after_gateway_check)
            elif tool == "create_feishu_report":
                from api import report_routes

                original_blocks = report_routes.report_blocks

                def edit_after_materialization(body, return_url):
                    blocks = original_blocks(body, return_url)
                    edit()
                    return blocks

                monkeypatch.setattr(report_routes, "report_blocks", edit_after_materialization)
            else:
                import api

                original_write = api.write_round_history

                def edit_after_read(http, access_token, file_token, body, return_url):
                    captured_rounds.append(body)
                    edit()
                    return original_write(http, access_token, file_token, body, return_url)

                monkeypatch.setattr(api, "write_round_history", edit_after_read)

            result = call(client, token, tool, **arguments)
            assert len(edits) == 1
            current = client.get(f"/rounds/{round_id}").json()["findings"][0]["review"]
            assert current["version"] == 2 and current["rationale"] == "并发网页修改"
            if edit_at == "before_capture":
                assert result["status"] == "error" and result["error"] == "confirmation_content_changed", result
                assert effects == []
            else:
                assert result["status"] == "ok", result
                if tool == "create_feishu_report":
                    assert len(effects) == 2
                    assert result["data"]["report"]["status"] == "ready"
                    sent = json.dumps(effects, ensure_ascii=False)
                    assert "原批准复核" in sent and "并发网页修改" not in sent
                else:
                    assert len(effects) == 1  # All-PASS rounds need no NG comment.
                    assert result["data"]["inserted"] is True
                    assert captured_rounds[0]["findings"][0]["review"]["rationale"] == "原批准复核"
                    assert all("approved-document" in path for path, _ in effects)
            # A terminal receipt can be read and replayed despite later edits.
            count = len(effects)
            assert call(client, token, "get_operation", request_id=arguments["request_id"])["status"] == result["status"]
            assert call(client, token, tool, **arguments)["status"] == result["status"]
            assert len(effects) == count


def test_real_file_bytes_resources_object_authority_and_revocation(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        version, content = upload(client, token)
        ids = {"file_id": version["file_id"], "version_id": version["id"]}
        uri = f"ppap://files/{version['file_id']}/versions/{version['id']}?pointer=%2Fsha256"
        resource = rpc(client, token, "resources/read", {"uri": uri}).json()
        assert resource["result"]["contents"][0]["mimeType"] == "application/json"
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


def test_mcp_precheck_rule_publication_prepare_and_human_confirm_once(mcp_app, core_system):
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
            def __call__(self, operation, **request):
                if operation == "document_classification":
                    return AIResult({"document_type": "控制计划"})
                return AIResult({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"})

        # Only external model metadata is controlled. Actual validation,
        # recognition, binding, acceptance and PostgreSQL jobs execute here.
        process_claimed(worker, worker.claim_job("mcp-match"), "mcp-match", rule_ai=MetadataAI())
        task = call(client, token, "get_task", task_id=task["id"])["data"]["value"]
        assert task["state"] == "pending_confirm" and task["round_id"] is None
        confirm = {"task_id": task["id"], "request_id": str(uuid4()), "expected_revision": task["input_revision"], "expected_confirmation_basis_digest": task["confirmation_basis_digest"]}
        proposal = call(client, token, "confirm_task", **confirm)
        assert call(client, token, "get_operation", request_id=confirm["request_id"])["confirmation_url"] == proposal["confirmation_url"]
        approve(client, proposal)
        accepted = call(client, token, "confirm_task", **confirm)
        assert accepted["status"] == "ok", accepted
        assert accepted["data"]["round_id"]
        assert call(client, token, "confirm_task", **confirm)["data"] == accepted["data"]
        job = worker.claim_job("mcp-audit")
        assert job.kind == "audit"
        assert worker.claim_job("no-second-audit") is None
        before_calls = core_system.calls
        process_claimed(worker, job, "mcp-audit")
        assert core_system.calls == before_calls + 1
        round_body = call(client, token, "get_round", round_id=accepted["data"]["round_id"])["data"]["value"]
        assert round_body["execution"] == "completed"
        assert round_body["executor"] == "ppap-checker-core"
        assert round_body["findings"] == []
        assert [item["coverage"] for item in round_body["coverage"]["rule_items"]] == ["unsupported"]
        assert round_body["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"]


def test_review_receipt_commits_with_effect_even_when_transport_loses_reply(mcp_app, core_system, monkeypatch):
    app, store = mcp_app
    core_system.adopt_presence(store)
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
        confirmation = tools["confirm_task"]["inputSchema"]
        assert set(confirmation["required"]) == {"task_id", "request_id", "expected_revision", "expected_confirmation_basis_digest"}
        assert confirmation["properties"]["expected_confirmation_basis_digest"]["type"] == "string"
        assert tools["list_feishu_files"]["annotations"]["readOnlyHint"] is True
        assert tools["share_reports"]["annotations"]["readOnlyHint"] is False
        assert tools["share_reports"]["annotations"]["openWorldHint"] is True
        # Scheduled model work and quota checks also cross the provider boundary.
        for name in ("parse_rule", "prepare_task", "confirm_task", "regenerate_report", "retry_report_package"):
            assert tools[name]["annotations"]["openWorldHint"] is True
        # Replacing current choices or judgments is not an additive-only write.
        for name in ("prepare_task", "end_unmatched_task", "set_notification", "submit_review", "save_display_info", "save_draft"):
            assert tools[name]["annotations"]["destructiveHint"] is True
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
            from product.identity import token_hash

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


def test_deep_uses_one_mcp_confirmation_save_reanalysis_and_readback(mcp_app, monkeypatch):
    app, store = mcp_app
    monkeypatch.setattr(store.settings, "git_sha", "a" * 40)
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _ = upload(client, token)
        rule = store.publish_rule(
            store.principal_for_fixture("alice"),
            "Deep rule",
            {"kind": "engine", "type": "控制计划", "rules": [{"id": "R1", "category": "identity", "rule_to_check": "Part number must be present", "type_of_rule": "table"}]},
            file["id"],
        )
        draft = client.post("/drafts", json={"analysis_mode": "deep", "items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": rule["id"]}]}).json()
        configured = client.put(f"/drafts/{draft['id']}", json={"analysis_mode": "deep", "items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": rule["id"]}]})
        assert configured.status_code == 200, configured.text
        accepted = client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert accepted.status_code == 201, accepted.text
        rid = accepted.json()["id"]
        feedback = call(client, token, "read_audit", round_id=rid)
        assert feedback["status"] == "ok", feedback
        view = feedback["data"]["value"]
        clause = view["results"][0]["inputs"][0]["rule"]["body"]["rules"][0]["id"]
        body = {
            "operation_id": str(uuid4()),
            "expected_revision": view["revision"],
            "intent": "save_only",
            "kind": "interpretation",
            "content": "Use the part number in column B",
            "scope": {"document_ids": [file["id"]], "rule_ids": [clause]},
        }
        pending = call(client, token, "apply_rule_change", round_id=rid, body=body)
        approve(client, pending)
        assert call(client, token, "get_rule_change", round_id=rid, operation_id=body["operation_id"])["data"]["value"] is None
        saved = call(client, token, "apply_rule_change", round_id=rid, body=body)
        assert saved["status"] == "ok", saved
        assert saved["data"]["analysis_round_id"] is None
        assert call(client, token, "apply_rule_change", round_id=rid, body=body)["data"] == saved["data"]
        # Saving feedback does not silently replace the adopted Suite. Explicitly
        # adopt the confirmed appendix before authorizing a new frozen analysis.
        from product.store.suite_releases import SuiteReleaseStore

        principal = store.principal_for_fixture("alice")
        releases = SuiteReleaseStore(store.settings, store.objects, role="api")
        suite_key = view["results"][0]["inputs"][0]["rule"]["body"]["suite_key"]
        selected = releases.active(principal, suite_key)
        # The typed MCP receipt includes null optional fields. An authored
        # appendix omits those fields, matching the confirmed persisted input.
        appendix = [{key: value for key, value in part.items() if value is not None} for part in saved["data"]["appendix"]]
        candidate = releases.save_candidate(principal, selected["current_revision_id"], {"appendix.json": json.dumps(appendix, ensure_ascii=False)})
        releases.publish(principal, suite_key, candidate["id"], selected["revision"], str(uuid4()), force_unverified=True)
        persisted_feedback = store.read_deep_feedback(principal, rid)
        assert persisted_feedback["appendix"] == appendix
        resolved = releases.suites.resolve_suite(candidate["id"], principal.tenant_id)
        assert json.loads(resolved.files["appendix.json"]) == persisted_feedback["appendix"]
        assert json.loads(resolved.files["rules.json"])["rules"] == view["results"][0]["inputs"][0]["rule"]["body"]["rules"]
        analysis = {"round_id": rid, "operation_id": str(uuid4()), "expected_revision": saved["data"]["revision"]}
        approve(client, call(client, token, "start_analysis", **analysis))
        gateway = app.state.mcp_gateway
        original_request = gateway.request

        responses = []

        async def lost_reply(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            if args[1] == "POST" and args[2].endswith("/rule-changes"):
                responses.append((response.status_code, response.json()))
                raise TimeoutError("controlled response loss after commit")
            return response

        monkeypatch.setattr(gateway, "request", lost_reply)
        uncertain = call(client, token, "start_analysis", **analysis)
        assert uncertain["status"] == "unknown"
        assert responses and responses[0][0] == 200, responses
        receipt = call(client, token, "get_rule_change", round_id=rid, operation_id=analysis["operation_id"])["data"]["value"]
        assert receipt["analysis_round_id"] != rid
        assert receipt["analysis_state"] == "queued"
        monkeypatch.setattr(gateway, "request", original_request)
        assert call(client, token, "start_analysis", **analysis)["status"] == "running"
        after = call(client, token, "read_audit", round_id=rid)["data"]["value"]
        assert len(after["results"]) == 2
        assert after["current_round_id"] == receipt["analysis_round_id"]
        assert call(client, token, "get_round", round_id=rid)["data"]["value"]["id"] == rid
        # A confirmed snapshot cannot authorize a subsequently changed basis.
        stale = {**body, "operation_id": str(uuid4()), "expected_revision": after["revision"]}
        approve(client, call(client, token, "apply_rule_change", round_id=rid, body=stale))
        external = {**stale, "operation_id": str(uuid4()), "content": "A different confirmed explanation"}
        assert client.post(f"/audit-rounds/{rid}/rule-changes", json=external).status_code == 200
        rejected = call(client, token, "apply_rule_change", round_id=rid, body=stale)
        assert rejected["error"] == "confirmation_content_changed"
        assert call(client, token, "get_rule_change", round_id=rid, operation_id=stale["operation_id"])["data"]["value"] is None
