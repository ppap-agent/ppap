"""Installed FastMCP client -> HTTP/ASGI -> real PPAP PostgreSQL contracts.

Browser consent is driven by TestClient with a fixture identity. These checks
do not establish native browser/keychain behavior or live Feishu acceptance.
"""

import asyncio
import hashlib
import io
import json
import re
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from openpyxl import Workbook
from ppap_api import create_app
from ppap_mcp_client.__main__ import bridge
from ppap_mcp_client.auth import SharedOAuth, encrypted_store
from ppap_mcp_client.files import LocalFiles

URL = "http://127.0.0.1:8080/mcp"


class Wire(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self.asgi = httpx.ASGITransport(app=app)
        self.events = []
        self.drop_refresh = False
        self.drop_tool = None
        self.drop_put = False

    async def handle_async_request(self, request):
        body = await request.aread()
        method = None
        tool = None
        if request.url.path == "/mcp" and body:
            rpc = json.loads(body)
            method = rpc.get("method")
            tool = rpc.get("params", {}).get("name")
        refresh = request.url.path == "/oauth/token" and parse_qs(body.decode()).get("grant_type") == ["refresh_token"]
        # Store only operation names; never tokens, codes, or file bodies.
        self.events.append((request.method, request.url.path, method, tool, refresh))
        response = await self.asgi.handle_async_request(request)
        drop = (refresh and self.drop_refresh) or (tool and tool == self.drop_tool) or (request.method == "PUT" and self.drop_put)
        if drop:
            self.drop_refresh = self.drop_put = False
            self.drop_tool = None
            await response.aclose()
            raise httpx.ReadError("controlled lost response", request=request)
        return response

    def http(self, **kwargs):
        return httpx.AsyncClient(transport=self, **kwargs)

    def client(self, auth):
        return Client(StreamableHttpTransport(URL, auth=auth, httpx_client_factory=self.http), timeout=30)


def consent_oauth(browser, *, approved=None):
    class BrowserOAuth(OAuth):
        async def redirect_handler(self, authorization_url):
            opened = browser.get(authorization_url, follow_redirects=False)
            assert opened.status_code == 302
            path = urlsplit(opened.headers["location"]).path
            page = browser.get(path)
            assert page.status_code == 200
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
            offered = parse_qs(urlsplit(authorization_url).query)["scope"][0].split()
            decision = browser.post(path, data={"csrf": csrf, "decision": "approve", "scope": approved or offered}, follow_redirects=False)
            assert decision.status_code == 303
            location = urlsplit(decision.headers["location"])
            assert str(self.context.client_metadata.redirect_uris[0]).split("?", 1)[0] == decision.headers["location"].split("?", 1)[0]
            result = parse_qs(location.query)
            self.returned = result["code"][0], result["state"][0]

        async def callback_handler(self):
            return self.returned

    return BrowserOAuth


def credentials(path, key):
    return encrypted_store(path, key)


def adapter_setup(app, store, browser, tmp_path, *, approved=None):
    browser.cookies.set("ppap_session", store.create_session(store.principal_for_fixture("alice")))
    key = Fernet.generate_key()
    auth = SharedOAuth(URL, tmp_path, credentials(tmp_path, key), oauth_factory=consent_oauth(browser, approved=approved))
    return Wire(app), auth, key


async def identity(wire, auth):
    async with wire.client(auth) as client:
        result = await client.call_tool("get_identity")
        assert result.structured_content["status"] == "ok"
        return result.structured_content["data"]["value"]


async def expire(auth):
    await auth.storage.put(URL + "/token_expiry", {"expires_at": time.time() - 1}, collection="mcp-oauth-token-expiry")


@pytest.mark.asyncio
async def test_sdk_initial_consent_restart_concurrent_refresh_and_revocation(mcp_app, tmp_path):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as browser:
        wire, auth, key = adapter_setup(app, store, browser, tmp_path)
        principal = await identity(wire, auth)
        assert principal["user_id"] == store.principal_for_fixture("alice").user_id
        original_client = await auth.adapter.get_client_info()
        original_tokens = await auth.adapter.get_tokens()
        await expire(auth)
        # Independent storage and Auth objects share a process-safe profile lock.
        restarted = SharedOAuth(URL, tmp_path, credentials(tmp_path, key), oauth_factory=consent_oauth(browser))
        await asyncio.gather(identity(wire, auth), identity(wire, restarted))
        assert sum(row[4] for row in wire.events) == 1
        tokens = await restarted.adapter.get_tokens()
        assert tokens.refresh_token != original_tokens.refresh_token
        assert (await restarted.adapter.get_client_info()).client_id == original_client.client_id
        assert all(row[1] != "/token" for row in wire.events)
        assert await auth.storage.get("refresh", collection="ppap-client-claim") is None
        # Revoked access triggers a new consent with the cached registered port.
        revoked = browser.post("/oauth/revoke", data={"client_id": original_client.client_id, "token": tokens.access_token})
        assert revoked.status_code == 200
        await identity(wire, restarted)
        assert (await restarted.adapter.get_client_info()).client_id == original_client.client_id
        assert sum(row[1] == "/oauth/register" for row in wire.events) == 1
        raw_files = b"".join(p.read_bytes() for p in (tmp_path / "credentials").rglob("*") if p.is_file())
        assert original_tokens.access_token.encode() not in raw_files
        assert tokens.refresh_token.encode() not in raw_files


@pytest.mark.asyncio
async def test_lost_refresh_response_requires_fresh_consent_without_token_replay(mcp_app, tmp_path):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as browser:
        wire, auth, key = adapter_setup(app, store, browser, tmp_path)
        await identity(wire, auth)
        first = await auth.adapter.get_client_info()
        await expire(auth)
        wire.drop_refresh = True
        with pytest.raises(Exception):
            await identity(wire, auth)
        assert await auth.storage.get("refresh", collection="ppap-client-claim") is not None
        restarted = SharedOAuth(URL, tmp_path, credentials(tmp_path, key), oauth_factory=consent_oauth(browser))
        await identity(wire, restarted)
        assert sum(row[4] for row in wire.events) == 1
        assert (await restarted.adapter.get_client_info()).client_id != first.client_id
        assert await restarted.storage.get("refresh", collection="ppap-client-claim") is None


def workbook_bytes():
    stream = io.BytesIO()
    book = Workbook()
    book.active.append(["控制计划", "客户", "零件 P-1"])
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["create_file", "PUT", "finalize_file"])
async def test_proxy_upload_resume_new_version_download_and_remote_resources(mcp_app, tmp_path, lost):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as browser:
        wire, auth, _ = adapter_setup(app, store, browser, tmp_path, approved=["identity:read", "files:read", "files:write"])
        root = tmp_path / "allowed"
        root.mkdir()
        source = root / "控制计划.xlsx"
        content = workbook_bytes()
        source.write_bytes(content)
        server = bridge(URL, auth, [root], client_factory=lambda: wire.client(auth), http_factory=wire.http)
        wire.drop_put = lost == "PUT"
        wire.drop_tool = None if lost == "PUT" else lost
        rid = str(uuid4())
        args = {"path": str(source), "request_id": rid, "prevalidate": False}
        async with Client(server) as local:
            tools = await local.list_tools()
            assert {"get_identity", "local_upload_file", "local_download_file"} <= {tool.name for tool in tools}
            first = (await local.call_tool("local_upload_file", args)).structured_content
            assert first["status"] == "unknown"
            resumed = (await local.call_tool("local_upload_file", args)).structured_content
            assert resumed["status"] == "ok", resumed
            version = resumed["file"]
            assert version["id"] == rid
            assert version["sha256"] == hashlib.sha256(content).hexdigest()
            repeated = (await local.call_tool("local_upload_file", args)).structured_content
            assert repeated["file"] == version
            source.write_bytes(b"changed")
            conflict = (await local.call_tool("local_upload_file", args)).structured_content
            assert conflict["status"] == "error"
            source.write_bytes(content)
            new_args = {**args, "request_id": str(uuid4()), "file_id": version["file_id"]}
            newer = (await local.call_tool("local_upload_file", new_args)).structured_content
            assert newer["status"] == "ok" and newer["file"]["id"] != version["id"]
            ids = {"file_id": version["file_id"], "version_id": version["id"]}
            link = (await local.call_tool("download_file", ids)).structured_content["data"]["url"]
            target = root / "download.xlsx"
            result = (await local.call_tool("local_download_file", {"transfer_url": link, "destination": str(target), "expected_sha256": version["sha256"]})).structured_content
            assert result["status"] == "ok", result
            assert target.read_bytes() == content
            uri = f"ppap://files/{version['file_id']}/versions/{version['id']}?pointer=%2Fsha256"
            resource = await local.read_resource(uri)
            assert json.loads(resource[0].text)["value"] == hashlib.sha256(content).hexdigest()
            prompts = await local.list_prompts()
            assert {p.name for p in prompts} == {"summarize_round", "compare_rounds"}


@pytest.mark.asyncio
async def test_local_upload_prevalidation_is_separate_and_grant_bound_download(mcp_app, tmp_path):
    app, store = mcp_app
    with TestClient(app, base_url="http://127.0.0.1:8080") as browser:
        wire, auth, _ = adapter_setup(app, store, browser, tmp_path)
        source = tmp_path / "input.xlsx"
        source.write_bytes(workbook_bytes())
        files = LocalFiles([tmp_path], URL, auth, lambda: wire.client(auth), http_factory=wire.http)
        result = await files.upload(str(source), uuid4())
        assert result["status"] == "ok", result
        assert result["validation"]["status"] == "ok", result
        assert result["validation"]["data"]
        version = result["file"]
        async with wire.client(auth) as client:
            link = (await client.call_tool("download_file", {"file_id": version["file_id"], "version_id": version["id"]})).structured_content["data"]["url"]
        # A different valid delegation, even for the same person, cannot borrow the URL.
        other_dir = tmp_path / "other-profile"
        other = SharedOAuth(URL, other_dir, credentials(other_dir, Fernet.generate_key()), oauth_factory=consent_oauth(browser))
        other_files = LocalFiles([tmp_path], URL, other, lambda: wire.client(other), http_factory=wire.http)
        with pytest.raises(ValueError, match="download_refused"):
            await other_files.download(link, str(tmp_path / "denied.xlsx"))
        assert not (tmp_path / "denied.xlsx").exists()


@pytest.mark.asyncio
async def test_cli_stdio_process_http_transfer_and_process_restart(mcp_app, tmp_path, monkeypatch):
    _, store = mcp_app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    base = "http://127.0.0.1:" + str(sock.getsockname()[1])
    url = base + "/mcp"
    store.settings.public_api_url = base
    app = create_app(store.settings, auth_store=store)
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    monkeypatch.setitem(globals(), "URL", url)
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        profile_base = tmp_path / "profiles"
        profile = profile_base / hashlib.sha256((url + "\ndefault").encode()).hexdigest()
        with httpx.Client(base_url=base) as browser:
            wire, auth, key = adapter_setup(app, store, browser, profile)
            await identity(wire, auth)
        root = tmp_path / "files"
        root.mkdir()
        source = root / "control.xlsx"
        source.write_bytes(workbook_bytes())
        launcher = tmp_path / "controlled_keychain.py"
        launcher.write_text(
            "import os\nfrom pathlib import Path\nimport ppap_mcp_client.__main__ as cli\n"
            "from ppap_mcp_client.auth import encrypted_store\n"
            "cli.user_data_path = lambda _: Path(os.environ['PPAP_TEST_DATA'])\n"
            "cli.secure_store = lambda directory: encrypted_store(directory, os.environ['PPAP_TEST_KEY'].encode())\n"
            "cli.main()\n"
        )
        log = tmp_path / "adapter-stderr.log"

        def process():
            return Client(
                StdioTransport(
                    sys.executable,
                    [str(launcher), "--server", url, "--directory", str(root)],
                    env={"PPAP_TEST_DATA": str(profile_base), "PPAP_TEST_KEY": key.decode()},
                    keep_alive=False,
                    log_file=log,
                ),
                timeout=30,
            )

        rid = str(uuid4())
        args = {"path": str(source), "request_id": rid, "prevalidate": False}
        async with process() as client:
            result = (await client.call_tool("local_upload_file", args)).structured_content
            assert result["status"] == "ok", result
            original = result["file"]
        before = await auth.adapter.get_tokens()
        await expire(auth)
        async with process() as restarted:
            same = (await restarted.call_tool("local_upload_file", args)).structured_content
            assert same["file"] == original
            ids = {"file_id": original["file_id"], "version_id": original["id"]}
            link = (await restarted.call_tool("download_file", ids)).structured_content["data"]["url"]
            target = root / "retrieved.xlsx"
            downloaded = (await restarted.call_tool("local_download_file", {"transfer_url": link, "destination": str(target)})).structured_content
            assert downloaded["status"] == "ok" and target.read_bytes() == source.read_bytes()
        after = await auth.adapter.get_tokens()
        assert before.refresh_token != after.refresh_token
        logs = log.read_text()
        assert all(token not in logs for token in (before.access_token, before.refresh_token, after.access_token, after.refresh_token))
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        sock.close()
        assert not thread.is_alive()
