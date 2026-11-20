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

# Uploaded rules in this module stand for rules with a generated program.
pytestmark = pytest.mark.usefixtures("generated_rules")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The 控制计划 rule an operator publishes on the web; MCP itself has no rule tools.
MCP_RULE = "00000000-0000-4000-8000-00000000c0de"
RULE_BODY = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "customers": ["测试客户"],
    "models": [],
    "parts": [],
    "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
}
TOOLS = {
    "get_operation",
    "get_account",
    "find_feishu_files",
    "import_feishu_file",
    "begin_upload",
    "finish_upload",
    "get_upload",
    "preview_file",
    "download",
    "start_audit",
    "get_audit",
    "list_audits",
    "set_deep_mode",
    "confirm_audit",
    "end_audit",
    "get_result",
    "answer_question",
    "review",
    "respond_hint",
    "regenerate_report",
    "package_reports",
    "find_recipients",
    "share_reports",
    "get_share",
    "retry_share",
    "send_to_feishu",
    "find_history",
    "get_usage",
    "list_templates",
}


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
        call = rpc(client, tokens["access_token"], "tools/call", {"name": "get_account", "arguments": {}})
        assert call.status_code == 200, call.text
        value = call.json()["result"]["structuredContent"]
        assert value["status"] == "ok", value
        assert value["data"]["value"]["user"]["user_id"] == store.principal_for_fixture("alice").user_id
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
        result = rpc(client, tokens["access_token"], "tools/call", {"name": "list_audits", "arguments": {}}).json()["result"]["structuredContent"]
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


def test_client_registered_without_scope_can_ask_for_every_scope(mcp_app):
    # ChatGPT registers without a scope, then asks for every advertised scope; refusing that ends its connection.
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        callback = "https://chatgpt.com/connector/oauth/example"
        registered = client.post("/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": [callback], "token_endpoint_auth_method": "none"})
        assert registered.status_code == 201, registered.text
        args = {
            "response_type": "code",
            "client_id": registered.json()["client_id"],
            "redirect_uri": callback,
            "scope": " ".join(SCOPES),
            "state": "s",
            "code_challenge": pkce_challenge(secrets.token_urlsafe(48)),
            "code_challenge_method": "S256",
            "resource": "http://127.0.0.1:8080/mcp",
        }
        started = client.get("/oauth/authorize", params=args, follow_redirects=False)
        assert started.status_code == 302 and "/oauth/consent/" in started.headers["location"], started.headers.get("location")


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


def read(client, token, name, **arguments):
    """The value a read tool returns."""
    reply = call(client, token, name, **arguments)
    assert reply["status"] == "ok", reply
    return reply["data"]["value"]


def task_of(client, token, task_id):
    return read(client, token, "get_audit", task_id=task_id, detail=True)["task"]


def frozen(client, token, round_id):
    """A round's frozen inputs, findings and reviews; the read view's live report state is left out."""
    body = read(client, token, "get_result", round_id=round_id, detail=True)["round"]
    return {key: item for key, item in body.items() if key != "read_view"}


def upload(client, token, content=None, *, file_id=None, filename="控制计划.xlsx", content_type=XLSX):
    """begin_upload, PUT with Bearer, finish_upload; returns the version, its bytes and the readability check finish_upload scheduled."""
    if content is None:
        stream = io.BytesIO()
        book = Workbook()
        book.active.append(["控制计划", "测试客户", "零件号 P-1"])
        book.save(stream)
        book.close()
        content = stream.getvalue()
    rid = str(uuid4())
    args = {
        "request_id": rid,
        "filename": filename,
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_type": content_type,
        **({"file_id": file_id} if file_id else {}),
    }
    begun = call(client, token, "begin_upload", **args)
    assert begun["status"] == "ok" and begun["data"]["state"] == "uploading", begun
    assert call(client, token, "begin_upload", **args)["data"]["file"] == begun["data"]["file"]
    transfer = begun["data"]["upload"]
    assert transfer["method"] == "PUT"
    assert client.put(transfer["url"], content=content).status_code == 401
    assert client.put(transfer["url"], headers={"Authorization": "Bearer " + token}, content=content).status_code == 200
    finished = call(client, token, "finish_upload", request_id=rid)
    assert finished["status"] == "ok", finished
    version, validation = finished["data"]["file"], finished["data"]["validation"]
    assert version["sha256"] == args["sha256"] and "id" in validation, finished
    if not file_id:
        assert version["id"] == rid
    # The same upload always gets the same check, never a second one.
    again = call(client, token, "finish_upload", request_id=rid)
    assert again["data"]["file"]["id"] == version["id"] and again["data"]["validation"] == validation
    return version, content, validation["id"]


def approve(client, operation):
    assert operation["status"] == "confirmation_required", operation
    page = client.get(operation["confirmation_url"])
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    assert client.post(operation["confirmation_url"], data={"csrf": "wrong", "decision": "approve"}).status_code == 403
    assert client.post(operation["confirmation_url"], data={"csrf": csrf, "decision": "approve"}).status_code == 200


def selected(file, validation_id):
    return {"file_version_id": file["id"], "validation_id": validation_id, "selection": "selected", "removal_reason": None}


class MetadataAI:
    """Only external model metadata is controlled; validation, recognition, binding, acceptance and jobs are real."""

    def __call__(self, operation, **request):
        if operation == "document_classification":
            return AIResult({"document_type": "控制计划"})
        return AIResult({"customer": "测试客户", "model": "M1", "part": "零件A", "part_number": "P-1"})


def operator_rule(store, core_system, source_version_id, *, adopt=True):
    """Publish the 控制计划 rule once per test, as an operator does on the web."""
    principal = store.principal_for_fixture("alice")
    if store.get_rule(principal.tenant_id, MCP_RULE) is None:
        store.publish_rule(principal, "MCP 测试规则", RULE_BODY, source_version_id, request_id=MCP_RULE)
        if adopt:
            core_system.adopt_presence(store, MCP_RULE)
    return MCP_RULE


def reviewed_round(client, store, token, core_system):
    core_system.adopt_presence(store)
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    file, _, _ = upload(client, token)
    process_claimed(worker, worker.claim_job("upload-validation"), "upload-validation")
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


@pytest.mark.parametrize("revision", [False, True])
def test_start_audit_reads_original_identity_after_reply_loss_and_restart(mcp_app, monkeypatch, revision):
    app, store = mcp_app
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    request_id = str(uuid4())
    task_id = request_id
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _, validation_id = upload(client, token)
        process_claimed(worker, worker.claim_job("prepare-validation"), "prepare-validation")
        arguments = {"request_id": request_id, "files": [selected(file, validation_id)]}
        if revision:
            initial = call(client, token, "start_audit", **arguments)
            assert initial["status"] == "ok" and initial["data"]["id"] == task_id
            request_id = str(uuid4())
            # The tool reads the task's current revision itself.
            arguments = {**arguments, "request_id": request_id, "task_id": task_id}
        original_request = app.state.mcp_gateway.request

        async def lost_reply(*args, **kwargs):
            response = await original_request(*args, **kwargs)
            if args[1:3] == ("POST", "/audit-tasks"):
                assert response.status_code == 202
                raise ConnectionError("controlled loss after recognition was committed")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "start_audit", **arguments)["status"] == "unknown"
    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        task = task_of(restored, token, task_id)
        assert task["id"] == task_id and task["input_revision"] == int(revision)
        assert task["round_id"] is None
        assert call(restored, token, "get_operation", request_id=request_id)["status"] in {"running", "unknown"}
        assert call(restored, token, "start_audit", **arguments)["status"] in {"running", "unknown"}
        assert task_of(restored, token, task_id) == task
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
            # The same enterprise reads the task; only the original connection reads its own receipt.
            assert call(restored, other["access_token"], "get_audit", task_id=task_id)["status"] == ("ok" if person == "bob" else "error")
            assert call(restored, other["access_token"], "get_operation", request_id=request_id)["status"] == "error"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert rpc(restored, token, "tools/call", {"name": "get_audit", "arguments": {"task_id": task_id}}).status_code == 401


def completed_mcp_task(client, store, token, core_system, *, writeback_target=None, fail_pdf=False):
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    file, _, validation_id = upload(client, token)
    process_claimed(worker, worker.claim_job("share-validation"), "share-validation")
    operator_rule(store, core_system, file["id"])
    extra = {"writeback_target": writeback_target} if writeback_target else {}
    started = call(client, token, "start_audit", request_id=str(uuid4()), files=[selected(file, validation_id)], **extra)
    assert started["status"] == "ok", started
    process_claimed(worker, worker.claim_job("share-match"), "share-match", rule_ai=MetadataAI())
    task_id = started["data"]["id"]
    # The tool reads the revision and confirmation basis the user approves.
    confirm = {"task_id": task_id, "request_id": str(uuid4())}
    approve(client, call(client, token, "confirm_audit", **confirm))
    accepted = call(client, token, "confirm_audit", **confirm)
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
    task = task_of(client, token, task_id)
    assert task["state"] == "completed", task
    row = task["files"][0]
    report = row["audit"]["report"]
    assert report["state"] == ("failed" if fail_pdf else "ready"), report
    reference = {"task_id": task_id, "round_id": task["round_id"], "file_version_id": file["id"], "result_attempt": row["audit"]["result_attempt"], "report_id": report["report_id"]}
    return worker, task, reference, report["artifact"]


def test_mcp_pdf_recovery_and_zip_preserve_original_results_after_lost_reply(mcp_app, core_system, monkeypatch):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        worker, first, reference, artifact = completed_mcp_task(client, store, token, core_system, fail_pdf=True)
        assert artifact is None
        _, second, second_reference, _ = completed_mcp_task(client, store, token, core_system)
        rounds = {row["round_id"]: frozen(client, token, row["round_id"]) for row in (first, second)}
        regeneration = {"request_id": str(uuid4()), "report": reference}
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
    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        # The accepted regeneration is read back from the task, never sent again with a new request ID.
        assert call(restored, token, "get_operation", request_id=regeneration["request_id"])["status"] in {"running", "unknown"}
        assert task_of(restored, token, reference["task_id"])["files"][0]["audit"]["report"]["state"] == "pending"
        assert call(restored, token, "regenerate_report", **regeneration)["status"] in {"running", "unknown"}
        pdf_job = worker.claim_job("recover-mcp-pdf")
        assert pdf_job.id == reference["report_id"] and pdf_job.attempt == 2
        assert worker.claim_job("no-second-pdf") is None
        process_claimed(worker, pdf_job, "recover-mcp-pdf")
        assert task_of(restored, token, reference["task_id"])["files"][0]["audit"]["report"]["state"] == "ready"
        pdfs = []
        for ref in (reference, second_reference):
            artifact = task_of(restored, token, ref["task_id"])["files"][0]["audit"]["report"]["artifact"]
            url = call(restored, token, "download", target={"kind": "report", "report": ref, "artifact_id": artifact["artifact_id"]})["data"]["url"]
            response = restored.get(url, headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200 and response.content.startswith(b"%PDF-")
            pdfs.append(response.content)
        package = {"request_id": str(uuid4()), "reports": [reference, second_reference]}
        created = call(restored, token, "package_reports", **package)
        assert created["status"] == "ok", created
        zip_job = worker.claim_job("mcp-zip")
        assert zip_job.kind == "report_zip"
        process_claimed(worker, zip_job, "mcp-zip")
        # Calling again with the same request ID reads the same package; it never builds a second ZIP.
        ready = call(restored, token, "package_reports", **package)["data"]
        assert ready["state"] == "ready" and ready["package_id"] == created["data"]["package_id"]
        assert worker.claim_job("no-second-zip") is None
        zipped = {"kind": "report_package", "request_id": package["request_id"], "package_id": ready["package_id"], "artifact_id": ready["artifact"]["artifact_id"]}
        url = call(restored, token, "download", target=zipped)["data"]["url"]
        downloaded = restored.get(url, headers={"Authorization": "Bearer " + token})
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert sorted(archive.read(name) for name in archive.namelist() if name.endswith(".pdf")) == sorted(pdfs)
        for round_id, before in rounds.items():
            assert frozen(restored, token, round_id) == before
        assert read(restored, token, "get_usage")["usage"]["entries"] == []
        assert len(read(restored, token, "list_audits")["tasks"]) == 2
        for person in ("bob", "carol"):
            _, other, _ = connection(restored, store, person=person, scopes=" ".join(SCOPES))
            assert call(restored, other["access_token"], "get_operation", request_id=regeneration["request_id"])["status"] == "error"
            assert call(restored, other["access_token"], "download", target=zipped)["status"] == "error"
        assert restored.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
        assert restored.get(url, headers={"Authorization": "Bearer " + token}).status_code == 401


def test_mcp_start_audit_carries_writeback_target_through_acceptance_and_lost_send_reply(mcp_app, core_system, monkeypatch):
    """Use only MCP business tools from starting the audit to writeback; HTTP effects are controlled."""
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
            _, task, _, _ = completed_mcp_task(client, store, token, core_system, writeback_target=target)
            round_id = task["round_id"]
            assert store.round_writeback(round_id) == target
            original_round = frozen(client, token, round_id)
            assert original_round["writeback"]["available"]
            assert effects == []  # Starting and accepting never write externally.
            # The target is fixed with the task's first inputs; a revision cannot move it.
            row = task["files"][0]
            moved = call(
                client,
                token,
                "start_audit",
                request_id=str(uuid4()),
                task_id=task["id"],
                files=[selected(row["file"], row["validation_id"])],
                writeback_target={**target, "object_ref": "stale-document"},
            )
            assert moved["status"] == "error" and moved["error"] == "writeback_target_conflict", moved
            arguments = {"round_id": round_id, "request_id": str(uuid4()), "mode": "writeback"}
            approve(client, call(client, token, "send_to_feishu", **arguments))
            original = app.state.mcp_gateway.request

            async def lose_writeback_reply(*args, **kwargs):
                response = await original(*args, **kwargs)
                if args[1:3] == ("POST", f"/integrations/feishu/rounds/{round_id}/writeback"):
                    assert response.status_code == 200
                    raise ConnectionError("controlled loss after provider acknowledged writeback")
                return response

            monkeypatch.setattr(app.state.mcp_gateway, "request", lose_writeback_reply)
            assert call(client, token, "send_to_feishu", **arguments)["status"] == "unknown"
            # The written report links back to its task page.
            assert effects and f"/tasks/{task['id']}" in json.dumps(effects)
            sent = list(effects)
        with TestClient(create_app(store.settings, auth_store=store, http=http), base_url="http://127.0.0.1:8080") as restarted:
            assert call(restarted, token, "get_operation", request_id=arguments["request_id"])["status"] in {"running", "unknown"}
            assert call(restarted, token, "send_to_feishu", **arguments)["status"] in {"running", "unknown"}
            assert effects == sent
            assert store.round_writeback(round_id) == target
            assert frozen(restarted, token, round_id) == original_round
            for person in ("bob", "carol"):
                _, other, _ = connection(restarted, store, person=person, scopes=" ".join(SCOPES))
                if person == "carol":
                    assert call(restarted, other["access_token"], "get_audit", task_id=task["id"])["status"] == "error"
                # Writing back speaks for the round: only its creator does it. Bob reads the round, so he is
                # asked to confirm; carol cannot read it. Neither write reaches Feishu (effects stay as sent).
                expected = {"confirmation_required"} if person == "bob" else {"error"}
                assert call(restarted, other["access_token"], "send_to_feishu", **{**arguments, "request_id": str(uuid4())})["status"] in expected
            assert restarted.post("/oauth/revoke", data={"client_id": cid, "token": token}).status_code == 200
            assert rpc(restarted, token, "tools/call", {"name": "send_to_feishu", "arguments": arguments}).status_code == 401
            assert effects == sent


@pytest.mark.parametrize("suite_choice", ["original", "newer"])
def test_mcp_review_replacement_and_linked_reaudit_preserve_frozen_suite_history(mcp_app, core_system, monkeypatch, suite_choice):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        worker, first_task, first_ref, _ = completed_mcp_task(client, store, token, core_system)
        round_id = first_task["round_id"]
        before = frozen(client, token, round_id)
        old_row = first_task["files"][0]
        old_file = old_row["file"]
        old_rule_id = old_row["audit"]["rule"]["id"]
        original = {"kind": "original", "file_id": old_file["file_id"], "version_id": old_file["id"]}
        old_transfer = call(client, token, "download", target=original)["data"]["url"]
        original_bytes = client.get(old_transfer, headers={"Authorization": "Bearer " + token}).content
        assert hashlib.sha256(original_bytes).hexdigest() == old_file["sha256"]
        review = {
            "round_id": round_id,
            "request_id": str(uuid4()),
            "item_key": before["findings"][0]["item_key"],
            "judgment": "需修改",
            "rationale": "已核对原版本，需要补件",
            "next_step": "补交新版控制计划",
            "citations": [{"file_version_id": old_file["id"], "location": "Sheet!A1", "excerpt": "控制计划"}],
        }
        approve(client, call(client, token, "review", **review))
        reviewed = call(client, token, "review", **review)
        assert reviewed["status"] == "ok" and reviewed["provenance"]["client_id"] == cid
        assert reviewed["data"]["findings"][0]["status"] == before["findings"][0]["status"]
        frozen_old = frozen(client, token, round_id)
        assert frozen_old["findings"][0]["review"]["citations"] == review["citations"]
        # Rule-version provenance stays immutable. Editing an adopted Checker
        # goes through a Suite candidate, not a stale rule publication that
        # would overwrite the current Suite head.
        from product.store.suite_releases import SuiteReleaseStore

        principal = store.principal_for_fixture("alice")
        releases = SuiteReleaseStore(store.settings, store.objects, role="api")
        old_suite_id = before["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"]
        suite_key = before["inputs"][0]["rule"]["body"]["suite_key"]
        current_head = releases.active(principal, suite_key)
        rules = json.loads(releases.suites.resolve_suite(old_suite_id, principal.tenant_id).files["rules.json"])
        changed_text = "必须填写修订后的零件号"
        rules["rules"][0]["rule_to_check"] = changed_text
        candidate = releases.save_candidate(principal, old_suite_id, {"rules.json": json.dumps(rules, ensure_ascii=False)})
        chosen_suite_id = old_suite_id
        if suite_choice == "newer":
            releases.publish(principal, suite_key, candidate["id"], current_head["revision"], str(uuid4()), force_unverified=True)
            chosen_suite_id = candidate["id"]
        chosen_rule = old_rule_id

        content = io.BytesIO()
        book = Workbook()
        book.active.append(["控制计划", "测试客户", "零件号 P-1", "补件修订"])
        book.save(content)
        book.close()
        replacement, _, validation_id = upload(client, token, content.getvalue(), file_id=old_file["file_id"])
        assert replacement["id"] != old_file["id"] and replacement["sha256"] != old_file["sha256"]
        process_claimed(worker, worker.claim_job("replacement-check"), "replacement-check")
        reaudit = {
            "request_id": str(uuid4()),
            "reaudit_round_id": round_id,
            "files": [
                {"file_version_id": old_file["id"], "validation_id": old_row["validation_id"], "selection": "removed", "removal_reason": "本次使用补件替换"},
                {**selected(replacement, validation_id), "replacement_of_file_version_id": old_file["id"], "preferred_rule_version_id": chosen_rule},
            ],
        }
        linked = call(client, token, "start_audit", **reaudit)
        assert linked["status"] == "ok", linked
        assert call(client, token, "start_audit", **reaudit)["data"] == linked["data"]
        process_claimed(worker, worker.claim_job("replacement-match"), "replacement-match", rule_ai=MetadataAI())
        next_task_id = linked["data"]["id"]
        matched = task_of(client, token, next_task_id)
        assert matched["predecessor_id"] == round_id
        assert matched["files"][1]["match"]["rule"]["id"] == chosen_rule
        confirm = {"task_id": next_task_id, "request_id": str(uuid4())}
        approve(client, call(client, token, "confirm_audit", **confirm))
        original_request = app.state.mcp_gateway.request

        async def lose_acceptance_reply(*a, **kw):
            response = await original_request(*a, **kw)
            if a[1:3] == ("POST", f"/audit-tasks/{next_task_id}/confirm"):
                raise ConnectionError("controlled reply loss after reaudit acceptance")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lose_acceptance_reply)
        assert call(client, token, "confirm_audit", **confirm)["status"] == "unknown"

    with TestClient(create_app(store.settings, auth_store=store), base_url="http://127.0.0.1:8080") as restored:
        # The persistent Task is the acceptance authority even if the MCP reply
        # was lost. A second invocation must not enqueue another audit.
        accepted = task_of(restored, token, next_task_id)
        next_round_id = accepted["round_id"]
        assert next_round_id and next_round_id != round_id
        assert call(restored, token, "confirm_audit", **confirm)["status"] in {"running", "unknown"}
        job = worker.claim_job("replacement-audit")
        assert job.round_id == next_round_id and worker.claim_job("no-duplicate-audit") is None
        guest_calls = core_system.calls
        process_claimed(worker, job, "replacement-audit")
        assert core_system.calls == guest_calls + 1
        process_claimed(worker, worker.claim_job("replacement-pdf"), "replacement-pdf")
        assert task_of(restored, token, next_task_id)["state"] == "completed"
        current = frozen(restored, token, next_round_id)
        assert current["predecessor_id"] == round_id
        assert current["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"] == chosen_suite_id
        expected_text = changed_text if suite_choice == "newer" else before["inputs"][0]["rule"]["body"]["rules"][0]["rule_to_check"]
        assert current["inputs"][0]["rule"]["body"]["rules"][0]["rule_to_check"] == expected_text
        assert [(row["file_version_id"], row["rule_version_id"]) for row in current["inputs"]] == [(replacement["id"], chosen_rule)]
        assert all(finding["review"] is None for finding in current["findings"])
        assert frozen(restored, token, round_id) == frozen_old
        assert restored.get(old_transfer, headers={"Authorization": "Bearer " + token}).content == original_bytes
        assert read(restored, token, "get_usage")["usage"]["entries"] == []  # This fixture explicitly disables charging.
        tasks = read(restored, token, "list_audits")["tasks"]
        assert {task["id"] for task in tasks} == {first_task["id"], next_task_id}
        # The same enterprise reads the rounds; another enterprise can neither read nor re-audit them.
        _, bob, _ = connection(restored, store, person="bob", scopes=" ".join(SCOPES))
        assert call(restored, bob["access_token"], "get_result", round_id=round_id)["status"] == "ok"
        assert call(restored, bob["access_token"], "get_result", round_id=next_round_id)["status"] == "ok"
        _, carol, _ = connection(restored, store, person="carol", scopes=" ".join(SCOPES))
        assert call(restored, carol["access_token"], "start_audit", request_id=str(uuid4()), reaudit_round_id=round_id)["error"] == "access_denied"
        assert call(restored, carol["access_token"], "get_result", round_id=next_round_id)["error"] == "access_denied"
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
            pdf_transfer = call(client, token, "download", target={"kind": "report", "report": reference, "artifact_id": artifact["artifact_id"]})
            assert pdf_transfer["status"] == "ok", pdf_transfer
            pdf = client.get(pdf_transfer["data"]["url"], headers={"Authorization": "Bearer " + token})
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
            content = {"kind": kind, "artifact": artifact if kind == "pdf" else None, "package_id": None}
            reports = [reference]
            if kind == "zip":
                _, _, second_reference, _ = completed_mcp_task(client, store, token, core_system)
                reports.append(second_reference)
                package_request = str(uuid4())
                created = call(client, token, "package_reports", request_id=package_request, reports=reports)
                assert created["status"] == "ok", created
                process_claimed(worker, worker.claim_job("share-zip"), "share-zip")
                package = call(client, token, "package_reports", request_id=package_request, reports=reports)["data"]
                assert package["state"] == "ready", package
                content.update(artifact=package["artifact"], package_id=package["package_id"])
                zipped = {"kind": "report_package", "request_id": package_request, "package_id": package["package_id"], "artifact_id": package["artifact"]["artifact_id"]}
                transfer = call(client, token, "download", target=zipped)
                archive = client.get(transfer["data"]["url"], headers={"Authorization": "Bearer " + token})
                assert archive.status_code == 200
                with zipfile.ZipFile(io.BytesIO(archive.content)) as packed:
                    assert any(packed.read(name) == pdf.content for name in packed.namelist() if name.endswith(".pdf"))

            request_id = str(uuid4())
            targets = [{"kind": "user", "id": ident} for ident in (principal.subject, "ou_failed", "ou_unknown")]
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
            receipt = read(client, token, "get_share", share_request_id=request_id)["delivery"]
            assert receipt["deliveries"] == sent["data"]["deliveries"] and len(messages) == 3
            retry = {"share_request_id": request_id, "request_id": str(uuid4()), "target": targets[1]}
            approve(client, call(client, token, "retry_share", **retry))
            recovered = call(client, token, "retry_share", **retry)
            assert recovered["status"] == "ok", recovered
            assert [item["state"] for item in recovered["data"]["deliveries"]] == ["sent", "sent", "unknown"]
            assert len(messages) == 4 and messages[-1]["receive_id"] == "ou_failed"
            assert call(client, token, "retry_share", **retry)["data"] == recovered["data"]
            for target in (targets[0], targets[2]):
                refused = {"share_request_id": request_id, "request_id": str(uuid4()), "target": target}
                approve(client, call(client, token, "retry_share", **refused))
                assert call(client, token, "retry_share", **refused)["error"] == "share_attempt_unconfirmed"
            assert len(messages) == 4


@pytest.mark.parametrize("mode", ["new_document", "writeback"])
@pytest.mark.parametrize("edit_at", ["before_capture", "after_capture"])
def test_external_report_uses_approved_content_at_effect_boundary(mcp_app, core_system, monkeypatch, mode, edit_at):
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
            arguments = {"round_id": round_id, "request_id": str(uuid4()), "mode": mode}
            approve(client, call(client, token, "send_to_feishu", **arguments))
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
            elif mode == "new_document":
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

            result = call(client, token, "send_to_feishu", **arguments)
            assert len(edits) == 1
            current = client.get(f"/rounds/{round_id}").json()["findings"][0]["review"]
            assert current["version"] == 2 and current["rationale"] == "并发网页修改"
            if edit_at == "before_capture":
                assert result["status"] == "error" and result["error"] == "confirmation_content_changed", result
                assert effects == []
            else:
                assert result["status"] == "ok", result
                if mode == "new_document":
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
            assert call(client, token, "send_to_feishu", **arguments)["status"] == result["status"]
            assert len(effects) == count


def test_real_file_bytes_object_authority_and_revocation(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        version, content, _ = upload(client, token)
        digest = hashlib.sha256(content).hexdigest()
        receipt = read(client, token, "get_upload", request_id=version["id"])
        assert receipt["state"] == "ready" and receipt["file"]["sha256"] == digest
        # One request ID names one upload: changed arguments are refused, never a second version.
        changed = {"request_id": version["id"], "filename": "different.xlsx", "byte_size": len(content), "sha256": digest, "content_type": XLSX}
        assert call(client, token, "begin_upload", **changed)["error"] == "request_conflict"
        original = {"kind": "original", "file_id": version["file_id"], "version_id": version["id"]}
        download = call(client, token, "download", target=original)["data"]["url"]
        assert client.get(download, headers={"Authorization": "Bearer " + token}).content == content
        _, other, _ = connection(client, store, person="carol")
        assert call(client, other["access_token"], "get_upload", request_id=version["id"])["status"] == "error"
        assert call(client, other["access_token"], "download", target=original)["status"] == "error"
        assert call(client, other["access_token"], "preview_file", file_id=version["file_id"], version_id=version["id"])["status"] == "error"
        assert client.get(download, headers={"Authorization": "Bearer " + other["access_token"]}).status_code == 404
        assert client.post("/oauth/revoke", data={"client_id": cid, "token": tokens["refresh_token"]}).status_code == 200
        assert client.get(download, headers={"Authorization": "Bearer " + token}).status_code == 401
        assert rpc(client, token, "tools/list").status_code == 401


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


def test_mcp_validation_prepare_and_human_confirm_once(mcp_app, core_system):
    app, store = mcp_app
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _, vid = upload(client, token)
        process_claimed(worker, worker.claim_job("mcp-validation"), "mcp-validation")
        assert read(client, token, "get_upload", request_id=file["id"])["validation"]["status"] == "succeeded"
        operator_rule(store, core_system, file["id"], adopt=False)
        args = {"request_id": str(uuid4()), "files": [selected(file, vid)]}
        started = call(client, token, "start_audit", **args)
        assert started["status"] == "ok", started
        task = started["data"]
        assert call(client, token, "start_audit", **args)["data"]["id"] == task["id"]
        # Only external model metadata is controlled. Actual validation,
        # recognition, binding, acceptance and PostgreSQL jobs execute here.
        process_claimed(worker, worker.claim_job("mcp-match"), "mcp-match", rule_ai=MetadataAI())
        task = task_of(client, token, task["id"])
        assert task["state"] == "pending_confirm" and task["round_id"] is None
        confirm = {"task_id": task["id"], "request_id": str(uuid4())}
        proposal = call(client, token, "confirm_audit", **confirm)
        assert call(client, token, "get_operation", request_id=confirm["request_id"])["confirmation_url"] == proposal["confirmation_url"]
        # The person approves what will happen in their words, not a tool name, internal IDs or raw request.
        page = client.get(proposal["confirmation_url"]).text
        assert "开始审核" in page and "控制计划.xlsx" in page
        assert "confirm_audit" not in page and task["id"] not in page and "expected_revision" not in page
        approve(client, proposal)
        accepted = call(client, token, "confirm_audit", **confirm)
        assert accepted["status"] == "ok", accepted
        assert accepted["data"]["round_id"]
        assert call(client, token, "confirm_audit", **confirm)["data"] == accepted["data"]
        job = worker.claim_job("mcp-audit")
        assert job.kind == "audit"
        assert worker.claim_job("no-second-audit") is None
        before_calls = core_system.calls
        process_claimed(worker, job, "mcp-audit")
        assert core_system.calls == before_calls + 1
        round_body = frozen(client, token, accepted["data"]["round_id"])
        assert round_body["execution"] == "completed"
        assert round_body["executor"] == "ppap-checker-core"
        assert round_body["findings"] == []
        assert [item["coverage"] for item in round_body["coverage"]["rule_items"]] == ["unsupported"]
        assert round_body["coverage"]["checker_execution"]["targets"][0]["suite_revision_id"]


def test_review_receipt_commits_with_effect_even_when_transport_loses_reply(mcp_app, core_system, monkeypatch):
    app, store = mcp_app
    core_system.adopt_presence(store)
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        file, _, _ = upload(client, token)
        process_claimed(worker, worker.claim_job("upload-validation"), "upload-validation")
        draft = client.post("/drafts", json={"items": [{"file_version_id": file["id"], "role": "target", "rule_version_id": RULE_A}]}).json()
        accepted = client.post(f"/drafts/{draft['id']}/accept", json={"idempotency_key": str(uuid4())})
        assert accepted.status_code == 201, accepted.text
        round_id = accepted.json()["id"]
        process_claimed(store, store.claim_job("presence-audit"), "presence-audit")
        before = client.get(f"/rounds/{round_id}").json()
        finding = before["findings"][0]
        # The tool reads the finding's current review version itself.
        args = {"round_id": round_id, "request_id": str(uuid4()), "item_key": finding["item_key"], "judgment": "同意", "rationale": "已核对原件", "next_step": ""}
        pending = call(client, token, "review", **args)
        assert client.get(f"/rounds/{round_id}").json()["findings"][0]["review"] is None
        approve(client, pending)
        original = app.state.mcp_gateway.request

        async def lost_reply(*a, **kw):
            response = await original(*a, **kw)
            if len(a) >= 2 and a[1] == "PUT":
                raise ConnectionError("response discarded after the actual commit")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "review", **args)["status"] == "unknown"
        receipt = call(client, token, "get_operation", request_id=args["request_id"])
        assert receipt["status"] == "ok", receipt
        assert receipt["provenance"]["client_id"] == cid
        assert receipt["provenance"]["confirmed_by"] == store.principal_for_fixture("alice").user_id
        assert receipt["data"]["findings"][0]["review"]["version"] == 1
        assert call(client, token, "review", **args)["data"] == receipt["data"]
        current = client.get(f"/rounds/{round_id}").json()["findings"][0]
        assert current["status"] == finding["status"] and current["review"]["version"] == 1
        next_args = {**args, "request_id": str(uuid4()), "rationale": "第二次核对"}
        approve(client, call(client, token, "review", **next_args))
        # A concurrent Web edit wins before the delegated write. The old
        # confirmation cannot authorize overriding that newer human judgment.
        web = {"item_key": finding["item_key"], "judgment": "需修改", "rationale": "网页复核已修订", "next_step": "", "version": 1}
        assert client.put(f"/rounds/{round_id}/reviews", json=web).status_code == 200
        assert call(client, token, "review", **next_args)["error"] == "confirmation_content_changed"
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
        # One tool per step of an audit; no rule, draft, URL-import or bundle tools remain.
        assert tools.keys() == TOOLS
        assert set(tools["confirm_audit"]["inputSchema"]["required"]) == {"task_id", "request_id"}
        assert set(tools["review"]["inputSchema"]["required"]) == {"round_id", "request_id", "item_key", "judgment", "rationale", "next_step"}
        for name in ("get_account", "get_audit", "get_result", "get_upload", "find_feishu_files", "find_recipients", "get_share", "get_usage"):
            assert tools[name]["annotations"]["readOnlyHint"] is True
        for name in ("begin_upload", "finish_upload", "start_audit", "confirm_audit", "review", "share_reports", "retry_share", "send_to_feishu", "download"):
            assert tools[name]["annotations"]["readOnlyHint"] is False
        # Effects that reach Feishu or start a charged audit cross the provider boundary.
        for name in ("import_feishu_file", "confirm_audit", "regenerate_report", "share_reports", "retry_share", "send_to_feishu"):
            assert tools[name]["annotations"]["openWorldHint"] is True
        # Each call creates a new transfer address.
        assert tools["download"]["annotations"]["idempotentHint"] is False
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
    args = {"request_id": str(uuid4()), "filename": "resume.txt", "byte_size": 4, "content_type": "text/plain", "sha256": hashlib.sha256(b"test").hexdigest()}
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        token = tokens["access_token"]
        before = call(client, token, "begin_upload", **args)
        assert before["status"] == "ok", before
        reserved = before["data"]["file"]
    # No browser session, SDK session or in-process receipt survives this restart.
    restarted = create_app(store.settings, auth_store=build_product_store(store.settings, objects=store.objects, role="api"))
    with TestClient(restarted, base_url="http://127.0.0.1:8080") as client:
        receipt = call(client, token, "get_operation", request_id=args["request_id"])
        assert receipt["status"] == "ok" and receipt["data"]["id"] == reserved["id"]
        again = call(client, token, "begin_upload", **args)
        assert again["data"]["file"] == reserved and again["data"]["upload"]["url"]
        assert read(client, token, "get_upload", request_id=args["request_id"])["file"]["id"] == reserved["id"]


def test_mcp_origin_and_body_boundaries_and_origin_root_discovery(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store)
        headers = {"Authorization": "Bearer " + tokens["access_token"], "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", headers={**headers, "Origin": "https://attacker.example"}, json={}).status_code == 403
        assert client.post("/mcp", headers=headers, content=b" " * (1024 * 1024 + 1)).status_code == 413
        assert rpc(client, tokens["access_token"], "tools/list").status_code == 200
        # A client probing without a token, in any content type, is told where to authorize, not that the server is broken.
        probe = client.post("/mcp", headers={"Content-Type": "application/octet-stream"}, content=b"")
        assert probe.status_code == 401 and "resource_metadata" in probe.headers["www-authenticate"]
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
                busy = call(client, second["access_token"], "get_account")
                assert busy["error"] == "provider_refresh_in_progress" and busy["retry_after_seconds"] == 5
                # Authentication remains valid; this is not an OAuth token failure.
                assert rpc(client, first["access_token"], "tools/list").status_code == 200
            finally:
                release.set()
            assert browser_request.result(timeout=10).status_code == 200
        assert call(client, first["access_token"], "get_account")["status"] == "ok"
        assert call(client, second["access_token"], "get_account")["status"] == "ok"
        assert store.load_session(second_browser).access_token == "access-rotated"
        repeated_login = store.create_session(
            principal, UserTokens("access-rotated", "refresh-rotated", datetime.now(timezone.utc) + timedelta(hours=1), datetime.now(timezone.utc) + timedelta(days=7))
        )
        assert store.load_session(repeated_login).provider_authorization_id == store.load_session(raw).provider_authorization_id
        assert requests == ["refresh-shared"]
        client.post("/oauth/revoke", data={"client_id": first_id, "token": first["access_token"]})
        assert rpc(client, first["access_token"], "tools/list").status_code == 401
        assert call(client, second["access_token"], "get_account")["status"] == "ok"
        assert client.get("/me").status_code == 200
        store.revoke_session(raw)
        # Browser logout revokes that session; it does not revoke separately
        # confirmed MCP grants that share the underlying provider credentials.
        assert client.get("/me").status_code == 401
        assert call(client, second["access_token"], "get_account")["status"] == "ok"


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
            assert call(client, tokens["access_token"], "get_account")["error"] == "provider_reauthorization_required"
        restarted_store = build_product_store(store.settings, objects=store.objects, role="api")
        with TestClient(create_app(store.settings, auth_store=restarted_store, http=http), base_url="http://127.0.0.1:8080") as client:
            assert call(client, tokens["access_token"], "get_account")["error"] == "provider_reauthorization_required"
            new_raw = restarted_store.create_session(principal, provider_tokens("new-login"))
            assert restarted_store.load_session(new_raw).provider_authorization_id != old_owner
            _, new_tokens, _ = connection(client, restarted_store, browser_session=new_raw)
            assert call(client, new_tokens["access_token"], "get_account")["status"] == "ok"
            assert call(client, tokens["access_token"], "get_account")["error"] == "provider_reauthorization_required"
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
            operation = pool.submit(call, client, tokens["access_token"], "get_account")
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
