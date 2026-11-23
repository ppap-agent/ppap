"""Actual PostgreSQL + public OAuth/MCP wire contract. Feishu sign-in is separate live evidence."""

import concurrent.futures
import functools
import io
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from api import create_app
from documents.ai import AIResult
from fastapi.testclient import TestClient
from openpyxl import Workbook
from product import build_product_store
from product.feishu import UserTokens
from product.fixtures import APP_ID
from product.identity import ProviderAuthError
from product.mcp import SCOPES, pkce_challenge, read_client_document
from worker import process_claimed

# Uploaded rules in this module stand for rules with a generated program.
pytestmark = pytest.mark.usefixtures("generated_rules")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The 控制计划 rule an operator publishes on the web; MCP never changes the rules an audit uses.
MCP_RULE = "00000000-0000-4000-8000-00000000c0de"
RULE_BODY = {
    "kind": "engine",
    "type": "控制计划",
    "rule_profile": "v3",
    "customers": ["理想"],
    "models": [],
    "parts": [],
    "rules": [{"id": "1", "category": "身份", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
}
TOOLS = {
    "find_submissions",
    "prepare_audit",
    "prepare_rule_clarification",
    "get_task",
    "get_issue",
    "resolve_issue",
    "confirm_audit",
    "get_results",
    "get_finding",
    "record_review",
    "publish_report",
    "find_audits",
    "get_operation",
    "get_account",
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
        authorization_server = client.get("/.well-known/oauth-authorization-server/oauth").json()
        assert "none" in authorization_server["token_endpoint_auth_methods_supported"]
        assert authorization_server["client_id_metadata_document_supported"] is True
        assert rpc(client, "invalid", "tools/list").status_code == 401
        cid, tokens, exchange = connection(client, store)
        assert client.post("/oauth/token", data=exchange).status_code == 400  # single use code
        call = rpc(client, tokens["access_token"], "tools/call", {"name": "get_account", "arguments": {}})
        assert call.status_code == 200, call.text
        value = call.json()["result"]["structuredContent"]
        assert value["next"]["kind"] == "done", value
        assert value["result"]["user"]["user_id"] == store.principal_for_fixture("alice").user_id
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
        result = rpc(client, tokens["access_token"], "tools/call", {"name": "find_audits", "arguments": {}}).json()["result"]["structuredContent"]
        assert result["next"]["kind"] == "blocked" and result["next"]["error_code"] == "insufficient_scope"
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


def test_consent_and_connections_say_who_the_client_is(mcp_app):
    # #356: a web client the platform registered is verified, a client named by its own HTTPS document shows that
    # document's domain, any other client only what it says of itself.
    app, store = mcp_app
    document_url = "https://client.example/ppap/client.json"
    documents = {
        document_url: {"client_id": document_url, "client_name": "Doc Client", "redirect_uris": ["http://127.0.0.1:4567/callback"]},
        "https://client.example/ppap/borrowed.json": {"client_id": document_url, "client_name": "Doc Client", "redirect_uris": ["http://127.0.0.1:4567/callback"]},
    }
    fetched = []

    def respond(request):
        url = "https://" + request.headers["host"] + request.url.path
        fetched.append(url)
        return httpx.Response(200, json=documents[url], headers={"Cache-Control": "max-age=300"}) if url in documents else httpx.Response(404)

    # Never the real network: the name resolves to a public address and a fake server answers.
    app.state.mcp_provider.read_document = functools.partial(read_client_document, resolve=lambda host, port: ["93.184.216.34"], transport=httpx.MockTransport(respond))
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        client.cookies.set("ppap_session", store.create_session(store.principal_for_fixture("alice")))

        def ask(cid, redirect):
            args = {
                "response_type": "code",
                "client_id": cid,
                "redirect_uri": redirect,
                "scope": "identity:read",
                "state": "s",
                "code_challenge": pkce_challenge(secrets.token_urlsafe(48)),
                "code_challenge_method": "S256",
                "resource": "http://127.0.0.1:8080/mcp",
            }
            return client.get("/oauth/authorize", params=args, follow_redirects=False)

        def consent(cid, redirect):
            started = ask(cid, redirect)
            assert started.status_code == 302, started.text
            path = urlsplit(started.headers["location"]).path
            shown = client.get(path)
            csrf = re.search(r'name="csrf" value="([^"]+)"', shown.text)[1]
            assert client.post(path, data={"csrf": csrf, "decision": "approve", "scope": ["identity:read"]}).status_code == 200
            return unescape(shown.text)

        def register(name, redirect):
            registered = client.post("/oauth/register", json={"client_name": name, "redirect_uris": [redirect], "token_endpoint_auth_method": "none"})
            assert registered.status_code == 201, registered.text
            return registered.json()["client_id"]

        # A document client may use only the callbacks its document declares, and only a document naming its own address.
        undeclared = ask(document_url, "http://127.0.0.1:4567/elsewhere")
        assert undeclared.status_code == 400 and "location" not in undeclared.headers
        assert ask("https://client.example/ppap/borrowed.json", "http://127.0.0.1:4567/callback").status_code == 400
        chatgpt = "https://chatgpt.com/connector/oauth/yjAMZ-kP_fvM"
        labels = {
            "Doc Client · 来自 client.example": (document_url, "http://127.0.0.1:4567/callback"),
            "ChatGPT · 已验证（chatgpt.com）": (register("PPAP 官方助手", chatgpt), chatgpt),
            "未验证（自称：PPAP 官方助手）": (register("PPAP 官方助手", "https://app.example/callback"), "https://app.example/callback"),
            "本机应用 · 无法验证来源（自称：Pi MCP test）": (register("Pi MCP test", "http://127.0.0.1:54121/callback"), "http://127.0.0.1:54121/callback"),
        }
        for label, (cid, redirect) in labels.items():
            shown = consent(cid, redirect)
            assert f"<strong>{label}</strong> 请求访问你的 PPAP 数据" in shown, shown
            assert ("PPAP 无法核实" in shown) == label.startswith(("未验证", "本机应用"))
        # Read once while it holds; the connections page says the same as the consent page.
        assert fetched.count(document_url) == 1
        connections = unescape(client.get("/mcp/connections").text)
        assert all(f"<h2>{label}</h2>" in connections for label in labels), connections
        # A proposal carries its client's identity, for the Web to mark one from a client PPAP cannot verify.
        mcp, alice = app.state.mcp_store, store.principal_for_fixture("alice")
        local = next(entry for entry in mcp.connections(alice) if entry["client_identity"]["level"] == "local")
        proposal = {"method": "POST", "path": "/rounds/x/report", "page": "/tasks/x", "request": {}}
        proposed = mcp.propose(mcp.delegation(local["id"]), str(uuid4()), "publish_report", proposal, "identity:read")
        intent = client.get("/intents/" + proposed["id"]).json()
        assert intent["client_identity"] == {"level": "local", "name": "Pi MCP test", "domain": None, "label": "本机应用 · 无法验证来源（自称：Pi MCP test）"}, intent


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


def intent_of(asked) -> str:
    step = asked["next"]
    assert step["kind"] == "user_action" and step["action_id"] == "complete_in_web", asked
    # The link enters the current edition first, then opens the page with the proposal.
    page = parse_qs(urlsplit(step["url"]).query)["return_to"][0]
    return parse_qs(urlsplit(page).query)["intent"][0]


def web_body(client, intent):
    """What the Web sends for this page's action, from what the page shows now (#348)."""
    if intent["path"].endswith("/confirm"):
        task = client.get(intent["path"].removesuffix("/confirm")).json()
        return {"request_id": str(uuid4()), "expected_revision": task["input_revision"], "expected_confirmation_basis_digest": task["confirmation_basis_digest"]}
    if intent["path"].endswith("/report"):
        return {"if_match": client.get(intent["path"]).json()["content_fingerprint"]}
    return (intent.get("step") or {}).get("body")


def complete(client, asked, body=None):
    """The person opens the page the proposal names and completes it there, naming the proposal; returns it and the reply."""
    ident = intent_of(asked)
    intent = client.get(f"/intents/{ident}")
    assert intent.status_code == 200, intent.text
    intent = intent.json()
    response = client.request(intent["method"], intent["path"], json=web_body(client, intent) if body is None else body, headers={"X-PPAP-Intent": ident})
    return intent, response


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


FOLDER = "https://example.feishu.cn/drive/folder/fldPpapTest1"


def book(*cells):
    stream = io.BytesIO()
    workbook = Workbook()
    workbook.active.append(list(cells))
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class Drive:
    """The user's Feishu: a folder of files (None: one that will not download) with an empty folder inside it, and a
    record of everything PPAP writes back."""

    def __init__(self, **files):
        self.files = files or {"plan": ("control-plan.xlsx", book("控制计划", "测试客户", "零件号 P-1"))}
        self.written = []

    def handler(self, request):
        path = request.url.path
        if request.method == "GET" and path.endswith("/drive/v1/files"):
            if request.url.params.get("folder_token") == "fldOld":
                return httpx.Response(200, json={"code": 0, "data": {"files": [], "has_more": False}})
            listed = [{"token": token, "name": name, "type": "file"} for token, (name, _) in self.files.items()]
            return httpx.Response(200, json={"code": 0, "data": {"files": [*listed, {"token": "fldOld", "name": "旧版", "type": "folder"}], "has_more": False}})
        if request.method == "POST" and path.endswith("/drive/v1/metas/batch_query"):
            token = json.loads(request.content)["request_docs"][0]["doc_token"]
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": token, "doc_type": "file", "title": self.files[token][0]}]}})
        if request.method == "GET" and path.endswith("/download"):
            name, data = self.files[path.rsplit("/files/", 1)[1].split("/download", 1)[0]]
            if data is None:
                return httpx.Response(404, json={"code": 99912345, "msg": "not found"})
            return httpx.Response(200, content=data, headers={"Content-Type": XLSX, "Content-Disposition": f'attachment; filename="{name}"'})
        assert request.method == "POST", path
        self.written.append((path, json.loads(request.content)))
        return httpx.Response(200, json={"code": 0, "data": {"document": {"document_id": "approved-report"}}})


@contextmanager
def folder_client(store, drive):
    """A signed-in Feishu user connected through MCP with every scope, whose drive is `drive`."""
    now = datetime.now(timezone.utc)
    session = store.create_session(store.principal_for_fixture("alice"), UserTokens("user-access", "user-refresh", now + timedelta(hours=1), now + timedelta(days=7)))
    with httpx.Client(transport=httpx.MockTransport(drive.handler)) as http:
        app = create_app(store.settings, auth_store=store, http=http)
        app.state.mcp_runtime.foreground, app.state.mcp_runtime.poll = 0.5, 0.05
        with TestClient(app, base_url="http://127.0.0.1:8080") as client:
            cid, tokens, _ = connection(client, store, scopes=" ".join(SCOPES), browser_session=session)
            yield app, client, tokens["access_token"], cid


def work(worker, name, **options):
    """Run the next job, waiting for the task to queue it: the worker is the test's, the task advances on its own."""
    for _ in range(200):
        job = worker.claim_job(name)
        if job is not None:
            process_claimed(worker, job, name, **options)
            return job
        time.sleep(0.05)
    raise AssertionError("no job was queued for " + name)


def drain(worker):
    """Finish whatever the audit left queued (its reports)."""
    while (job := worker.claim_job("mcp-rest")) is not None:
        process_claimed(worker, job, "mcp-rest")


def until(client, token, task, *phases):
    for _ in range(100):
        seen = call(client, token, "get_task", task_id=task)
        if (seen["result"] or {}).get("phase") in phases:
            return seen
        time.sleep(0.1)
    raise AssertionError(seen)


def prepared_folder(client, store, token, core_system, worker, *, adopt=True):
    """prepare_audit on the folder, with the worker doing the checking and recognising it asks for."""
    ask = str(uuid4())
    seen = call(client, token, "prepare_audit", request_id=ask, source=FOLDER, customer="理想")
    task = seen["task_id"]
    assert task != ask  # the task is named for this person, not by the client's request ID alone
    for _ in range(100):
        versions = [row["file_version_id"] for row in seen["result"]["files"]["items"] if row["file_version_id"]]
        if versions:
            break
        time.sleep(0.1)
        seen = call(client, token, "get_task", task_id=task)
    assert seen["result"]["phase"] == "preparing" and seen["next"]["kind"] == "wait", seen
    operator_rule(store, core_system, versions[0], adopt=adopt)
    work(worker, "mcp-check")
    work(worker, "mcp-match", rule_ai=MetadataAI())
    return task, ask, versions[0]


def confirmed(client, token, task, quote_id):
    confirm = {"task_id": task, "quote_id": quote_id, "request_id": str(uuid4())}
    intent, response = complete(client, call(client, token, "confirm_audit", **confirm))
    assert response.is_success, response.status_code
    return confirm, intent, call(client, token, "confirm_audit", **confirm)


def audited_folder(client, store, token, core_system):
    """A folder audited to the end, with a finding to read and review."""
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    task, _, _ = prepared_folder(client, store, token, core_system, worker)
    ready = until(client, token, task, "ready_to_confirm")
    confirmed(client, token, task, ready["result"]["quote"]["quote_id"])
    work(worker, "mcp-audit")
    assert work(worker, "mcp-pdf").kind == "report_pdf"
    drain(worker)
    done = until(client, token, task, "completed")
    return task, done


def test_a_feishu_folder_is_prepared_confirmed_and_audited_and_an_interruption_changes_nothing(mcp_app, core_system):
    _, store = mcp_app
    worker = build_product_store(store.settings, objects=store.objects, role="worker")
    drive = Drive(plan=("control-plan.xlsx", book("控制计划", "测试客户", "零件号 P-1")), broken=("broken.xlsx", None))
    with folder_client(store, drive) as (_, client, token, _):
        task, ask, version = prepared_folder(client, store, token, core_system, worker, adopt=False)
        seen = until(client, token, task, "awaiting_decision")
        # One file failed: it is an issue with its own choices; the other is recognised, prechecked and waiting.
        blocking = [issue for issue in seen["issues"] if issue["blocking"]]
        assert [(issue["code"], issue["subject"]["filename"]) for issue in blocking] == [("IMPORT_FAILED", "broken.xlsx")]
        assert seen["next"] == {"kind": "user_action", "issue_id": blocking[0]["id"], "prompt": blocking[0]["message"], "resolve_with": "resolve_issue"}
        ingest = next(stage for stage in seen["result"]["stages"] if stage["stage"] == "ingest")
        assert (ingest["state"], ingest["imported"], ingest["failed"]) == ("partial", 1, 1)
        assert "quote" in seen["result"]
        decision = {"task_id": task, "issue_id": blocking[0]["id"], "action_id": "exclude", "expected_revision": seen["task_revision"], "request_id": str(uuid4())}
        ready = call(client, token, "resolve_issue", **decision)
        assert ready["result"]["phase"] == "ready_to_confirm" and not any(issue["blocking"] for issue in ready["issues"]), ready
        assert call(client, token, "resolve_issue", **decision)["result"]["phase"] == "ready_to_confirm"
        quote = ready["result"]["quote"]
        assert quote["files_to_audit"] == 1 and quote["mode"] == "standard"
        assert ready["next"]["then"] == {"tool": "confirm_audit", "arguments": {"task_id": task, "quote_id": quote["quote_id"]}}
        # prepare_audit again is the same task: nothing is imported or created a second time.
        again = call(client, token, "prepare_audit", request_id=ask, source=FOLDER, customer="理想")
        assert again["task_id"] == task and again["result"]["phase"] == "ready_to_confirm"
        assert client.get("/audit-tasks").json()["tasks"][0]["id"] == task and len(client.get("/audit-tasks").json()["tasks"]) == 1
    # A new API process, with no memory of the calls above, reads the same task from what was saved.
    restarted = build_product_store(store.settings, objects=store.objects, role="api")
    with folder_client(restarted, drive) as (_, client, token, _):
        again = call(client, token, "get_task", task_id=task)
        assert again["result"]["phase"] == "ready_to_confirm" and again["result"]["quote"] == quote
        assert call(client, token, "confirm_audit", task_id=task, quote_id="an-older-quote", request_id=str(uuid4()))["next"]["reason"] == "quote_changed"
        confirm, intent, started = confirmed(client, token, task, quote["quote_id"])
        # The client proposed; the person started it on the task page, which shows the current files and price (#348).
        assert intent["page"] == f"/tasks/{task}" and intent["client_identity"]["label"] and intent["outcome"]["state"] == "proposed"
        assert started["result"]["phase"] == "auditing" and started["next"]["kind"] == "wait", started
        assert call(client, token, "confirm_audit", **confirm)["result"]["phase"] == "auditing"
        job = worker.claim_job("mcp-audit")
        assert job.kind == "audit" and worker.claim_job("no-second-audit") is None
        process_claimed(worker, job, "mcp-audit")
        drain(worker)
        done = until(client, token, task, "completed")
        assert done["next"] == {"kind": "call_tool", "tool": "get_results", "arguments": {"task_id": task}}
        results = call(client, token, "get_results", task_id=task)
        assert results["result"]["execution"] == "completed" and results["result"]["files"][0]["file_version_id"] == version
        assert results["next"]["kind"] == "done"
        assert call(client, token, "find_audits")["result"]["audits"][0]["task_id"] == task
        listed = call(client, token, "find_submissions", folder=FOLDER)["result"]
        assert listed["audits_from_this_folder"][0]["task_id"] == task
        # Every item says what prepare_audit takes for it (#350).
        assert listed["items"] and all(item["source"] for item in listed["items"])


def test_review_receipt_commits_with_effect_even_when_transport_loses_reply(mcp_app, core_system, monkeypatch):
    """A review is written at once under the granted scope (#355); a lost reply is read back, never written twice."""
    _, store = mcp_app
    with folder_client(store, Drive()) as (app, client, token, cid):
        task, done = audited_folder(client, store, token, core_system)
        round_id = client.get(f"/audit-tasks/{task}").json()["round_id"]
        listed = call(client, token, "get_results", task_id=task)["result"]["findings"][0]
        full = call(client, token, "get_finding", task_id=task, item_key=listed["item_key"])["result"]["value"]
        assert full["item_key"] == listed["item_key"] and full["status"] == listed["status"]
        # The tool reads the finding's current review version itself.
        args = {"task_id": task, "request_id": str(uuid4()), "item_key": listed["item_key"], "judgment": "confirm", "rationale": "已核对原件", "next_step": ""}
        original = app.state.mcp_gateway.request

        async def lost_reply(*a, **kw):
            response = await original(*a, **kw)
            if len(a) >= 2 and a[1] == "PUT":
                raise ConnectionError("response discarded after the actual commit")
            return response

        monkeypatch.setattr(app.state.mcp_gateway, "request", lost_reply)
        assert call(client, token, "record_review", **args)["next"]["kind"] == "recover"
        monkeypatch.setattr(app.state.mcp_gateway, "request", original)
        receipt = call(client, token, "get_operation", request_id=args["request_id"])
        assert receipt["next"]["kind"] == "done" and receipt["result"]["state"] == "succeeded", receipt
        assert receipt["result"]["provenance"]["client_id"] == cid
        assert receipt["result"]["data"]["findings"][0]["review"]["version"] == 1
        assert call(client, token, "record_review", **args)["result"] == {"item_key": listed["item_key"], "saved": True}
        current = client.get(f"/rounds/{round_id}").json()["findings"][0]
        # The machine's conclusion is as it was; the person's judgment is beside it, written once.
        assert current["status"] == listed["status"] and current["review"]["version"] == 1
        # The tool reads the current version itself: a later review is added on top of a Web edit, and history keeps both.
        web = {"item_key": listed["item_key"], "judgment": "disagree", "rationale": "网页复核已修订", "next_step": "", "version": 1}
        assert client.put(f"/rounds/{round_id}/reviews", json=web).status_code == 200
        later = {**args, "request_id": str(uuid4()), "rationale": "第二次核对"}
        assert call(client, token, "record_review", **later)["result"] == {"item_key": listed["item_key"], "saved": True}
        history = client.get(f"/rounds/{round_id}/reviews/history", params={"item_key": listed["item_key"]}).json()["versions"]
        assert [row["rationale"] for row in history] == ["第二次核对", "网页复核已修订", "已核对原件"]


@pytest.mark.parametrize("edit_at", ["after_reading", "after_capture"])
def test_a_published_report_is_the_document_the_person_saw(mcp_app, core_system, monkeypatch, edit_at):
    """The Web sends the content fingerprint it showed; a document that changed since is shown again, not sent (#348)."""
    _, store = mcp_app
    drive = Drive()
    with folder_client(store, drive) as (app, client, token, _):
        task, _ = audited_folder(client, store, token, core_system)
        round_id = client.get(f"/audit-tasks/{task}").json()["round_id"]
        finding = client.get(f"/rounds/{round_id}").json()["findings"][0]
        review = {"item_key": finding["item_key"], "judgment": "同意", "rationale": "原批准复核", "next_step": "", "version": 0}
        assert client.put(f"/rounds/{round_id}/reviews", json=review).status_code == 200
        arguments = {"task_id": task, "request_id": str(uuid4())}
        asked = call(client, token, "publish_report", **arguments)
        ident = intent_of(asked)
        path = f"/integrations/feishu/rounds/{round_id}/report"
        seen = client.get(path).json()["content_fingerprint"]

        def edit():
            saved = client.put(f"/rounds/{round_id}/reviews", json={**review, "version": 1, "rationale": "并发网页修改"})
            assert saved.status_code == 200, saved.text

        if edit_at == "after_reading":
            edit()
            refused = client.post(path, json={"if_match": seen}, headers={"X-PPAP-Intent": ident})
            assert refused.status_code == 409 and refused.json()["error"] == "content_changed"
            assert drive.written == []
            # Not done, and the client says so; the same link still completes it with what the page shows now.
            again = call(client, token, "publish_report", **arguments)
            assert again["next"]["url"] == asked["next"]["url"] and again["next"]["reason"] == "content_changed", again
            _, done = complete(client, asked)
            assert done.status_code == 200, done.text
            sent = json.dumps(drive.written, ensure_ascii=False)
            assert "并发网页修改" in sent and "原批准复核" not in sent
        else:
            # Once the document is fixed during execution, a later edit changes nothing that is sent.
            original_claim = store.claim_report

            def edit_after_materialization(*args, **kwargs):
                edit()
                return original_claim(*args, **kwargs)

            monkeypatch.setattr(store, "claim_report", edit_after_materialization)
            done = client.post(path, json={"if_match": seen}, headers={"X-PPAP-Intent": ident})
            assert done.status_code == 200, done.text
            sent = json.dumps(drive.written, ensure_ascii=False)
            assert len(drive.written) == 2 and "原批准复核" in sent and "并发网页修改" not in sent
        result = call(client, token, "publish_report", **arguments)
        assert result["next"]["kind"] == "done" and result["result"]["delivered"], result
        # What was sent is the current document only when nothing changed after it was written.
        assert (result["result"]["delivery"] or {}).get("status") == ("ready" if edit_at == "after_reading" else None)
        # Reading it again sends nothing again.
        count = len(drive.written)
        assert call(client, token, "get_operation", request_id=arguments["request_id"])["next"]["kind"] == "done"
        assert call(client, token, "publish_report", **arguments)["next"] == result["next"]
        assert len(drive.written) == count


def test_a_report_on_a_round_with_review_hints_is_published(mcp_app, core_system, monkeypatch):
    """GET /rounds/{id} adds the reader's review hints; the report entry must read the same projection (#347)."""
    from product.store import review_hints

    _, store = mcp_app
    drive = Drive()
    with folder_client(store, drive) as (app, client, token, _):
        task, _ = audited_folder(client, store, token, core_system)
        round_id = client.get(f"/audit-tasks/{task}").json()["round_id"]
        monkeypatch.setattr(review_hints, "read_hints", lambda store, conn, principal, body: [{"item_key": body["findings"][0]["item_key"], "reason": "differs"}])
        assert "review_hint" in client.get(f"/rounds/{round_id}").json()["findings"][0]
        arguments = {"task_id": task, "request_id": str(uuid4())}
        _, done = complete(client, call(client, token, "publish_report", **arguments))
        assert done.status_code == 200, done.text
        result = call(client, token, "publish_report", **arguments)
        assert result["next"]["kind"] == "done" and result["result"]["delivery"]["status"] == "ready", result
        assert len(drive.written) == 2
        # Saving a review answers with that same projection, so the Web's comparison after saving holds.
        key = client.get(f"/rounds/{round_id}").json()["findings"][0]["item_key"]
        saved = client.put(f"/rounds/{round_id}/reviews", json={"item_key": key, "judgment": "confirm", "rationale": "核对", "next_step": "", "version": 0})
        assert saved.status_code == 200 and "review_hint" in saved.json()["findings"][0]


def test_signing_is_proposed_by_the_client_and_completed_or_declined_by_the_person(mcp_app, core_system):
    """MCP only proposes a signing action; the person completes or declines it in the Web, and the client reads which (#348, #355)."""
    _, store = mcp_app
    drive = Drive()
    with folder_client(store, drive) as (app, client, token, _):
        task, _ = audited_folder(client, store, token, core_system)
        round_id = client.get(f"/audit-tasks/{task}").json()["round_id"]
        path = f"/integrations/feishu/rounds/{round_id}/report"
        arguments = {"task_id": task, "request_id": str(uuid4())}
        asked = call(client, token, "publish_report", **arguments)
        ident = intent_of(asked)
        web = store.settings.canonical_web_url().rstrip("/")
        assert asked["next"]["url"] == web + "/enter-current?" + urlencode({"return_to": f"{web}/tasks/{task}?intent={ident}"})
        assert call(client, token, "publish_report", **arguments)["next"]["url"] == asked["next"]["url"]
        # The same request_id is one proposal: other input under it is refused.
        other = call(client, token, "publish_report", task_id=str(uuid4()), request_id=arguments["request_id"])
        assert other["next"]["reason"] == "request_conflict" and other["next"]["strategy"] == "new_request_id", other
        # The proposal names one call; it cannot be spent on another.
        assert client.post(f"/audit-tasks/{task}/confirm", json={}, headers={"X-PPAP-Intent": ident}).json()["error"] == "intent_mismatch"
        # Completing a client's proposal needs a recent sign-in; the person's own Web action does not.
        with store._conn() as conn:
            conn.execute("UPDATE product.sessions SET created_at = now() - interval '1 hour'")
        stale = client.post(path, json={"if_match": client.get(path).json()["content_fingerprint"]}, headers={"X-PPAP-Intent": ident})
        assert stale.status_code == 401 and stale.json()["error"] == "reauthentication_required" and drive.written == []
        assert call(client, token, "get_operation", request_id=arguments["request_id"])["next"]["kind"] == "user_action"
        with store._conn() as conn:
            conn.execute("UPDATE product.sessions SET created_at = now()")
        # Declined, with a reason the client reads back; the declined proposal cannot be completed.
        declined = {"task_id": task, "request_id": str(uuid4())}
        other = intent_of(call(client, token, "publish_report", **declined))
        said = client.post(f"/intents/{other}/decline", json={"reason": "先等供应商补件"}, headers={"X-PPAP-Intent": other})
        assert said.status_code == 200 and said.json() == {"state": "declined", "reason": "先等供应商补件"}
        answer = call(client, token, "publish_report", **declined)
        assert answer["next"]["action_id"] == "denied_by_user" and answer["next"]["reason"] == "先等供应商补件", answer
        assert client.post(path, json={}, headers={"X-PPAP-Intent": other}).json()["error"] == "intent_unavailable"
        assert drive.written == []

        intent, done = complete(client, asked)
        assert done.status_code == 200 and len(drive.written) == 2, done.text
        settled = client.get(f"/intents/{ident}").json()
        # Done where the person did it, with their receipt (#361).
        assert settled["outcome"]["state"] == "succeeded" and settled["receipt"]["number"].startswith("R-") and done.headers["x-ppap-action"] == settled["receipt"]["id"]
        assert call(client, token, "publish_report", **arguments)["next"]["kind"] == "done" and len(drive.written) == 2
        # A settled proposal cannot be declined afterwards.
        assert client.post(f"/intents/{ident}/decline", json={}, headers={"X-PPAP-Intent": ident}).status_code == 409
        # The same document asked for again is already delivered: read back at once, nothing proposed or sent.
        again = call(client, token, "publish_report", task_id=task, request_id=str(uuid4()))
        assert again["next"]["kind"] == "done" and again["result"]["delivery"]["status"] == "ready", again
        assert len(drive.written) == 2


def test_every_write_by_a_person_is_recorded_once_with_channel_and_result(mcp_app, core_system):
    """Who did what, when, through what and with which result: one row per write request, any channel (#362)."""
    _, store = mcp_app
    drive = Drive()
    with folder_client(store, drive) as (app, client, token, _):
        task, _ = audited_folder(client, store, token, core_system)
        round_id = client.get(f"/audit-tasks/{task}").json()["round_id"]
        key = client.get(f"/rounds/{round_id}").json()["findings"][0]["item_key"]
        review = {"item_key": key, "judgment": "confirm", "rationale": "核对", "next_step": "", "version": 0}
        assert client.put(f"/rounds/{round_id}/reviews", json=review).status_code == 200
        assert client.put(f"/rounds/{round_id}/reviews", json=review).status_code == 409
        asked = call(client, token, "publish_report", task_id=task, request_id=str(uuid4()))
        complete(client, asked)

        def rows(route):
            with store._conn() as conn:
                return conn.execute(
                    "SELECT channel, grant_id IS NOT NULL, params, outcome, status_code, error, intent_id::text FROM product.actions WHERE route=%s ORDER BY at",
                    (route,),
                ).fetchall()

        reviews = rows("/rounds/{round_id}/reviews")
        assert [(r[0], r[1], r[2]["item_key"], r[3], r[4], r[5]) for r in reviews] == [
            ("web", False, key, "succeeded", 200, None),
            ("web", False, key, "failed", 409, "review_conflict"),
        ]
        # The person completed the client's proposal in the Web: their action, linked to the proposal.
        assert [(r[0], r[1], r[3], r[6]) for r in rows("/integrations/feishu/rounds/{round_id}/report")] == [("web", False, "succeeded", intent_of(asked))]
        # Reads are not actions; nothing without a known person is recorded.
        with store._conn() as conn:
            assert conn.execute("SELECT count(*) FROM product.actions WHERE method NOT IN ('POST','PUT','PATCH','DELETE')").fetchone()[0] == 0


def test_registry_annotations_and_browser_revoke(mcp_app):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as client:
        _, tokens, _ = connection(client, store, scopes=" ".join(SCOPES))
        token = tokens["access_token"]
        tools = {tool["name"]: tool for tool in rpc(client, token, "tools/list").json()["result"]["tools"]}
        # Commands and queries on a task; nothing uploads or downloads bytes, and rules change only through an approved clarification.
        assert tools.keys() == TOOLS
        for tool in tools.values():
            assert tool["inputSchema"]["type"] == "object" and {"result", "issues", "next"} <= tool["outputSchema"]["properties"].keys()
        assert set(tools["prepare_audit"]["inputSchema"]["required"]) == {"request_id", "source", "customer"}
        assert set(tools["confirm_audit"]["inputSchema"]["required"]) == {"task_id", "quote_id", "request_id"}
        assert set(tools["resolve_issue"]["inputSchema"]["required"]) == {"task_id", "issue_id", "action_id", "expected_revision", "request_id"}
        # One set of human judgments: a review recorded through MCP counts as reviewed on the Web and in the Meego plugin.
        from audit.audits import JUDGMENTS

        assert tools["record_review"]["inputSchema"]["properties"]["judgment"]["enum"] == list(JUDGMENTS)
        web = (Path(__file__).resolve().parents[3] / "apps/web/src/journey/judgment-state.ts").read_text(encoding="utf-8")
        assert all(f"{value}: " in web or f"'{value}': " in web for value in JUDGMENTS)
        for name in ("find_submissions", "get_task", "get_issue", "get_results", "get_finding", "find_audits", "get_operation", "get_account"):
            assert tools[name]["annotations"]["readOnlyHint"] is True
        # Effects that reach Feishu or start a charged audit cross the provider boundary.
        for name in ("prepare_audit", "resolve_issue", "confirm_audit", "record_review", "publish_report"):
            assert tools[name]["annotations"]["readOnlyHint"] is False and tools[name]["annotations"]["openWorldHint"] is True
        management = client.get("/mcp/connections")
        assert management.status_code == 200
        target = re.search(r'<form method="post" action="([^"]+)"', management.text)[1]
        csrf = re.search(r'name="csrf" value="([^"]+)"', management.text)[1]
        assert client.post(target, data={"csrf": "wrong"}).status_code == 403
        assert rpc(client, token, "tools/list").status_code == 200
        assert client.post(target, data={"csrf": csrf}, follow_redirects=False).status_code == 303
        assert rpc(client, token, "tools/list").status_code == 401
        # A revoked connection leaves the list of usable ones and waits behind its counted button, without a revoke button.
        folded = client.get("/mcp/connections").text
        assert "已失效的连接" in folded and "1 个" in folded and "已撤销" not in folded and 'method="post"' not in folded
        shown = client.get("/mcp/connections", params={"show": "inactive"}).text
        assert "已撤销（" in shown and 'method="post"' not in shown and "收起已失效的连接" in shown


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
                assert busy["next"] == {"kind": "recover", "strategy": "repeat_same_request", "check_after_ms": 5000, "reason": "provider_refresh_in_progress"}
                # Authentication remains valid; this is not an OAuth token failure.
                assert rpc(client, first["access_token"], "tools/list").status_code == 200
            finally:
                release.set()
            assert browser_request.result(timeout=10).status_code == 200
        assert call(client, first["access_token"], "get_account")["next"]["kind"] == "done"
        assert call(client, second["access_token"], "get_account")["next"]["kind"] == "done"
        assert store.load_session(second_browser).access_token == "access-rotated"
        repeated_login = store.create_session(
            principal, UserTokens("access-rotated", "refresh-rotated", datetime.now(timezone.utc) + timedelta(hours=1), datetime.now(timezone.utc) + timedelta(days=7))
        )
        assert store.load_session(repeated_login).provider_authorization_id == store.load_session(raw).provider_authorization_id
        assert requests == ["refresh-shared"]
        client.post("/oauth/revoke", data={"client_id": first_id, "token": first["access_token"]})
        assert rpc(client, first["access_token"], "tools/list").status_code == 401
        assert call(client, second["access_token"], "get_account")["next"]["kind"] == "done"
        assert client.get("/me").status_code == 200
        store.revoke_session(raw)
        # Browser logout revokes that session; it does not revoke separately
        # confirmed MCP grants that share the underlying provider credentials.
        assert client.get("/me").status_code == 401
        assert call(client, second["access_token"], "get_account")["next"]["kind"] == "done"


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
            assert call(client, tokens["access_token"], "get_account")["next"]["error_code"] == "provider_reauthorization_required"
        restarted_store = build_product_store(store.settings, objects=store.objects, role="api")
        with TestClient(create_app(store.settings, auth_store=restarted_store, http=http), base_url="http://127.0.0.1:8080") as client:
            assert call(client, tokens["access_token"], "get_account")["next"]["error_code"] == "provider_reauthorization_required"
            new_raw = restarted_store.create_session(principal, provider_tokens("new-login"))
            assert restarted_store.load_session(new_raw).provider_authorization_id != old_owner
            _, new_tokens, _ = connection(client, restarted_store, browser_session=new_raw)
            assert call(client, new_tokens["access_token"], "get_account")["next"]["kind"] == "done"
            assert call(client, tokens["access_token"], "get_account")["next"]["error_code"] == "provider_reauthorization_required"
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
            assert operation.result(timeout=10)["next"]["error_code"] == "invalid_token"
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
