"""Bounded failure diagnostics for existing live OCR and UI preview gates."""

import inspect
import itertools
import json
import logging
import os
import re
import secrets
import shutil
import socket
import socket as socket_module
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from html import unescape
from io import BytesIO
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from openpyxl import Workbook
from ppap_api import create_app
from ppap_application import build_product_store
from ppap_core.db import connect
from ppap_core.files import ObjectExists, ObjectMissing, ObjectTooLarge
from ppap_core.identity import SESSION_COOKIE
from ppap_core.mcp import SCOPES, pkce_challenge
from ppap_core.settings import ISOLATED_DEV_SECRET, Settings
from ppap_deep.auditor import SemanticRuntime
from ppap_deep.controller import Controller, UnixControllerServer
from ppap_deep.semantic.guest import CompileManifest, Manifest, execute, execute_compilation
from ppap_deep.semantic.models import RulePlan
from ppap_deep.semantic.protocol import CompileSpec
from ppap_deep.semantic.trace import AuditTrace
from ppap_deep.source import authorize_execution, object_file_source
from ppap_product_store import AuthStore
from ppap_worker import process_claimed
from psycopg import sql


def pytest_addoption(parser):
    parser.addoption("--local-postgres", action="store_true", help="Create a disposable native PostgreSQL cluster for this test process")


def pytest_configure(config):
    if not config.getoption("--local-postgres"):
        return
    root = Path(__file__).resolve().parents[1]
    installed = shutil.which("postgres")
    bindir = Path(os.environ.get("PPAP_TEST_PG_BIN", str(Path(installed).parent) if installed else "/opt/homebrew/opt/postgresql@17/bin"))
    for name in ("postgres", "initdb", "pg_ctl", "psql"):
        if not (bindir / name).is_file():
            raise pytest.UsageError(f"--local-postgres requires {name}; set PPAP_TEST_PG_BIN")
    temporary = Path(tempfile.mkdtemp(prefix="ppap-test-pg-"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        "PGHOST": "127.0.0.1",
        "PGPORT": str(port),
        "PGUSER": "ppap",
        "PGPASSWORD": "ppap_isolated_dev",
        "PPAP_PG_HOST": "127.0.0.1",
        "PPAP_PG_PORT": str(port),
        "PPAP_PG_DATABASE": "ppap",
        "PPAP_PG_BOOTSTRAP_USER": "ppap",
        "PPAP_PG_BOOTSTRAP_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_MIGRATOR_USER": "ppap_migrator",
        "PPAP_PG_MIGRATOR_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_API_USER": "ppap_api",
        "PPAP_PG_API_PASSWORD": "ppap_isolated_dev",
        "PPAP_PG_WORKER_USER": "ppap_worker",
        "PPAP_PG_WORKER_PASSWORD": "ppap_isolated_dev",
        "PPAP_LOCAL_POSTGRES": str(temporary),
        "PPAP_TEST_PG_BIN": str(bindir),
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
    }
    previous = {key: os.environ.get(key) for key in environment}
    config._ppap_local_postgres = (temporary, bindir, previous)
    os.environ.update(environment)

    def run(command):
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise pytest.UsageError("Local PostgreSQL setup failed: " + (result.stderr or result.stdout)[-2500:])

    try:
        run([str(bindir / "initdb"), "-D", str(temporary / "data"), "-U", "ppap", "-A", "trust", "--no-locale", "-E", "UTF8"])
        run([str(bindir / "pg_ctl"), "-D", str(temporary / "data"), "-l", str(temporary / "postgres.log"), "-o", f"-h 127.0.0.1 -p {port} -k {temporary} -c timezone=UTC", "-w", "start"])
        run([str(bindir / "psql"), "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", str(root / "migrations/bootstrap.sql")])
        run(["sh", str(root / "migrations/apply.sh")])
    except BaseException:
        pytest_unconfigure(config)
        raise


def pytest_unconfigure(config):
    owned = getattr(config, "_ppap_local_postgres", None)
    if owned is None:
        return
    temporary, bindir, previous = owned
    del config._ppap_local_postgres
    try:
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(temporary / "data"), "-m", "immediate", "-w", "stop"], capture_output=True, timeout=30)
    finally:
        shutil.rmtree(temporary)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def ocr_transport_diagnostics(monkeypatch, caplog):
    """Observe real requests without reading bodies, URLs, headers or trace info."""
    logger = logging.getLogger("tests.ocr_transport")
    caplog.set_level(logging.INFO, logger=logger.name)
    caplog.set_level(logging.INFO, logger="ppap_pdf_to_excel.vision.cell_ocr")
    original = httpx.AsyncClient.send
    original_close = httpx.AsyncClient.aclose
    original_read = httpx.Response.aread
    sequence = itertools.count(1)
    clients = {}
    requests = {}

    async def send(client, request, *args, **kwargs):
        number, started, phase = next(sequence), monotonic(), "dispatch"
        clients[id(client)] = number
        state = {"number": number, "last_event": "dispatch"}
        requests[id(request)] = state
        previous = request.extensions.get("trace")

        async def trace(event_name, info):
            nonlocal phase
            state["last_event"] = event_name
            # Preserve the waiting phase when cancellation emits later close events.
            if event_name.endswith(".started") and "close" not in event_name:
                phase = event_name
            if previous:
                result = previous(event_name, info)
                if inspect.isawaitable(result):
                    await result

        request.extensions["trace"] = trace
        try:
            response = await original(client, request, *args, **kwargs)
            logger.info("OCR transport complete: request=%d stage=%s elapsed_ms=%d", number, phase, round((monotonic() - started) * 1000))
            return response
        except BaseException as error:
            logger.warning(
                "OCR transport stopped: request=%d stage=%s last_event=%s elapsed_ms=%d error_type=%s", number, phase, state["last_event"], round((monotonic() - started) * 1000), type(error).__name__
            )
            raise

    async def close(client):
        number, started = clients.get(id(client), 0), monotonic()
        logger.info("OCR transport close starting: request=%d", number)
        try:
            await original_close(client)
            logger.info("OCR transport close complete: request=%d elapsed_ms=%d", number, round((monotonic() - started) * 1000))
        except BaseException as error:
            logger.warning("OCR transport close stopped: request=%d elapsed_ms=%d error_type=%s", number, round((monotonic() - started) * 1000), type(error).__name__)
            raise

    async def read(response):
        try:
            return await original_read(response)
        except BaseException as error:
            try:
                state = requests.get(id(response.request), {})
            except RuntimeError:
                state = {}
            content_type = response.headers.get("content-type", "").lower()
            kind = "event-stream" if "text/event-stream" in content_type else "json" if "json" in content_type else "other"
            length = response.headers.get("content-length", "")
            declared_bytes = int(length) if length.isdecimal() and len(length) < 20 else None
            logger.warning(
                "OCR body stopped: request=%d last_event=%s status=%d downloaded_bytes=%d declared_bytes=%s kind=%s error_type=%s",
                state.get("number", 0),
                state.get("last_event", "unknown"),
                response.status_code,
                response.num_bytes_downloaded,
                declared_bytes,
                kind,
                type(error).__name__,
            )
            raise

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", close)
    monkeypatch.setattr(httpx.Response, "aread", read)


@pytest.fixture(autouse=True)
def preview_failure_diagnostics(request, monkeypatch):
    preview = os.environ.get("PPAP_PREVIEW_URL", "")
    original = getattr(request.module, "sync_playwright", None)
    if not preview or not str(request.path).endswith("_preview.py") or not original:
        return
    from playwright.sync_api import Browser, BrowserContext

    origin = urlsplit(preview)
    pages = deque(maxlen=3)

    def asset_path(url):
        parsed = urlsplit(url)
        return parsed.path if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and parsed.path.startswith(("/preview/", "/src/", "/node_modules/", "/@")) else None

    def observe(create):
        def new_page(*args, **kwargs):
            page = create(*args, **kwargs)
            events = deque(maxlen=12)
            first_asset_failure = {}

            def failed_asset(event):
                if path := asset_path(event.url):
                    # Chromium failure messages can contain URLs. Keep only its
                    # error code and the already-filtered same-origin path.
                    code = re.match(r"net::ERR_[A-Z0-9_]+\b", event.failure or "")
                    failure = {"request_failed": path, "error_code": code.group() if code else "unknown"}
                    if not first_asset_failure:
                        first_asset_failure.update(failure)
                    events.append(failure)

            def failed_response(event):
                if event.status >= 400 and (path := asset_path(event.url)):
                    failure = {"status": event.status, "asset": path}
                    if not first_asset_failure:
                        first_asset_failure.update(failure)
                    events.append(failure)

            page.on(
                "pageerror",
                lambda error: events.append(
                    {
                        "error_type": getattr(error, "name", "Error"),
                        "scripts": [path for url in re.findall(r"https?://[^\s)]+", (getattr(error, "stack", "") or "").split("\n", 1)[-1]) if (path := asset_path(url))][:3],
                    }
                ),
            )
            page.on("console", lambda event: events.append({"console_error": True}) if event.type == "error" else None)
            page.on("requestfailed", failed_asset)
            page.on("response", failed_response)
            pages.append((page, events, first_asset_failure))
            return page

        return new_page

    @contextmanager
    def observed_playwright():
        with original() as playwright:
            try:
                yield playwright
            except BaseException:
                for page, events, first_asset_failure in pages:
                    result = {"first_asset_failure": first_asset_failure, "events": list(events), "closed": page.is_closed()}
                    if not page.is_closed():
                        try:
                            result["document"] = page.locator("body").evaluate(
                                """() => ({
                                    readyState: document.readyState,
                                    visibility: document.visibilityState,
                                    appChildren: document.getElementById('app')?.childElementCount,
                                    bodyTextLength: document.body?.innerText.length,
                                    headings: document.querySelectorAll('h1,h2').length,
                                    alerts: document.querySelectorAll('[role=alert]').length,
                                    sampleButtonPresent: [...document.querySelectorAll('button')].some(node => node.textContent.trim() === '使用三类文件示例'),
                                    overviewPresent: Boolean(document.querySelector('[role=region][aria-label="全部文件概览"]')),
                                    bodyAriaHidden: document.body?.getAttribute('aria-hidden')
                                })""",
                                timeout=1000,
                            )
                        except Exception as error:
                            result["snapshot_error_type"] = type(error).__name__
                    print("Preview failure diagnostics: " + json.dumps(result), file=sys.stderr)
                raise

    monkeypatch.setattr(Browser, "new_page", observe(Browser.new_page))
    monkeypatch.setattr(BrowserContext, "new_page", observe(BrowserContext.new_page))
    monkeypatch.setattr(request.module, "sync_playwright", observed_playwright)


class MemoryObjects:
    """Small in-memory fake for the object-storage boundary only."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if key not in self.data:
            raise ObjectMissing()
        body = self.data[key]
        if max_bytes is not None and len(body) > max_bytes:
            raise ObjectTooLarge()
        return body

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def reset_product_database(settings: Settings) -> None:
    with connect(settings, "migrator") as conn:
        tables = conn.execute(
            """
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname IN ('product', 'engine')
            ORDER BY schemaname, tablename
            """
        ).fetchall()
        targets = [sql.Identifier(schema, table) for schema, table in tables if not (schema == "product" and table in {"schema_migrations", "trial_cutover_baselines"})]
        if targets:
            conn.execute(sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.SQL(", ").join(targets)))
        conn.execute("DELETE FROM product.trial_cutover_baselines WHERE tenant_key LIKE 'isolated-dev-%'")


def make_auth_store(
    settings: Settings | None = None,
    objects=None,
    *,
    reset: bool = True,
    role: str = "migrator",
) -> AuthStore:
    if os.environ.get("CI") and not os.environ.get("PGPORT"):
        raise RuntimeError("CI database fixture requires an explicit PGPORT; refusing default database")
    settings = settings or Settings()
    settings.pg_host = "127.0.0.1"
    settings.pg_port = int(os.environ.get("PGPORT", "5432"))
    settings.pg_database = "ppap"
    settings.pg_migrator_user = "ppap_migrator"
    settings.pg_migrator_password = ISOLATED_DEV_SECRET
    if reset:
        reset_product_database(settings)
    return build_product_store(settings, objects=objects or MemoryObjects(), role=role)


EXPLANATION = "本控制计划中的作业单元编号表示工序编号；按原始编号逐字对应，不合并不同编号。"
QUESTION_ID = "cp-operation-code"
CANDIDATE_ID = "operation-code"
RULES = [
    {"id": "R1", "rule_to_check": "流程图中的每个工序编号必须在控制计划中按原始编号一一对应，否则不通过。"},
    {"id": "R2", "rule_to_check": "对应工序的分类必须在流程图和控制计划中一致，否则不通过。"},
    {"id": "R3", "rule_to_check": "流程图中的每个工序编号必须非空，否则不通过。"},
]


def source_bytes(kind):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = kind
    rows = [["工序编号", "分类"], ["010", "CC"], ["020", "SC"]] if kind == "PFD" else [["作业单元编号", "分类"], ["010", "SC"], [None, None], ["020", "SC"]]
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class FeedbackModel:
    input_tokens = 0
    output_tokens = 0

    def __init__(self):
        self.calls = []

    def run(self, work, prompt, schema, deadline, *, reasoning_effort):
        self.calls.append(work.name)
        assert reasoning_effort == "medium"
        if issubclass(schema, RulePlan):
            supplied = prompt.split("identity and ordered clause IDs must match exactly: ", 1)[1]
            identity, _ = json.JSONDecoder().raw_decode(supplied)
            clauses = json.loads((work / "clauses.json").read_text())
            assert [(clause["id"], clause["rule_to_check"]) for clause in clauses] == [(clause["id"], clause["rule_to_check"]) for clause in RULES]
            relation = {"kind": "related", "relationship_id": "corresponds", "target": "cp", "source_key": {"fields": ["code"]}, "target_key": {"fields": ["code"]}, "match": "one_to_one"}
            programs = [
                relation,
                {**relation, "comparisons": [{"source_field": "classification", "target_field": "classification", "mode": "exact"}]},
                {"kind": "value_test", "field": "code", "op": "not_empty"},
            ]
            return RulePlan.model_validate(
                {
                    "source_sha256": identity["source_sha256"],
                    "plan_revision": identity["plan_revision"],
                    "ontology": {
                        "entities": [
                            {"id": entity, "grain": "按明确工序编号标识的一个工序，可跨多行", "attributes": {"code": "string", "classification": "string"}, "identity": ["code"]}
                            for entity in ["pfd", "cp"]
                        ],
                        "relationships": [{"id": "corresponds", "kind": "same_instance", "source": "pfd", "target": "cp", "meaning": "源工序在目标文件中按原始编号一一对应"}],
                    },
                    "rules": [
                        {
                            "id": clause["id"],
                            "source_locator": {"kind": "published_clause", "clause_id": clause["id"]},
                            "program": {"kind": "records", "source": "pfd", "quantifier": "forall", "require": program},
                            "empty_scope_status": "UNABLE",
                            "empty_scope_basis": "原文未规定没有工序时的结论。",
                        }
                        for clause, program in zip(clauses, programs, strict=True)
                    ],
                }
            )
        assert schema is AuditTrace
        documents = json.loads((work / "documents.json").read_text())
        targets = json.loads((work / "targets.json").read_text())
        bindings, identities = [], {}
        for document_id, document in documents.items():
            table = document["tables"][0]
            kind = table["id"]
            assert kind in {"PFD", "CP"}
            name = kind.lower()
            identities[name] = document_id
            binding = {
                "id": name,
                "document_id": document_id,
                "source_sha256": document["source"]["sha256"],
                "table": kind,
                "header_rows": [1],
                "first_row": 2,
                "last_row": table["rows"],
                "columns": {"code": "A", "classification": "B"},
                "mapping_basis": "原件首行字段及明确工序身份",
                "boundary_basis": "示例表完整正文",
            }
            if kind == "CP":
                binding["interpretations"] = [
                    {
                        "id": QUESTION_ID,
                        "field": "code",
                        "question": "本控制计划的作业单元编号是否表示工序编号？",
                        "candidates": [{"id": CANDIDATE_ID, "label": "按工序编号理解", "explanation": EXPLANATION}],
                        "evidence": [{"document_id": document_id, "locator": {"kind": "xlsx_cell", "sheet": "CP", "cell": "A1"}}],
                    }
                ]
            bindings.append(binding)
        pfd = {"kind": "bound", "scopes": [{"binding_id": "pfd", "objects": [{"id": "010", "rows": [2]}, {"id": "020", "rows": [3]}]}]}
        cp = {"kind": "bound", "scopes": [{"binding_id": "cp", "objects": [{"id": "010", "rows": [2, 3]}, {"id": "020", "rows": [4]}]}]}
        for collection, table in [(pfd, "PFD"), (cp, "CP")]:
            for item in collection["scopes"][0]["objects"]:
                item["basis"] = "原始工序编号及其后续空白行共同描述一个工序；不同编号分别保留。"
                item["evidence"] = [{"kind": "xlsx_cell", "sheet": table, "cell": f"A{row}"} for row in item["rows"]]
        uses = [{"audit_id": target, "rule_id": clause["id"], "inputs": {"pfd": pfd, **({"cp": cp} if clause["id"] != "R3" else {})}} for target in targets for clause in RULES]
        return AuditTrace.model_validate({"bindings": bindings, "uses": uses})


PREFIX = "io.modelcontextprotocol/"
VERSION = "2026-07-28"


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
        "resource": store.settings.public_api_url.rstrip("/") + "/mcp",
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


def rpc(env, token, method, params=None, capabilities=None, rpc_id=None):
    if rpc_id is None:
        rpc_id = len(env["wire_history"]) + 1
    params = {
        **(params or {}),
        "_meta": {
            PREFIX + "protocolVersion": VERSION,
            PREFIX + "clientInfo": {"name": "local-acceptance", "version": "1"},
            PREFIX + "clientCapabilities": capabilities if capabilities is not None else {"elicitation": {"form": {}}},
        },
    }
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream", "Mcp-Method": method, "MCP-Protocol-Version": VERSION}
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    if method.startswith("tasks/"):
        headers["Mcp-Name"] = params["taskId"]
    message = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
    response = env["wire_client"].post("/mcp/deep", headers=headers, json=message)
    env["wire_history"].append(message)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["jsonrpc"] == "2.0" and result["id"] == rpc_id
    return result


def upload(client, name, data):
    created = client.post("/files", json={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(data)})
    assert created.status_code == 201, created.text
    value = created.json()
    path = f"/files/{value['file_id']}/versions/{value['id']}"
    assert client.put(path + "/blob", content=data).status_code == 200
    response = client.post(path + "/finalize")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def deep_feedback_env(tmp_path, monkeypatch, request):
    local_cluster = bool(os.environ.get("PPAP_LOCAL_POSTGRES"))
    ci_cluster = bool(os.environ.get("CI") and os.environ.get("PGPORT") and os.environ.get("COMPOSE_PROJECT_NAME", "").startswith("ppap-ci-"))
    if not local_cluster and not ci_cluster:
        raise pytest.UsageError("Deep feedback acceptance requires --local-postgres")
    settings = Settings(usage_charge_enabled=False, mcp_enabled=True, feishu_app_id="isolated-dev-feishu-app", feishu_app_secret="", git_sha="a" * 40, public_api_url="http://127.0.0.1:8080")
    if ci_cluster and not local_cluster:
        # Parallel browser jobs must never truncate the shared test stack.
        # Reuse its existing database transport and production migration runner,
        # but give each feedback fixture an independently owned database.
        import psycopg

        database = "deep_feedback_" + uuid4().hex
        migrator = os.environ.get("PPAP_PG_MIGRATOR_USER", "ppap_migrator")

        def database_sql(statement):
            with psycopg.connect(
                host="127.0.0.1", port=int(os.environ["PGPORT"]), dbname="postgres",
                user=os.environ.get("PPAP_PG_BOOTSTRAP_USER", "ppap"),
                password=os.environ.get("PPAP_PG_BOOTSTRAP_PASSWORD", ISOLATED_DEV_SECRET), autocommit=True,
            ) as connection:
                connection.execute(statement)

        database_sql(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(migrator)))
        request.addfinalizer(lambda: database_sql(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))))
        result = subprocess.run(
            ["docker", "compose", "-f", str(Path(__file__).resolve().parents[1] / "deploy/isolated-dev/compose.yaml"),
             "exec", "-T", "-e", "PGHOST=127.0.0.1", "-e", "PGPORT=5432", "-e", "PPAP_PG_DATABASE=" + database,
             "-e", "PPAP_PG_MIGRATOR_USER=" + migrator,
             "-e", "PPAP_PG_MIGRATOR_PASSWORD=" + os.environ.get("PPAP_PG_MIGRATOR_PASSWORD", ISOLATED_DEV_SECRET),
             "postgres", "sh", "/migrations/apply.sh"], capture_output=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]
        settings.pg_port = int(os.environ["PGPORT"])
        settings.pg_host = "127.0.0.1"
        settings.pg_database = database
        store = build_product_store(settings, objects=MemoryObjects())
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

        def run(self, submission, inputs, work, deadline, cancelled, *, on_progress, prior_usage=None):
            compiling = isinstance(submission, CompileSpec)
            manifest_type = CompileManifest if compiling else Manifest
            assert manifest_type.model_validate_json((inputs / "manifest.json").read_bytes()).spec == submission
            if compiling:
                execute_compilation(inputs, work, model, deadline)
            else:
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
    wire_socket = socket_module.socket()
    wire_socket.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
    wire_socket.bind(("127.0.0.1", 0))
    wire_socket.listen()
    request.addfinalizer(wire_socket.close)
    wire_url = "http://127.0.0.1:" + str(wire_socket.getsockname()[1])
    settings.public_api_url = wire_url
    app = create_app(settings, auth_store=store)
    client = TestClient(app, base_url=wire_url)
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
        env = {
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
            "wire_url": wire_url,
            "wire_history": [],
        }
        wire_server = wire_thread = None

        def stop_wire():
            if wire_server is not None:
                env["wire_client"].close()
                wire_server.should_exit = True
                wire_thread.join(10)
                assert not wire_thread.is_alive(), "feedback HTTP server did not stop"
                wire_socket.close()

        def start_wire(*, restart=False):
            nonlocal wire_server, wire_thread, wire_socket
            if wire_server is not None and not restart:
                return env["wire_client"]
            if restart:
                stop_wire()
                # A new application and store use the same PG/object facts. No
                # protocol Task or question state is carried from the old app.
                fresh_store = build_product_store(settings, objects=store.objects)
                env["app"] = create_app(settings, auth_store=fresh_store)
                wire_socket = socket_module.socket()
                wire_socket.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
                wire_socket.bind(("127.0.0.1", int(urlsplit(wire_url).port)))
                wire_socket.listen()
            wire_server = uvicorn.Server(uvicorn.Config(env["app"], log_level="critical", access_log=False))
            wire_thread = threading.Thread(target=wire_server.run, kwargs={"sockets": [wire_socket]}, daemon=True)
            wire_thread.start()
            deadline = time.monotonic() + 10
            while not wire_server.started and wire_thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert wire_server.started, "feedback HTTP server did not start"
            env["wire_client"] = httpx.Client(base_url=wire_url, timeout=30, trust_env=False)
            env["wire_client"].cookies.set(SESSION_COOKIE, session_cookie)
            return env["wire_client"]

        env["start_wire"] = start_wire
        env["restart_wire"] = lambda: start_wire(restart=True)
        env["mcp_access"] = lambda person="alice", scopes=" ".join(SCOPES): connection(start_wire(), store, person=person, scopes=scopes)[1]["access_token"]
        env["mcp_rpc"] = lambda token, method, params=None, capabilities=None, rpc_id=None: rpc(env, token, method, params, capabilities, rpc_id)
        try:
            yield env
        finally:
            stop_wire()
    finally:
        server.close()
        client.close()
