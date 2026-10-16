"""Request-bound Drive imports use real PostgreSQL and mocked provider HTTP."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.files import ObjectExists, ObjectMissing
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


def test_drive_import_can_read_back_one_frozen_version_after_a_lost_reply(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="drive-receipt-test"), reset=False, role="api")
    principal = store.upsert_feishu_identity("drive-receipt-" + uuid.uuid4().hex, "owner")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("test-user-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1))
    content = [b"first frozen bytes"]
    downloads = []

    def provider(request):
        if request.url.path.endswith("/metas/batch_query"):
            token = json.loads(request.content)["request_docs"][0]["doc_token"]
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": token, "doc_type": "file", "title": "source.xlsx"}]}})
        downloads.append(str(request.url))
        return httpx.Response(200, content=content[0], headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, tokens))
        request_id = str(uuid.uuid4())
        command = {"request_id": request_id, "file_token": "source-token", "doc_type": "file"}
        response = client.post("/integrations/feishu/files/import", json=command)
        assert response.status_code == 201
        # The caller may lose this response. Only the original request is read back.
        receipt = client.get(f"/file-uploads/{request_id}")
        assert receipt.status_code == 200
        ready = receipt.json()
        assert ready["state"] == "ready"
        assert ready["file"]["id"] == request_id
        assert ready["file"]["status"] == "ready"
        content[0] = b"remote content changed after the original import"
        duplicate = client.post("/integrations/feishu/files/import", json=command)
        assert duplicate.status_code == 201
        assert duplicate.json()["id"] == request_id
        assert len(downloads) == 1
        file = ready["file"]
        path = f"/files/{file['file_id']}/versions/{file['id']}"
        assert client.get(path + "/download").content == b"first frozen bytes"
        assert client.post("/integrations/feishu/files/import", json={**command, "file_token": "another-source"}).status_code == 409
        assert len(downloads) == 1
        local_id = str(uuid.uuid4())
        local = client.post("/files", json={"request_id": local_id, "original_filename": "local.txt", "content_type": "text/plain", "byte_size": 3, "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}).json()
        assert client.put(f"/files/{local['file_id']}/versions/{local_id}/blob", content=b"abc").json()["status"] == "ready"
        assert client.get(f"/file-uploads/{local_id}").json()["state"] == "ready"
        assert client.post("/integrations/feishu/files/import", json={**command, "request_id": local_id}).status_code == 409
        assert len(downloads) == 1
        peer = store.upsert_feishu_identity(principal.tenant_key, "peer")
        client.cookies.set(SESSION_COOKIE, store.create_session(peer, tokens))
        assert client.get(f"/file-uploads/{request_id}").status_code == 403
        assert client.post("/integrations/feishu/files/import", json=command).status_code == 403
        assert len(downloads) == 1
        foreign = store.upsert_feishu_identity("another-drive-tenant-" + uuid.uuid4().hex, "owner")
        client.cookies.set(SESSION_COOKIE, store.create_session(foreign, tokens))
        assert client.get(f"/file-uploads/{request_id}").status_code == 403
        assert client.post("/integrations/feishu/files/import", json=command).status_code == 403
        assert len(downloads) == 1


def test_overlapping_imports_observe_one_request_before_provider_completion(auth_store_factory):
    store = auth_store_factory(Settings(feishu_app_id="drive-receipt-test"), reset=False, role="api")
    principal = store.upsert_feishu_identity("drive-overlap-" + uuid.uuid4().hex, "owner")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("test-user-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1))
    entered, release = Event(), Event()
    downloads = []

    def provider(request):
        if request.url.path.endswith("/metas/batch_query"):
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": "source-token", "doc_type": "file", "title": "source.xlsx"}]}})
        downloads.append(str(request.url))
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, content=b"frozen bytes", headers={"Content-Type": "application/octet-stream"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, tokens))
        request_id = str(uuid.uuid4())
        command = {"request_id": request_id, "file_token": "source-token", "doc_type": "file"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(client.post, "/integrations/feishu/files/import", json=command)
            try:
                assert entered.wait(10)
                pending = client.get(f"/file-uploads/{request_id}").json()
                assert pending["state"] == "uploading"
                assert pending["file"] is None
                repeated = client.post("/integrations/feishu/files/import", json=command)
                assert repeated.status_code == 201
                assert repeated.json()["id"] == request_id
                assert repeated.json()["status"] == "staging"
                assert len(downloads) == 1
                assert client.post("/integrations/feishu/files/import", json={**command, "doc_type": "docx"}).status_code == 409
                assert len(downloads) == 1
            finally:
                release.set()
            assert original.result(timeout=10).status_code == 201
        assert client.get(f"/file-uploads/{request_id}").json()["state"] == "ready"


@pytest.mark.parametrize("effect_before_failure", [False, True])
def test_import_resume_cannot_replace_bytes_captured_before_an_object_failure(auth_store_factory, effect_before_failure):
    class FaultObjects:
        def __init__(self):
            self.data = {}
            self.once = True

        def put_exclusive(self, key, body, content_type):
            if key in self.data:
                raise ObjectExists()
            if not self.once or effect_before_failure:
                self.data[key] = body
            if self.once:
                self.once = False
                raise OSError("lost object-store completion")

        def get(self, key):
            if key not in self.data:
                raise ObjectMissing()
            return self.data[key]

    store = auth_store_factory(Settings(feishu_app_id="drive-receipt-test"), objects=FaultObjects(), reset=False, role="api")
    principal = store.upsert_feishu_identity("drive-effect-" + uuid.uuid4().hex, "owner")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("test-user-access", "test-refresh", now + timedelta(hours=1), now + timedelta(days=1))
    content = [b"original captured bytes"]
    downloads = []

    def provider(request):
        if request.url.path.endswith("/metas/batch_query"):
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": "source-token", "doc_type": "file", "title": "source.xlsx"}]}})
        downloads.append(str(request.url))
        return httpx.Response(200, content=content[0], headers={"Content-Type": "application/octet-stream"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(store.settings, auth_store=store, http=http), raise_server_exceptions=False) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, tokens))
        request_id = str(uuid.uuid4())
        command = {"request_id": request_id, "file_token": "source-token", "doc_type": "file"}
        assert client.post("/integrations/feishu/files/import", json=command).status_code == 500
        receipt = client.get(f"/file-uploads/{request_id}").json()
        assert receipt["state"] == "uploading"
        assert receipt["resume_allowed"] is True
        assert receipt["file"]["id"] == request_id
        content[0] = b"changed source bytes"
        resumed = client.post("/integrations/feishu/files/import", json=command)
        receipt = client.get(f"/file-uploads/{request_id}").json()
        if effect_before_failure:
            assert resumed.status_code == 201
            assert receipt["state"] == "ready"
            assert len(downloads) == 1
            file = receipt["file"]
            assert client.get(f"/files/{file['file_id']}/versions/{request_id}/download").content == b"original captured bytes"
        else:
            assert resumed.status_code == 409
            assert receipt["state"] == "failed"
            assert receipt["resume_allowed"] is False
            assert len(downloads) == 2
        assert client.get("/audit-tasks").json()["tasks"] == []
        assert client.get("/rounds").json()["rounds"] == []
