"""Customer templates keep frozen bytes without granting permanent file access."""

import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from ppap_api import create_app
from ppap_core.feishu import UserTokens
from ppap_core.identity import SESSION_COOKIE
from ppap_core.settings import Settings


def test_template_list_freezes_real_bytes_and_generic_download_cannot_bypass_drive(auth_store_factory):
    tenant = "template-" + uuid.uuid4().hex
    settings = Settings(feishu_app_id="template-app", feishu_template_sources=[{"tenant_key": tenant, "customer_id": "海岸线", "folder_token": "test-folder"}])
    store = auth_store_factory(settings, reset=False)
    principal = store.upsert_feishu_identity(tenant, "template-reader")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    files = [{"token": "template-one", "name": "控制计划.xlsx", "type": "file", "modified_time": "1750000000"}]
    content = b"real-provider-template-one"
    denied = False

    def provider(request):
        assert request.headers["authorization"] == "Bearer template-user-token"
        if denied:
            return httpx.Response(403, json={"code": 1063002})
        if request.url.path == "/open-apis/drive/v1/files":
            assert request.url.params["folder_token"] == "test-folder"
            return httpx.Response(200, json={"code": 0, "data": {"files": files, "has_more": False}})
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=content)
        if request.url.path == "/open-apis/drive/v1/metas/batch_query":
            token = json.loads(request.content)["request_docs"][0]["doc_token"]
            selected = next(row for row in files if row["token"] == token)
            return httpx.Response(200, json={"code": 0, "data": {"metas": [{"doc_token": token, "doc_type": "file", "title": selected["name"], "size": len(content)}]}})
        raise AssertionError(request.url.path)

    with httpx.Client(transport=httpx.MockTransport(provider)) as http, TestClient(create_app(settings, auth_store=store, http=http)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(principal, UserTokens("template-user-token", "", expires, expires)))
        source = client.post("/files", json={"original_filename": "rule.md", "content_type": "text/markdown", "byte_size": 4}).json()
        path = f"/files/{source['file_id']}/versions/{source['id']}"
        assert client.put(path + "/blob", content=b"rule").status_code == 200
        assert client.post(path + "/finalize").status_code == 200
        assert (
            client.post(
                "/rules",
                json={
                    "title": "海岸线控制计划要求",
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
        response = client.get("/audit-templates", params={"customer_id": "海岸线", "page_size": 10})
        assert response.status_code == 200
        assert client.get("/audit-templates", params={"customer_id": "海岸线", "page_size": 9}).status_code == 422
        assert response.headers["cache-control"] == "private, no-store"
        page = response.json()
        assert page["total"] == page["folder_template_count"] == 1
        item = page["items"][0]
        assert item["file"]["status"] == "ready"
        assert item["reference"]["file_version_id"] == item["file"]["id"]

        def simultaneous_read(_index):
            with TestClient(create_app(settings, auth_store=store, http=http)) as reader:
                reader.cookies.update(client.cookies)
                result = reader.get("/audit-templates", params={"customer_id": "海岸线"})
                assert result.status_code == 200
                return result.json()["items"][0]["reference"]

        with ThreadPoolExecutor(max_workers=2) as readers:
            assert list(readers.map(simultaneous_read, range(2))) == [item["reference"], item["reference"]]
        assert client.get(f"/files/{item['file']['file_id']}/versions/{item['file']['id']}/download").status_code == 403
        selected = f"/audit-templates/{item['reference']['template_id']}/versions/{item['file']['id']}"
        assert client.get(selected, params={"customer_id": "海岸线"}).content == content
        original = content
        content = b"provider-template-replaced"
        newer = client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
        assert newer["folder_revision"] != page["folder_revision"]
        assert newer["items"][0]["file"]["id"] != item["file"]["id"]
        assert client.get(selected, params={"customer_id": "海岸线"}).content == original
        package_query = {"customer_id": "海岸线", "folder_id": newer["folder_id"], "folder_revision": newer["folder_revision"], "folder_template_count": 1}
        package = client.get("/audit-template-packages", params=package_query)
        assert package.status_code == 200
        metadata = package.json()
        assert metadata["complete"] is True
        downloaded = client.get("/audit-template-packages/download", params=package_query)
        assert downloaded.status_code == 200
        assert metadata["byte_size"] == len(downloaded.content)
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert archive.namelist() == ["控制计划.xlsx"]
            assert archive.read("控制计划.xlsx") == content
        assert client.get("/audit-template-packages", params={**package_query, "folder_revision": page["folder_revision"]}).status_code == 409
        denied = True
        assert client.get(selected, params={"customer_id": "海岸线"}).status_code == 403
        assert client.get("/audit-template-packages/download", params=package_query).status_code == 403
        denied = False
        files[:] = [{"token": "template-" + str(index), "name": "控制计划.xlsx", "type": "file", "modified_time": str(1750000000 + index)} for index in range(12)]
        full = client.get("/audit-templates", params={"customer_id": "海岸线"}).json()
        assert full["total"] == full["folder_template_count"] == 12
        assert len(full["items"]) == 10
        second = client.get("/audit-templates", params={"customer_id": "海岸线", "page": 2}).json()
        assert len(second["items"]) == 2 and second["folder_revision"] == full["folder_revision"]
        assert full["items"][0]["updated_at"] > second["items"][0]["updated_at"]
        filtered = client.get("/audit-templates", params={"customer_id": "海岸线", "search": "PDF"}).json()
        assert filtered["total"] == 0 and filtered["folder_template_count"] == 12
        whole = {"customer_id": "海岸线", "folder_id": full["folder_id"], "folder_revision": full["folder_revision"], "folder_template_count": 12}
        metadata = client.get("/audit-template-packages", params=whole).json()
        assert len(metadata["templates"]) == len({entry["filename"] for entry in metadata["templates"]}) == 12
        assert len({entry["reference"]["file_version_id"] for entry in metadata["templates"]}) == 12
        files.append({"token": "nested-folder", "name": "子目录", "type": "folder", "modified_time": "1750000013"})
        assert client.get("/audit-template-packages/download", params=whole).status_code == 409
        assert client.get("/audit-tasks").json()["tasks"] == []
        assert client.get("/rounds").json()["rounds"] == []
        assert client.get("/usage").json()["events"] == []
        other = store.upsert_feishu_identity("other-" + tenant, "template-reader")
        client.cookies.set(SESSION_COOKIE, store.create_session(other, UserTokens("template-user-token", "", expires, expires)))
        assert client.get("/audit-templates", params={"customer_id": "海岸线"}).status_code == 403
        client.cookies.clear()
        assert client.get("/audit-templates", params={"customer_id": "海岸线"}).status_code == 401
