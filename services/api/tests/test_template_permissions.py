"""Template permission commands use current Drive authority and fixed receipts."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.db import connect
from ppap_core.feishu import UserTokens
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


@pytest.fixture
def permission_client(auth_store_factory):
    tenant = "permissions-" + uuid.uuid4().hex
    settings = Settings(feishu_app_id="template-app", feishu_template_sources=[{"tenant_key": tenant, "customer_id": "海岸线", "folder_token": "test-folder"}])
    store = auth_store_factory(settings, reset=False)
    principal = store.upsert_feishu_identity(tenant, "manager")
    state = SimpleNamespace(
        members=[
            {"member_type": "openid", "member_id": "manager", "name": "当前管理者", "perm": "full_access", "type": "user"},
            {"member_type": "openid", "member_id": "reader", "name": "协作者", "perm": "view", "type": "user"},
        ],
        changes=[],
        lose_receipt=False,
        before_members=None,
        before_put=None,
    )

    def provider(request):
        assert request.headers["authorization"] == "Bearer current-user-token"
        path = request.url.path
        if path == "/open-apis/authen/v1/user_info":
            return httpx.Response(200, json={"code": 0, "data": {"tenant_key": tenant, "open_id": "manager", "name": "当前管理者"}})
        if path == "/open-apis/drive/v1/files":
            return httpx.Response(200, json={"code": 0, "data": {"files": [], "has_more": False}})
        if path == "/open-apis/drive/explorer/v2/root_folder/meta":
            return httpx.Response(403, json={"code": 1063002})
        if path == "/open-apis/drive/v1/permissions/test-folder/members":
            assert request.method == "GET" and request.url.params["type"] == "folder"
            if state.before_members:
                state.before_members()
            return httpx.Response(200, json={"code": 0, "data": {"items": state.members}})
        if path == "/open-apis/drive/v1/permissions/test-folder/members/reader":
            assert request.method == "PUT" and request.url.params["type"] == "folder"
            assert request.url.params["need_notification"] == "false"
            command = json.loads(request.content)
            assert command["member_type"] == "openid" and command["perm"] in {"view", "edit"}
            if state.before_put:
                state.before_put()
            state.changes.append(command)
            state.members[1]["perm"] = command["perm"]
            if state.lose_receipt:
                raise httpx.ReadTimeout("receipt lost", request=request)
            return httpx.Response(200, json={"code": 0, "data": {"member": {"member_id": "reader", **command}}})
        raise AssertionError((request.method, path))

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, UserTokens("current-user-token", "", expires, expires)))
        source = client.post("/files", json={"original_filename": "rule.md", "content_type": "text/markdown", "byte_size": 4}).json()
        path = f"/files/{source['file_id']}/versions/{source['id']}"
        assert client.put(path + "/blob", content=b"rule").status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        assert (
            client.post(
                "/rules",
                json={
                    "title": "测试要求",
                    "source_file_version_id": source["id"],
                    "body": {
                        "kind": "engine",
                        "type": "控制计划",
                        "customers": ["海岸线"],
                        "rules": [{"id": "1", "category": "检查", "rule_to_check": "应填写零件号", "type_of_rule": "text", "priority": "P1"}],
                    },
                },
            ).status_code
            == 201
        )
        page = client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
        state.folder = {"customer_id": "海岸线", "folder_id": page["folder_id"]}
        state.client, state.store, state.settings, state.http, state.principal = client, store, settings, http, principal
        yield state


def test_current_non_creator_manager_gets_only_safe_collaborator_choices(permission_client):
    state = permission_client
    page = state.client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
    assert page["manage_access"] == {"state": "allowed", "reason": None}
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    assert snapshot["creator_name"] is None
    assert snapshot["manage_access"] == {"state": "allowed", "reason": None}
    assert [(action["target"], action["before"], action["after"]) for action in snapshot["actions"]] == [({"kind": "user", "id": "reader"}, "可阅读", "可编辑")]
    state.members[0]["perm"] = "view"
    revoked = state.client.get("/audit-template-permissions", params=state.folder).json()
    assert revoked["manage_access"]["state"] == "unknown" and revoked["actions"] == []


def test_permission_change_has_a_durable_original_receipt(permission_client):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}
    response = state.client.post("/audit-template-permission-changes", json=command)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["request"] == command and receipt["state"] == "applied"
    assert receipt["result_revision"] and receipt["result_revision"] != command["permission_revision"]
    assert state.client.get("/audit-template-permission-changes/" + command["request_id"]).json() == receipt
    assert state.client.post("/audit-template-permission-changes", json=command).json() == receipt
    assert len(state.changes) == 1


def test_lost_receipt_is_restored_without_replaying_or_using_current_acl(permission_client):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}
    state.lose_receipt = True
    receipt = state.client.post("/audit-template-permission-changes", json=command).json()
    assert receipt["state"] == "unknown" and receipt["result_revision"] is None
    assert state.members[1]["perm"] == "edit"
    with TestClient(create_app(state.settings, auth_store=state.store, http=state.http)) as restored:
        restored.cookies.update(state.client.cookies)
        assert restored.get("/audit-template-permission-changes/" + command["request_id"]).json() == receipt
        current = restored.get("/audit-template-permissions", params=state.folder).json()
        assert current["change"] == receipt
        assert current["manage_access"]["state"] == "unknown" and current["actions"] == []
        assert restored.post("/audit-template-permission-changes", json=command).json() == receipt
        another = {**command, "request_id": str(uuid.uuid4())}
        assert restored.post("/audit-template-permission-changes", json=another).json()["state"] == "conflict"
    assert len(state.changes) == 1


@pytest.mark.parametrize("change,expected", [("revoked", "rejected"), ("stale", "conflict"), ("forged", "rejected")])
def test_modification_rechecks_current_authority_revision_and_service_choice(permission_client, change, expected):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}
    if change == "revoked":
        state.members[0]["perm"] = "view"
    elif change == "stale":
        state.members[1]["perm"] = "edit"
    else:
        command["action"]["after"] = "可管理"
    receipt = state.client.post("/audit-template-permission-changes", json=command).json()
    assert receipt["state"] == expected and receipt["result_revision"] is None
    state.members[0]["perm"], state.members[1]["perm"] = "full_access", "view"
    assert state.client.post("/audit-template-permission-changes", json=command).json() == receipt
    assert state.changes == []


def test_original_permission_request_is_private_and_cannot_be_rebound(permission_client):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}
    assert state.client.post("/audit-template-permission-changes", json=command).json()["state"] == "applied"
    assert state.client.post("/audit-template-permission-changes", json={**command, "permission_revision": "another"}).status_code == 409
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    for tenant, subject in ((state.principal.tenant_key, "another-user"), ("another-tenant", "manager")):
        other = state.store.upsert_feishu_identity(tenant, subject)
        state.client.cookies.set(SESSION_COOKIE, state.store.create_session(other, UserTokens("current-user-token", "", expires, expires)))
        assert state.client.get("/audit-template-permission-changes/" + command["request_id"]).status_code == 403
    state.client.cookies.clear()
    assert state.client.get("/audit-template-permission-changes/" + command["request_id"]).status_code == 401
    assert len(state.changes) == 1


def test_simultaneous_commands_do_not_replay_or_overlap_the_original_change(permission_client):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}
    started, release = Event(), Event()

    def pause():
        started.set()
        assert release.wait(10)

    def submit():
        with TestClient(create_app(state.settings, auth_store=state.store, http=state.http)) as parallel:
            parallel.cookies.update(state.client.cookies)
            return parallel.post("/audit-template-permission-changes", json=command).json()

    state.before_put = pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(submit)
        try:
            assert started.wait(10)
            assert state.client.post("/audit-template-permission-changes", json=command).json()["state"] == "unknown"
            competing = {**command, "request_id": str(uuid.uuid4())}
            conflict = state.client.post("/audit-template-permission-changes", json=competing).json()
            assert conflict["state"] == "conflict"
        finally:
            release.set()
        assert original.result(timeout=10)["state"] == "applied"
    assert state.client.post("/audit-template-permission-changes", json=competing).json() == conflict
    assert len(state.changes) == 1


@pytest.mark.parametrize("cut,expected,effects", [("preparing", "rejected", 0), ("dispatching", "unknown", 1)])
def test_database_guard_loss_cannot_reacquire_dispatch_authority(permission_client, cut, expected, effects):
    state = permission_client
    snapshot = state.client.get("/audit-template-permissions", params=state.folder).json()
    command = {"request_id": str(uuid.uuid4()), "folder": state.folder, "permission_revision": snapshot["permission_revision"], "action": snapshot["actions"][0]}

    def disconnect():
        state.before_members, state.before_put = None, None
        # Fault injection at the external database seam, not a store mock or a
        # state assertion. Only this request's lock in the owned test DB is hit.
        with connect(state.settings, "migrator") as conn:
            killed = conn.execute(
                """SELECT pg_terminate_backend(pid) FROM pg_locks
                WHERE locktype = 'advisory' AND granted AND objsubid = 1
                AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
                AND classid = ((hashtextextended(%s, 0) >> 32) & 4294967295)::oid
                AND objid = (hashtextextended(%s, 0) & 4294967295)::oid""",
                ("template-permission:" + command["request_id"], "template-permission:" + command["request_id"]),
            ).fetchall()
            assert killed == [(True,)]

    if cut == "preparing":
        state.before_members = disconnect
    else:
        state.before_put = disconnect
    with TestClient(create_app(state.settings, auth_store=state.store, http=state.http), raise_server_exceptions=False) as interrupted:
        interrupted.cookies.update(state.client.cookies)
        assert interrupted.post("/audit-template-permission-changes", json=command).status_code == 500
        receipt = interrupted.get("/audit-template-permission-changes/" + command["request_id"]).json()
        assert receipt["state"] == expected and receipt["result_revision"] is None
        assert interrupted.post("/audit-template-permission-changes", json=command).json() == receipt
    assert len(state.changes) == effects
