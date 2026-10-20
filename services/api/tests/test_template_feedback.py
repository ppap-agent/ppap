"""Template-feedback recovery through the real API and isolated PostgreSQL."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


@pytest.fixture
def feedback_client(auth_store_factory):
    settings = Settings(
        feishu_app_id="feedback-app",
        feishu_app_secret="test-secret",
        feishu_feedback_base_token="base_test",
        feishu_feedback_table_id="tbl_test",
        feishu_feedback_tenant_key="operator-tenant",
    )
    store = auth_store_factory(settings, reset=False)
    principal = store.upsert_feishu_identity("feedback-" + uuid.uuid4().hex, "submitter")
    store.save_app_ticket(settings.feishu_app_id, "test-ticket")
    state = SimpleNamespace(reject=True, denial=(400, {"code": 99991672, "msg": "Access denied. One of the following scopes is required"}), writes=[], records=[], uploads=[], external_paths=[])

    def provider(request):
        path = request.url.path
        state.external_paths.append(path)
        if path.endswith("app_access_token"):
            return httpx.Response(200, json={"code": 0, "app_access_token": "test-app"})
        if path.endswith("tenant_access_token"):
            assert json.loads(request.content)["tenant_key"] == "operator-tenant"
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "test-operator"})
        if path.endswith("user_info"):
            return httpx.Response(200, json={"code": 0, "data": {"tenant_key": principal.tenant_key, "open_id": principal.subject, "name": "提交人"}})
        if path.endswith("upload_prepare"):
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": "upload-a", "block_size": 4194304, "block_num": 1}})
        if path.endswith("upload_part"):
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("upload_finish"):
            state.uploads.append("box_" + str(len(state.uploads)))
            return httpx.Response(200, json={"code": 0, "data": {"file_token": state.uploads[-1]}})
        if path.endswith("/records"):
            fields = json.loads(request.content)["fields"]
            state.writes.append((str(request.url.params["client_token"]), fields))
            if state.reject:
                if state.denial is None:
                    raise httpx.ReadTimeout("receipt lost", request=request)
                if state.denial == "missing-record-id":
                    return httpx.Response(200, json={"code": 0, "data": {"record": {"fields": fields}}})
                status, payload = state.denial
                return httpx.Response(status, json=payload)
            record = {"record_id": "rec_received", "fields": fields}
            state.records.append(record)
            return httpx.Response(200, json={"code": 0, "data": {"record": record}})
        assert path.endswith("/records/search")
        operation = json.loads(request.content)["filter"]["conditions"][0]["value"][0]
        return httpx.Response(200, json={"code": 0, "data": {"items": [record for record in state.records if record["fields"]["请求编号"] == operation], "has_more": False}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, UserTokens("test-user", "", expires, expires)))
        version = client.post("/files", json={"original_filename": "template.xlsx", "content_type": "application/octet-stream", "byte_size": 8}).json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        assert client.put(path + "/blob", content=b"template").status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        state.client, state.store, state.settings, state.http, state.version = client, store, settings, http, version
        yield state


def test_missing_destination_keeps_material_and_resumes_after_configuration_is_repaired(feedback_client):
    state = feedback_client
    table_id = state.settings.feishu_feedback_table_id
    state.settings.feishu_feedback_table_id = ""
    command = {"request_id": str(uuid.uuid4()), "file_version_ids": [state.version["id"]]}
    path = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
    first = state.client.post(path, json=command).json()
    assert (first["state"], first["retry_allowed"]) == ("failed", True)
    assert first["reason"] == "模板提报服务配置暂不可用，材料未提交给运营。已上传附件仍保留，请联系支持。"
    assert first["request"] == command and not first["received_file_version_ids"]
    assert state.client.get(path).json() == first
    assert state.client.post(path, json=command).json() == first
    assert state.external_paths == []
    history = state.client.get("/audit-template-feedback").json()
    assert history["entries"][0]["files_available"]
    assert history["entries"][0]["files"][0]["id"] == state.version["id"]

    state.settings.feishu_feedback_table_id = table_id
    state.reject = False
    with TestClient(create_app(state.settings, auth_store=state.store, http=state.http)) as restored:
        restored.cookies.update(state.client.cookies)
        assert restored.get(path).json() == first
        assert state.external_paths == []
        retry = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
        received = restored.post(retry, json=command).json()
        assert received["state"] == "received" and received["request"] == command
        assert received["received_file_version_ids"] == command["file_version_ids"]
        assert restored.get(retry).json() == received
        assert restored.post(retry, json=command).json() == received
        assert not restored.get(path).json()["retry_allowed"]
    assert len(state.writes) == 1 and len(state.records) == 1 and len(state.uploads) == 1


def test_explicit_creation_denial_can_resume_with_the_original_uploaded_material(feedback_client):
    state = feedback_client
    command = {"request_id": str(uuid.uuid4()), "file_version_ids": [state.version["id"]]}
    path = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
    response = state.client.post(path, json=command)
    assert response.status_code == 200
    first = response.json()
    assert (first["state"], first["retry_allowed"]) == ("failed", True)
    assert first["request"] == command and first["received_file_version_ids"] == []
    assert len(state.writes) == 1 and not state.records
    assert state.client.get(path).json() == first
    assert state.client.post(path, json=command).json() == first
    assert len(state.writes) == 1

    state.reject = False
    with TestClient(create_app(state.settings, auth_store=state.store, http=state.http)) as restored:
        restored.cookies.update(state.client.cookies)
        assert restored.get(path).json() == first
        retry = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
        received = restored.post(retry, json=command).json()
        assert received["state"] == "received" and received["submission_id"] == "rec_received"
        assert received["received_file_version_ids"] == command["file_version_ids"]
        assert restored.get(retry).json() == received
        assert restored.post(retry, json=command).json() == received
        assert not restored.get(path).json()["retry_allowed"]
        history = restored.get("/audit-template-feedback").json()
        assert history["total"] == 1 and history["entries"][0]["receipt"] == received

    assert len(state.writes) == 2 and len(state.records) == 1
    assert state.writes[0][0] == state.writes[1][0]
    assert state.writes[0][1]["文件附件"] == state.writes[1][1]["文件附件"]
    assert len(state.uploads) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        (503, {"code": 99991672}),
        "missing-record-id",
        (400, {"code": 1254607, "msg": "Data not ready, please try again later"}),
        (403, {"code": 12345678, "msg": "Unknown failure"}),
    ],
    ids=["timeout", "server-error", "missing-record-id", "processing", "unrecognized-forbidden"],
)
def test_uncertain_creation_cannot_be_replayed_even_after_an_empty_search(feedback_client, outcome):
    state = feedback_client
    state.denial = outcome
    command = {"request_id": str(uuid.uuid4()), "file_version_ids": [state.version["id"]]}
    path = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
    first = state.client.post(path, json=command).json()
    assert (first["state"], first["retry_allowed"]) == ("unknown", False)
    assert first["request"] == command
    assert state.client.get(path).json()["state"] == "unknown"
    assert state.client.post(path, json=command).json()["state"] == "unknown"
    retry = f"/audit-template-feedback/{command['request_id']}/attempts/{uuid.uuid4()}"
    assert state.client.post(retry, json=command).status_code == 409
    assert len(state.writes) == 1 and not state.records
    assert len(state.uploads) == 1
