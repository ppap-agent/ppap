"""Frozen template sharing through API/PostgreSQL; Feishu is the HTTP seam."""

import io
import json
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


@pytest.fixture
def template_share(auth_store_factory):
    tenant = "template-share-" + uuid.uuid4().hex
    settings = Settings(feishu_app_id="template-app", feishu_app_secret="test-secret", feishu_template_sources=[{"tenant_key": tenant, "customer_id": "海岸线", "folder_token": "test-folder"}])
    store = auth_store_factory(settings, reset=False)
    sender = store.upsert_feishu_identity(tenant, "sender")
    reader = store.upsert_feishu_identity(tenant, "reader")
    store.save_app_ticket(settings.feishu_app_id, "test-ticket")
    state = {
        "active": sender,
        "rights": {},
        "sent": [],
        "uploads": [],
        "mode": {},
        "files": [
            {"token": "template-one", "name": "控制计划.xlsx", "type": "file", "modified_time": "1750000000"},
            {"token": "template-two", "name": "流程图.xlsx", "type": "file", "modified_time": "1750000001"},
        ],
        "content": {"template-one": b"first-original-template", "template-two": b"second-original-template"},
    }

    def provider(request):
        path = request.url.path
        if path.endswith("app_access_token"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "test-app"})
        if path.endswith("tenant_access_token"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-tenant"})
        if path.endswith("user_info"):
            return httpx.Response(200, json={"code": 0, "data": {"open_id": state["active"].subject, "tenant_key": state["active"].tenant_key, "name": "测试用户"}})
        if path.endswith("/members/auth"):
            token = path.split("/")[-3]
            value = state["rights"].get((state["active"].subject, token, request.url.params["action"]), True)
            return httpx.Response(200, json={"code": 0, "data": {} if value is None else {"auth_result": value}})
        if path.endswith("/drive/v1/files"):
            assert request.url.params["folder_token"] == "test-folder"
            return httpx.Response(200, json={"code": 0, "data": {"files": state["files"], "has_more": False}})
        if path.endswith("/metas/batch_query"):
            token = json.loads(request.content)["request_docs"][0]["doc_token"]
            row = next(row for row in state["files"] if row["token"] == token)
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": token, "doc_type": row["type"], "title": row["name"], "size": len(state["content"][token])}]}})
        if path.endswith("/download"):
            return httpx.Response(200, content=state["content"][path.split("/")[-2]])
        if "/contact/v3/users/" in path:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "user": {
                            "open_id": path.rsplit("/", 1)[-1],
                            "name": "组织成员",
                            "status": {"is_activated": True, "is_resigned": False, "is_frozen": False, "is_exited": False, "is_unjoin": False},
                        }
                    },
                },
            )
        if path.endswith("/im/v1/files"):
            state["uploads"].append(request.content)
            if state.get("after_upload"):
                state["after_upload"]()
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "test-upload"}})
        if path.endswith("/im/v1/messages"):
            body = json.loads(request.content)
            state["sent"].append(body)
            if state.get("before_send_reply"):
                state["before_send_reply"]()
            mode = state["mode"].get(body["receive_id"])
            if mode == "unknown":
                raise httpx.ReadTimeout("reply lost")
            if mode == "failed":
                return httpx.Response(400, json={"code": 230013})
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_" + str(len(state["sent"]))}})
        return httpx.Response(403, json={"code": 1063002})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)

        def login(principal):
            state["active"] = principal
            client.cookies.set(SESSION_COOKIE, store.create_session(principal, UserTokens("test-user", "", expires, expires)))

        login(sender)
        content = b"rule"
        source = client.post("/files", json={"original_filename": "rule.md", "content_type": "text/markdown", "byte_size": len(content)}).json()
        path = f"/files/{source['file_id']}/versions/{source['id']}"
        assert client.put(path + "/blob", content=content).status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        assert (
            client.post(
                "/rules",
                json={
                    "title": "模板客户要求",
                    "source_file_version_id": source["id"],
                    "body": {
                        "kind": "engine",
                        "type": "控制计划",
                        "customers": ["海岸线"],
                        "rules": [{"id": "1", "category": "检查", "rule_to_check": "填写零件号", "type_of_rule": "text", "priority": None}],
                    },
                },
            ).status_code
            == 201
        )
        page = client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
        folder = {"customer_id": "海岸线", **{key: page[key] for key in ("folder_id", "folder_revision", "folder_template_count")}}
        yield client, store, state, folder, login, sender, reader


def test_template_share_scope_checks_each_original_and_copy_permission(template_share):
    client, _store, state, folder, _login, _sender, _reader = template_share
    response = client.get("/audit-template-share-scope", params=folder)
    assert response.status_code == 200
    scope = response.json()
    assert scope["folder_revision"] == folder["folder_revision"]
    assert len(scope["templates"]) == 2
    assert scope["link_access"]["state"] == scope["zip_access"]["state"] == "allowed"
    state["rights"][("sender", "template-two", "export")] = False
    scope = client.get("/audit-template-share-scope", params=folder).json()
    assert scope["link_access"]["state"] == "allowed"
    assert scope["zip_access"]["state"] == "denied"
    state["rights"][("sender", "template-one", "share")] = None
    scope = client.get("/audit-template-share-scope", params=folder).json()
    assert scope["link_access"]["state"] == "unknown"
    assert state["sent"] == state["uploads"] == []


def test_template_link_freezes_versions_and_preserves_recipient_drive_authority(template_share):
    client, _store, state, folder, login, sender, reader = template_share
    scope = client.get("/audit-template-share-scope", params=folder).json()
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")},
        "targets": [{"kind": "user", "id": reader.subject}],
        "content": {"kind": "link", "package": None},
    }
    command["scope"]["templates"] = [item["reference"] for item in scope["templates"]]
    sent = client.post("/audit-template-shares", json=command)
    assert sent.status_code == 200
    assert sent.json()["deliveries"][0]["state"] == "sent"
    assert client.post("/audit-template-shares", json=command).json() == sent.json()
    assert len(state["sent"]) == 1
    path = "/audit-template-shares/" + command["request_id"]
    assert client.get(path).json() == sent.json()
    login(reader)
    assert client.get(path).status_code == 403
    shared = client.get(path + "/templates")
    assert shared.status_code == 200
    assert [item["reference"] for item in shared.json()["templates"]] == command["scope"]["templates"]
    item = shared.json()["templates"][0]
    binary = path + "/files/" + item["reference"]["template_id"] + "/versions/" + item["file"]["id"]
    original = client.get(binary).content
    state["content"]["template-two"] = b"new-provider-version"
    state["files"][1]["modified_time"] = "1750000010"
    assert client.get(binary).content == original
    assert client.get(f"/files/{item['file']['file_id']}/versions/{item['file']['id']}/download").status_code == 403
    state["rights"][("reader", "template-two", "view")] = False
    assert client.get(path + "/templates").status_code == 403
    assert client.get(binary).status_code == 403
    assert len(state["sent"]) == 1


def test_template_share_only_retries_definite_failed_recipient_not_unknown_or_sent(template_share):
    client, _store, state, folder, _login, sender, reader = template_share
    scope = client.get("/audit-template-share-scope", params=folder).json()
    state["mode"].update({reader.subject: "failed", "uncertain": "unknown"})
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {**{key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")}, "templates": [item["reference"] for item in scope["templates"]]},
        "targets": [{"kind": "user", "id": target} for target in (reader.subject, "uncertain", "success")],
        "content": {"kind": "link", "package": None},
    }
    response = client.post("/audit-template-shares", json=command)
    assert response.status_code == 200
    assert [delivery["state"] for delivery in response.json()["deliveries"]] == ["failed", "unknown", "sent"]
    path = "/audit-template-shares/" + command["request_id"]
    assert client.get(path).json() == response.json()
    assert client.post("/audit-template-shares", json=command).json() == response.json()
    assert len(state["sent"]) == 3
    state["mode"][reader.subject] = "success"
    retry = {"target": command["targets"][0], "attempt_id": str(uuid.uuid4())}
    recovered = client.post(path + "/retries", json=retry)
    assert recovered.status_code == 200
    assert [delivery["state"] for delivery in recovered.json()["deliveries"]] == ["sent", "unknown", "sent"]
    assert client.post(path + "/retries", json=retry).json() == recovered.json()
    assert len(state["sent"]) == 4
    for target in command["targets"][1:]:
        assert client.post(path + "/retries", json={"target": target, "attempt_id": str(uuid.uuid4())}).status_code == 409
    assert len(state["sent"]) == 4


def test_shared_template_download_uses_the_readers_own_per_file_export_permission(template_share):
    client, _store, state, folder, login, sender, reader = template_share
    scope = client.get("/audit-template-share-scope", params=folder).json()
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {**{key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")}, "templates": [item["reference"] for item in scope["templates"]]},
        "targets": [{"kind": "user", "id": reader.subject}],
        "content": {"kind": "link", "package": None},
    }
    assert client.post("/audit-template-shares", json=command).status_code == 200
    login(reader)
    state["rights"][(reader.subject, "template-two", "export")] = False
    path = "/audit-template-shares/" + command["request_id"]
    response = client.get(path + "/templates")
    assert response.status_code == 200
    for item in response.json()["templates"]:
        binary = path + "/files/" + item["reference"]["template_id"] + "/versions/" + item["file"]["id"]
        assert client.get(binary, params={"purpose": "preview"}).status_code == 200
        allowed = item["file"]["original_filename"] == "控制计划.xlsx"
        assert item["download_access"]["state"] == ("allowed" if allowed else "denied")
        assert client.get(binary, params={"purpose": "download"}).status_code == (200 if allowed else 403)


def test_template_zip_sends_complete_frozen_bytes_and_rejects_a_changed_folder(template_share):
    client, _store, state, folder, _login, sender, reader = template_share
    state["files"][1]["name"] = state["files"][0]["name"]
    page = client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
    folder = {"customer_id": "海岸线", **{key: page[key] for key in ("folder_id", "folder_revision", "folder_template_count")}}
    scope = client.get("/audit-template-share-scope", params=folder).json()
    package = client.get("/audit-template-packages", params=folder).json()
    assert len({entry["filename"] for entry in package["templates"]}) == 2
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {**{key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")}, "templates": [item["reference"] for item in scope["templates"]]},
        "targets": [{"kind": "user", "id": reader.subject}],
        "content": {"kind": "zip", "package": package},
    }
    result = client.post("/audit-template-shares", json=command)
    assert result.status_code == 200
    assert result.json()["deliveries"][0]["state"] == "sent"
    assert len(state["uploads"]) == 1
    body = client.get("/audit-template-packages/download", params=folder).content
    assert body in state["uploads"][0]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert sorted(archive.read(name) for name in archive.namelist()) == sorted(state["content"].values())
    state["files"][0]["modified_time"] = "1750000012"
    assert client.post("/audit-template-shares", json={**command, "request_id": str(uuid.uuid4())}).status_code == 409
    assert client.post("/audit-template-shares", json=command).json() == result.json()
    assert len(state["uploads"]) == len(state["sent"]) == 1


def test_revocation_after_private_upload_prevents_delivery_and_retry_rechecks_authority(template_share):
    client, _store, state, folder, _login, sender, reader = template_share
    scope = client.get("/audit-template-share-scope", params=folder).json()
    package = client.get("/audit-template-packages", params=folder).json()
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {**{key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")}, "templates": [item["reference"] for item in scope["templates"]]},
        "targets": [{"kind": "user", "id": reader.subject}],
        "content": {"kind": "zip", "package": package},
    }
    state["after_upload"] = lambda: state["rights"].update({(sender.subject, "template-two", "share"): False})
    result = client.post("/audit-template-shares", json=command)
    assert result.status_code == 200
    assert result.json()["deliveries"][0]["state"] == "failed"
    assert len(state["uploads"]) == 1 and not state["sent"]
    state["rights"].clear()
    retry = client.post("/audit-template-shares/" + command["request_id"] + "/retries", json={"target": command["targets"][0], "attempt_id": str(uuid.uuid4())})
    assert retry.status_code == 200
    assert retry.json()["deliveries"][0]["state"] == "sent"
    assert len(state["uploads"]) == len(state["sent"]) == 1


def test_concurrent_read_and_repeated_submit_never_reclaim_an_active_dispatch(template_share):
    client, _store, state, folder, _login, sender, reader = template_share
    scope = client.get("/audit-template-share-scope", params=folder).json()
    command = {
        "request_id": str(uuid.uuid4()),
        "organization_id": sender.tenant_key,
        "scope": {**{key: scope[key] for key in ("customer_id", "folder_id", "folder_revision")}, "templates": [item["reference"] for item in scope["templates"]]},
        "targets": [{"kind": "user", "id": reader.subject}],
        "content": {"kind": "link", "package": None},
    }
    entered, release = threading.Event(), threading.Event()

    def delayed_reply():
        entered.set()
        assert release.wait(10)

    state["before_send_reply"] = delayed_reply
    path = "/audit-template-shares/" + command["request_id"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, "/audit-template-shares", json=command)
        try:
            assert entered.wait(10)
            assert client.get(path).json()["deliveries"][0]["state"] == "unknown"
            assert client.post("/audit-template-shares", json=command).json()["deliveries"][0]["state"] == "unknown"
            assert client.post(path + "/retries", json={"target": command["targets"][0], "attempt_id": str(uuid.uuid4())}).status_code == 409
            assert len(state["sent"]) == 1
        finally:
            release.set()
        assert future.result().json()["deliveries"][0]["state"] == "sent"
    assert client.get(path).json()["deliveries"][0]["state"] == "sent"
    assert len(state["sent"]) == 1
