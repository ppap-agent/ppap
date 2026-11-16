"""Meego plugin host: binding confirmation, Bearer host sessions, attachment import and writeback readback."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from api import create_app
from fastapi.testclient import TestClient
from product.feishu import UserTokens
from product.identity import SESSION_COOKIE, Resource
from product.meego import MeegoClient
from product.settings import Settings

PLUGIN = "MII_TEST"


@pytest.fixture(autouse=True)
def _fresh_plugin_tokens():
    MeegoClient._tokens.clear()
    yield
    MeegoClient._tokens.clear()


def settings_for(feishu_tenant: str, meego_tenant: str, **extra) -> Settings:
    return Settings(
        feishu_app_id="meego-test-app",
        feishu_app_secret="meego-test-secret",
        meego_plugin_id=PLUGIN,
        meego_plugin_secret="plugin-secret",
        meego_tenant_map={meego_tenant: feishu_tenant},
        **extra,
    )


# Real Meego attachment uids are ~150 characters of base64url with padding.
FILE_UID = ("bCJAK4e1-x_Q=" * 12)[:152]


class FakeMeego:
    """Only the Meego OpenAPI boundary is fake; PostgreSQL and the Product API are real."""

    def __init__(self):
        self.tenant = "saas_" + uuid.uuid4().hex
        self.user_key = str(uuid.uuid4().int)[:19]
        self.comments: list[str] = []
        self.downloads = 0
        self.fail_comment_unknown = False
        self.files = [{"uid": FILE_UID, "name": "PFMEA.xlsx", "size": "12KB", "type": "application/vnd.ms-excel"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else {}
        if path == "/open_api/authen/plugin_token":
            return httpx.Response(200, json={"data": {"token": "p-token", "expire_time": 7200}, "error": {"code": 0}})
        if path == "/open_api/authen/user_plugin_token":
            if body.get("code") != "good-code":
                return httpx.Response(200, json={"data": {}, "error": {"code": 20006, "msg": "bad code"}})
            return httpx.Response(200, json={"data": {"token": "u-token", "user_key": self.user_key, "saas_tenant_key": self.tenant}, "error": {"code": 0}})
        assert request.headers.get("X-Plugin-Token") == "p-token"
        if path == "/open_api/user/query":
            return httpx.Response(200, json={"err_code": 0, "data": [{"user_key": self.user_key, "name_cn": "飞书项目用户", "status": "activated", "out_id": "on_x"}]})
        if path.endswith("/work_item/story/query"):
            assert request.headers.get("X-User-Key") == self.user_key
            field = {"field_key": "field_files", "field_type_key": "multi_file", "field_value": self.files}
            return httpx.Response(200, json={"err_code": 0, "data": [{"id": 42, "name": "PPAP 交付", "fields": [field]}]})
        if path.endswith("/file/download"):
            self.downloads += 1
            return httpx.Response(200, content=b"workbook bytes", headers={"Content-Type": "application/octet-stream"})
        if path == "/open_api/comment/query":
            rich = [{"status": "NORMAL", "content": {"rich_text": {"doc_text": text}}} for text in self.comments]
            return httpx.Response(200, json={"err_code": 0, "data": {"comments": rich, "next_cursor": ""}})
        if path == "/open_api/comment/create":
            assert body["object"] == {"type": "WORKITEM", "work_item_type_key": "story", "work_item_id": "42"}
            text = "".join(run["text"] for paragraph in body["content"]["rich_text"] for run in paragraph["content"])
            self.comments.append(text)
            if self.fail_comment_unknown:
                raise httpx.ReadTimeout("lost reply", request=request)
            return httpx.Response(200, json={"err_code": 0, "data": {"comment_id": "c1"}})
        return httpx.Response(404, json={"err_code": 404})


def feishu_login(store, tenant_key: str, name: str = "owner"):
    principal = store.upsert_feishu_identity(tenant_key, name + "-" + uuid.uuid4().hex[:6], name)
    now = datetime.now(timezone.utc)
    tokens = UserTokens("user-access-" + name, "user-refresh-" + name, now + timedelta(hours=1), now + timedelta(days=1))
    return principal, store.create_session(principal, tokens)


def connect(client: TestClient, browser: str) -> str:
    started = client.post("/integrations/meego/session", json={"code": "good-code"})
    assert started.status_code == 200
    assert started.json()["state"] == "connect_required"
    path = started.json()["connect_url"].split("://", 1)[1].split("/", 1)[1]
    client.cookies.set(SESSION_COOKIE, browser)
    page = client.get("/" + path)
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    ticket = re.search(r'name="ticket" value="([^"]+)"', page.text).group(1)
    done = client.post("/integrations/meego/connect", data={"ticket": ticket, "csrf": csrf, "decision": "approve"})
    assert done.status_code == 200 and "连接成功" in done.text
    client.cookies.clear()
    session = client.post("/integrations/meego/session", json={"code": "good-code"})
    assert session.json()["state"] == "connected"
    return session.json()["token"]


def test_binding_requires_browser_confirmation_and_issues_bearer_only_sessions(auth_store_factory):
    tenant = "meego-feishu-" + uuid.uuid4().hex
    fake = FakeMeego()
    store = auth_store_factory(settings_for(tenant, fake.tenant), reset=False, role="api")
    principal, browser = feishu_login(store, tenant)
    with httpx.Client(transport=httpx.MockTransport(fake)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        assert client.post("/integrations/meego/session", json={"code": "bad-code"}).status_code == 401
        token = connect(client, browser)
        bearer = {"Authorization": "Bearer " + token}
        me = client.get("/integrations/meego/me", headers=bearer)
        assert me.status_code == 200
        assert me.json()["display_name"] == principal.display_name
        # The ordinary Product API accepts the host session as the same person.
        assert client.get("/audit-tasks", headers=bearer).status_code == 200
        # A host token is never a browser cookie.
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/audit-tasks").status_code == 401
        client.cookies.clear()
        # Disconnecting revokes every host session of that binding.
        assert client.delete("/integrations/meego/connection", headers=bearer).status_code == 200
        assert client.get("/integrations/meego/me", headers=bearer).status_code == 401
        assert client.post("/integrations/meego/session", json={"code": "good-code"}).json()["state"] == "connect_required"


def test_connect_page_rejects_other_tenant_reused_ticket_and_forged_confirmation(auth_store_factory):
    tenant = "meego-feishu-" + uuid.uuid4().hex
    fake = FakeMeego()
    store = auth_store_factory(settings_for(tenant, fake.tenant), reset=False, role="api")
    _, outsider = feishu_login(store, "other-tenant-" + uuid.uuid4().hex, "outsider")
    _, owner = feishu_login(store, tenant)
    with httpx.Client(transport=httpx.MockTransport(fake)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        started = client.post("/integrations/meego/session", json={"code": "good-code"}).json()
        path = "/" + started["connect_url"].split("://", 1)[1].split("/", 1)[1]
        unsigned = client.get(path, follow_redirects=False)
        assert unsigned.status_code == 302 and "/authen/v1/authorize" in unsigned.headers["location"]
        client.cookies.set(SESSION_COOKIE, outsider)
        assert "不属于" in client.get(path).text
        client.cookies.set(SESSION_COOKIE, owner)
        page = client.get(path)
        ticket = re.search(r'name="ticket" value="([^"]+)"', page.text).group(1)
        forged = client.post("/integrations/meego/connect", data={"ticket": ticket, "csrf": "forged", "decision": "approve"})
        assert forged.status_code == 400
        csrf = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)
        assert client.post("/integrations/meego/connect", data={"ticket": ticket, "csrf": csrf, "decision": "approve"}).status_code == 200
        assert "用过" in client.get(path).text


def test_meego_routes_are_off_without_configuration(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="meego-off"), reset=False, role="api")
    principal, browser = feishu_login(store, "meego-off-" + uuid.uuid4().hex)
    with httpx.Client(transport=httpx.MockTransport(FakeMeego())) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        assert client.post("/integrations/meego/session", json={"code": "good-code"}).json()["error"] == "meego_disabled"
        assert client.get("/audit-tasks", headers={"Authorization": "Bearer anything"}).status_code == 401
        client.cookies.set(SESSION_COOKIE, browser)
        assert client.get("/audit-tasks", headers={"Authorization": "Bearer ignored"}).status_code == 200


def test_attachment_import_binds_source_before_download_and_replays_one_version(auth_store_factory):
    tenant = "meego-feishu-" + uuid.uuid4().hex
    fake = FakeMeego()
    store = auth_store_factory(settings_for(tenant, fake.tenant), reset=False, role="api")
    _, browser = feishu_login(store, tenant)
    with httpx.Client(transport=httpx.MockTransport(fake)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        bearer = {"Authorization": "Bearer " + connect(client, browser)}
        listed = client.get("/integrations/meego/work-items/proj_1/story/42/files", headers=bearer)
        assert listed.status_code == 200
        assert listed.json()["files"][0]["name"] == "PFMEA.xlsx"
        request_id = str(uuid.uuid4())
        command = {"request_id": request_id, "project_key": "proj_1", "work_item_type_key": "story", "work_item_id": "42", "uid": FILE_UID}
        first = client.post("/integrations/meego/files/import", json=command, headers=bearer)
        assert first.status_code == 201, first.text
        assert first.json()["id"] == request_id
        again = client.post("/integrations/meego/files/import", json=command, headers=bearer)
        assert again.status_code == 201 and again.json()["id"] == request_id
        assert fake.downloads == 1
        other = client.post("/integrations/meego/files/import", json={**command, "uid": "file-uid-2"}, headers=bearer)
        assert other.status_code == 404
        fake.files.append({"uid": "file-uid-2", "name": "CP.xlsx", "size": "1KB", "type": ""})
        conflict = client.post("/integrations/meego/files/import", json={**command, "uid": "file-uid-2"}, headers=bearer)
        assert conflict.status_code == 409
        assert client.post("/integrations/meego/files/import", json={**command, "work_item_id": "../x"}, headers=bearer).status_code == 400
        assert client.put(f"/integrations/meego/tasks/{uuid.uuid4()}/work-item", json=command, headers=bearer).status_code == 403


def test_writeback_reads_back_before_writing_and_never_rewrites_after_unknown(auth_store_factory, monkeypatch):
    tenant = "meego-feishu-" + uuid.uuid4().hex
    fake = FakeMeego()
    store = auth_store_factory(settings_for(tenant, fake.tenant), reset=False, role="api")
    principal, browser = feishu_login(store, tenant)
    round_id = str(uuid.uuid4())
    monkeypatch.setattr(store, "resource", lambda rid: Resource(rid, principal.tenant_id, "audit_round"))
    monkeypatch.setattr(store, "grants", lambda rid, uid: {"read"})
    monkeypatch.setattr(store, "get_round", lambda rid: {"id": rid, "source_draft_id": "task-1"})
    monkeypatch.setattr(store, "round_writeback", lambda rid: {"provider": "meego", "object_kind": "work_item", "object_ref": "proj_1/story/42"})
    monkeypatch.setattr("api.meego_routes.summary", lambda body, web: ["PPAP 审核结果（PPAP 审核轮次 " + body["id"] + "）", "总体：存在问题"])
    with httpx.Client(transport=httpx.MockTransport(fake)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        bearer = {"Authorization": "Bearer " + connect(client, browser)}
        written = client.post(f"/integrations/meego/rounds/{round_id}/writeback", headers=bearer)
        assert written.status_code == 200 and written.json()["state"] == "written"
        assert "enter-current" in written.json()["link"]
        repeat = client.post(f"/integrations/meego/rounds/{round_id}/writeback", headers=bearer)
        assert repeat.json()["state"] == "already_written"
        assert len(fake.comments) == 1
        # A lost reply after the comment landed is resolved by reading back once.
        fake.fail_comment_unknown = True
        other_round = str(uuid.uuid4())
        landed = client.post(f"/integrations/meego/rounds/{other_round}/writeback", headers=bearer)
        assert landed.json()["state"] == "written"
        assert len(fake.comments) == 2
        # Unknown with nothing to read back stays unknown; it is not retried.

        def lost(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/open_api/comment/create":
                raise httpx.ReadTimeout("lost before landing", request=request)
            return fake(request)

        with httpx.Client(transport=httpx.MockTransport(lost)) as lost_http, TestClient(create_app(store.settings, auth_store=store, http=lost_http)) as lost_client:
            unknown = lost_client.post(f"/integrations/meego/rounds/{uuid.uuid4()}/writeback", headers=bearer)
            assert unknown.status_code == 502 and unknown.json()["error"] == "meego_writeback_unknown"
        assert len(fake.comments) == 2


def test_writeback_refuses_feishu_or_missing_targets(auth_store_factory, monkeypatch):
    tenant = "meego-feishu-" + uuid.uuid4().hex
    fake = FakeMeego()
    store = auth_store_factory(settings_for(tenant, fake.tenant), reset=False, role="api")
    principal, browser = feishu_login(store, tenant)
    monkeypatch.setattr(store, "resource", lambda rid: Resource(rid, principal.tenant_id, "audit_round"))
    monkeypatch.setattr(store, "grants", lambda rid, uid: {"read"})
    monkeypatch.setattr(store, "get_round", lambda rid: {"id": rid, "execution": "running"})
    monkeypatch.setattr(store, "round_writeback", lambda rid: {"provider": "feishu", "object_kind": "docx", "object_ref": "doc-token"})
    with httpx.Client(transport=httpx.MockTransport(fake)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        bearer = {"Authorization": "Bearer " + connect(client, browser)}
        assert client.post(f"/integrations/meego/rounds/{uuid.uuid4()}/writeback", headers=bearer).json()["error"] == "writeback_target_missing"
        monkeypatch.setattr(store, "round_writeback", lambda rid: {"provider": "meego", "object_kind": "work_item", "object_ref": "proj_1/story/42"})
        assert client.post(f"/integrations/meego/rounds/{uuid.uuid4()}/writeback", headers=bearer).json()["error"] == "round_incomplete"
