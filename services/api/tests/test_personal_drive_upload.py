"""Real upload receipts/PostgreSQL, controlled Feishu HTTP; not a live delivery proof."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import httpx
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


def test_local_audit_file_waits_for_personal_drive_then_keeps_a_frozen_source(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="personal-drive-test"), reset=False, role="api")
    principal = store.upsert_feishu_identity("personal-drive-" + uuid.uuid4().hex, "owner")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("test-user-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1))
    finish_calls = []

    def provider(request):
        if request.url.path.endswith("root_folder/meta"):
            return httpx.Response(200, json={"code": 0, "data": {"token": "personal-root"}})
        if request.url.path.endswith("upload_prepare"):
            assert json.loads(request.content)["parent_node"] == "personal-root"
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "upload-one", "block_size": 4, "block_num": 2}})
        if request.url.path.endswith("upload_part"):
            return httpx.Response(200, json={"code": 0})
        assert request.url.path.endswith("upload_finish")
        finish_calls.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "new-personal-file"}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, tokens))
        assert client.get("/integrations/feishu/root-folder").json() == {"token": "personal-root", "name": "我的文件夹", "type": "folder"}
        request_id = str(uuid.uuid4())
        body = {
            "request_id": request_id,
            "original_filename": "审核.xlsx",
            "content_type": "application/octet-stream",
            "byte_size": 5,
            "sha256": hashlib.sha256(b"abcde").hexdigest(),
            "feishu_folder_token": "personal-root",
        }
        version = client.post("/files", json=body).json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        assert client.put(path + "/blob", content=b"abcde").json()["status"] == "ready"
        pending = client.get(f"/file-uploads/{request_id}").json()
        assert pending["state"] == "uploading" and pending["resume_allowed"]
        assert store.file_source(request_id) is None
        blocked = client.post("/file-validations", json={"request_id": str(uuid.uuid4()), "source_file_id": version["file_id"], "source_file_version_id": request_id})
        assert blocked.status_code == 409 and blocked.json()["error"] == "file_not_ready"
        assert client.post("/files", json={**body, "feishu_folder_token": "different-folder"}).status_code == 409
        ready = client.post(f"/file-uploads/{request_id}/feishu").json()
        assert ready["state"] == "ready" and ready["file"]["id"] == request_id
        assert store.file_source(request_id)["object_ref"] == "new-personal-file"
        assert client.post(f"/file-uploads/{request_id}/feishu").json() == ready
        assert client.get(f"/file-uploads/{request_id}").json() == ready
        assert len(finish_calls) == 1
        # The frozen bytes do not depend on a later cloud read or source deletion.
        assert client.get(path + "/download").content == b"abcde"


def _staged_client(auth_store_factory, provider):
    store = auth_store_factory(Settings(feishu_app_id="personal-drive-test"), reset=False, role="api")
    principal = store.upsert_feishu_identity("personal-drive-" + uuid.uuid4().hex, "owner")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("test-user-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1))
    client = TestClient(create_app(store.settings, auth_store=store, http=httpx.Client(transport=httpx.MockTransport(provider))))
    client.cookies.set(SESSION_COOKIE, store.create_session(principal, tokens))
    request_id = str(uuid.uuid4())
    version = client.post(
        "/files",
        json={
            "request_id": request_id,
            "original_filename": "same.xlsx",
            "content_type": "application/octet-stream",
            "byte_size": 5,
            "sha256": hashlib.sha256(b"abcde").hexdigest(),
            "feishu_folder_token": "chosen-folder",
        },
    ).json()
    assert client.put(f"/files/{version['file_id']}/versions/{request_id}/blob", content=b"abcde").status_code == 200
    return client, store, principal, request_id


def test_finish_timeout_does_not_publish_success_or_repeat_the_cloud_write(auth_store_factory):
    calls = []

    def provider(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if calls[-1] == "upload_prepare":
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "original-plan", "block_size": 5, "block_num": 1}})
        if calls[-1] == "upload_part":
            return httpx.Response(200, json={"code": 0})
        raise httpx.ReadTimeout("lost finish acknowledgement")

    client, store, _, request_id = _staged_client(auth_store_factory, provider)
    with client:
        path = f"/file-uploads/{request_id}"
        unknown = client.post(path + "/feishu").json()
        assert unknown["state"] == "unknown" and not unknown["resume_allowed"] and not unknown["retry_allowed"]
        assert client.get(path).json() == unknown
        assert client.post(path + "/feishu").json() == unknown
        assert calls == ["upload_prepare", "upload_part", "upload_finish"]
        assert store.file_source(request_id) is None


def test_parts_resume_original_plan_and_new_same_name_upload_has_a_new_identity(auth_store_factory):
    plans, parts, finishes = [], [], []

    def provider(request):
        if request.url.path.endswith("upload_prepare"):
            plan = "plan-" + str(len(plans))
            plans.append(plan)
            assert json.loads(request.content)["parent_node"] == "chosen-folder"
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": plan, "block_size": 5, "block_num": 1}})
        if request.url.path.endswith("upload_part"):
            parts.append(request.content)
            if len(parts) == 1:
                raise httpx.ConnectError("part interrupted")
            return httpx.Response(200, json={"code": 0})
        token = "new-file-" + str(len(finishes))
        finishes.append(token)
        return httpx.Response(200, json={"code": 0, "data": {"file_token": token}})

    client, store, _, request_id = _staged_client(auth_store_factory, provider)
    with client:
        path = f"/file-uploads/{request_id}/feishu"
        interrupted = client.post(path).json()
        assert interrupted["state"] == "uploading" and interrupted["resume_allowed"]
        assert client.post(path).json()["state"] == "ready"
        assert len(plans) == 1 and len(finishes) == 1
    other, other_store, _, other_id = _staged_client(auth_store_factory, provider)
    with other:
        assert other.post(f"/file-uploads/{other_id}/feishu").json()["state"] == "ready"
        assert other_id != request_id
        assert store.file_source(request_id)["object_ref"] != other_store.file_source(other_id)["object_ref"]


def test_overlapping_handlers_finish_once_and_receipts_remain_private(auth_store_factory):
    started, release = Event(), Event()
    finishes = []

    def provider(request):
        if request.url.path.endswith("upload_prepare"):
            started.set()
            assert release.wait(5)
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "original-plan", "block_size": 5, "block_num": 1}})
        if request.url.path.endswith("upload_part"):
            return httpx.Response(200, json={"code": 0})
        finishes.append(request)
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "only-one-file"}})

    client, store, _, request_id = _staged_client(auth_store_factory, provider)
    with client, ThreadPoolExecutor(max_workers=2) as pool:
        path = f"/file-uploads/{request_id}/feishu"
        first = pool.submit(client.post, path)
        assert started.wait(5)
        try:
            assert client.post(path).json()["state"] == "uploading"
        finally:
            release.set()
        assert first.result().json()["state"] == "ready"
        assert len(finishes) == 1
        other = store.upsert_feishu_identity("another-personal-user-" + uuid.uuid4().hex, "other")
        client.cookies.set(SESSION_COOKIE, store.create_session(other))
        assert client.get(f"/file-uploads/{request_id}").status_code == 403
        assert client.post(path).status_code == 409  # No user token; no provider call.
        assert len(finishes) == 1
