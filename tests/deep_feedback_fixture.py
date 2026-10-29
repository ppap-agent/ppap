"""Real Product/PG/Worker/guest feedback loop; only model and S3 are controlled."""

from __future__ import annotations

# Reuse the Product fixture owner without adding repository roots to sys.path.
import importlib.util
import json
import os
import re
import secrets
import tempfile
import threading
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_api.mcp_deep import PREFIX, VERSION
from ppap_application import build_product_store
from ppap_core.identity import SESSION_COOKIE
from ppap_core.mcp import SCOPES, pkce_challenge
from ppap_core.settings import Settings
from ppap_deep.auditor import SemanticRuntime
from ppap_deep.controller import Controller, UnixControllerServer
from ppap_deep.semantic.guest import Manifest, execute
from ppap_deep.source import authorize_execution, object_file_source
from ppap_worker import process_claimed

_spec = importlib.util.spec_from_file_location("ppap_product_fixtures", Path(__file__).parent / "integration/product/conftest.py")
_product = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_product)
make_auth_store = _product.make_auth_store
FeedbackModel = _product.FeedbackModel
source_bytes = _product.source_bytes
RULES = _product.RULES


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


def rpc(env, token, method, params=None, capabilities=None, rpc_id=1):
    params = {
        **(params or {}),
        "_meta": {
            PREFIX + "protocolVersion": VERSION,
            PREFIX + "clientInfo": {"name": "local-acceptance", "version": "1"},
            PREFIX + "clientCapabilities": capabilities if capabilities is not None else {"elicitation": {"form": {}}},
        },
    }
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "Mcp-Method": method, "MCP-Protocol-Version": VERSION}
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    if method.startswith("tasks/"):
        headers["Mcp-Name"] = params["taskId"]
    response = env["client"].post("/mcp/deep", headers=headers, json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params})
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, name, data):
    created = client.post("/files", json={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(data)})
    assert created.status_code == 201, created.text
    value = created.json()
    path = f"/files/{value['file_id']}/versions/{value['id']}"
    assert client.put(path + "/blob", content=data).status_code == 200
    response = client.post(path + "/finalize")
    assert response.status_code == 200, response.text
    return response.json()


def environment(tmp_path, monkeypatch, request):
    local_cluster = bool(os.environ.get("PPAP_LOCAL_POSTGRES"))
    ci_cluster = bool(os.environ.get("CI") and os.environ.get("PGPORT") and os.environ.get("COMPOSE_PROJECT_NAME", "").startswith("ppap-ci-"))
    if not local_cluster and not ci_cluster:
        raise pytest.UsageError("Deep feedback acceptance requires --local-postgres")
    settings = Settings(usage_charge_enabled=False, mcp_enabled=True, feishu_app_id="isolated-dev-feishu-app", feishu_app_secret="", git_sha="a" * 40, public_api_url="http://127.0.0.1:8080")
    if ci_cluster and not local_cluster:
        # Parallel browser jobs must never truncate the shared test stack.
        # Reuse its existing database transport and production migration runner,
        # but give each feedback fixture an independently owned database.
        spec = importlib.util.spec_from_file_location("ppap_schema_test_transport", Path(__file__).parent / "schema/conftest.py")
        schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema_module)
        tools = schema_module.SchemaTools()
        database = "deep_feedback_" + uuid4().hex
        tools.require(tools.psql(tools.bootstrap_user(), tools.bootstrap_password(), "postgres", "-c", f"CREATE DATABASE {database} OWNER {tools.migrator_user()}"), "create feedback database")
        request.addfinalizer(
            lambda: tools.require(tools.psql(tools.bootstrap_user(), tools.bootstrap_password(), "postgres", "-c", f"DROP DATABASE {database} WITH (FORCE)"), "drop feedback database")
        )
        tools.require(tools.apply(database), "migrate feedback database")
        settings.pg_port = int(os.environ["PGPORT"])
        settings.pg_host = "127.0.0.1"
        settings.pg_database = database
        store = build_product_store(settings, objects=_product.MemoryObjects())
    else:
        store = make_auth_store(settings)
    store.seed_isolated_dev()
    principal = store.principal_for_fixture("alice")
    session_cookie = store.create_session(principal)
    model = FeedbackModel()
    revision = settings.deep_execution_revision()

    class Guest:
        usage_source = "controlled"

        def engine_revision(self):
            return revision

        def run(self, submission, inputs, work, deadline, cancelled, *, on_progress):
            assert Manifest.model_validate_json((inputs / "manifest.json").read_bytes()).spec == submission
            execute(inputs, work, model, deadline)

    import ppap_core.storage

    class S3:
        def get_object(self, **kw):
            return {"Body": BytesIO(store.objects.get(kw["Key"]))}

    monkeypatch.setattr(ppap_core.storage, "s3_client", lambda *_a, **_kw: S3())
    socket_dir = tempfile.TemporaryDirectory(prefix="pd-feedback-", dir="/tmp")
    request.addfinalizer(socket_dir.cleanup)
    socket = str(Path(socket_dir.name) / "c.sock")
    server = UnixControllerServer(socket, Controller(SemanticRuntime(Guest(), store.objects), object_file_source(settings), executor_revision=revision, authorize=authorize_execution(settings)))
    server.start()
    request.addfinalizer(server.close)
    settings.deep_controller_socket = socket
    worker = build_product_store(settings, objects=store.objects, role="worker")
    app = create_app(settings, auth_store=store)
    client = TestClient(app, base_url="http://127.0.0.1:8080")
    client.cookies.set(SESSION_COOKIE, session_cookie)
    body = {
        "kind": "engine",
        "type": "控制计划",
        "rule_profile": "v3",
        "customers": ["测试客户"],
        "models": [],
        "parts": [],
        "rules": [{**r, "category": "跨文件", "priority": "P1", "type_of_rule": "table"} for r in RULES],
    }
    source = upload(client, "rules.json", json.dumps(body, ensure_ascii=False).encode())
    published = client.post("/rules", json={"title": "跨文件构造合同", "body": body, "source_file_version_id": source["id"]})
    assert published.status_code == 201, published.text
    rule = published.json()
    documents = {kind: {"name": kind + ".xlsx", "bytes": source_bytes(kind)} for kind in ["PFD", "CP"]}
    uploaded = {kind: upload(client, item["name"], item["bytes"]) for kind, item in documents.items()}
    draft = client.post("/drafts").json()["id"]
    items = [{"file_version_id": uploaded["CP"]["id"], "role": "target", "rule_version_id": rule["id"]}, {"file_version_id": uploaded["PFD"]["id"], "role": "reference"}]
    saved = client.put("/drafts/" + draft, json={"items": items, "analysis_mode": "deep"})
    assert saved.status_code == 200, saved.text
    accepted = client.post("/drafts/" + draft + "/accept", json={"idempotency_key": str(uuid4())})
    assert accepted.status_code == 201, accepted.text
    round_id = accepted.json()["id"]
    results = []
    lock = threading.Lock()

    def run_jobs():
        with lock:
            while job := worker.claim_job("feedback-local-worker"):
                result = process_claimed(worker, job, "feedback-local-worker")
                results.append(result)
                assert not result.get("failure"), result

    try:
        run_jobs()
        yield {
            "app": app,
            "store": store,
            "client": client,
            "worker": worker,
            "run_jobs": run_jobs,
            "documents": documents,
            "uploaded": uploaded,
            "round_id": round_id,
            "task_id": None,
            "principal": principal,
            "session_cookie": session_cookie,
            "rule": rule,
            "model": model,
            "worker_results": results,
            "settings": settings,
            "mcp_access": lambda: connection(client, store, scopes=" ".join(SCOPES))[1]["access_token"],
            "mcp_rpc": lambda token, method, params=None, capabilities=None, rpc_id=1: rpc({"client": client}, token, method, params, capabilities, rpc_id),
        }
    finally:
        server.close()
        client.close()
