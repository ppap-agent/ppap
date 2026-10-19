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
    root_meta = {"user_id": "12345"}
    folder_meta = {"token": "test-folder", "createUid": "12345", "ownUid": "99999"}
    profile = {"tenant_key": tenant, "open_id": "template-reader", "name": "模板创建人"}
    metadata_unavailable = False
    members = [{"member_type": "openid", "member_id": "template-reader", "name": "模板创建人", "perm": "full_access", "type": "user", "perm_type": "container"}]
    permissions_denied = False

    def provider(request):
        assert request.headers["authorization"] == "Bearer template-user-token"
        if denied:
            return httpx.Response(403, json={"code": 1063002})
        if request.url.path == "/open-apis/drive/explorer/v2/root_folder/meta":
            if metadata_unavailable:
                return httpx.Response(403, json={"code": 1063002})
            return httpx.Response(200, json={"code": 0, "data": root_meta})
        if request.url.path == "/open-apis/drive/explorer/v2/folder/test-folder/meta":
            return httpx.Response(200, json={"code": 0, "data": folder_meta})
        if request.url.path == "/open-apis/authen/v1/user_info":
            return httpx.Response(200, json={"code": 0, "data": profile})
        if request.url.path == "/open-apis/drive/v1/permissions/test-folder/members":
            assert request.url.params["type"] == "folder"
            if permissions_denied:
                return httpx.Response(403, json={"code": 1063002})
            return httpx.Response(200, json={"code": 0, "data": {"items": members}})
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
        assert page["creator_name"] == "模板创建人"
        assert page["manage_access"] == {"state": "allowed", "reason": None}
        permissions_query = {"customer_id": "海岸线", "folder_id": page["folder_id"], "query": ""}
        permissions = client.get("/audit-template-permissions", params=permissions_query)
        assert permissions.status_code == 200
        assert permissions.headers["cache-control"] == "private, no-store"
        observed = permissions.json()
        assert observed["folder"] == {"customer_id": "海岸线", "folder_id": page["folder_id"]}
        assert observed["members"] == [{"target": {"kind": "user", "id": "template-reader"}, "display_name": "模板创建人", "role": "可管理", "source": "unknown"}]
        assert observed["actions"] == []
        assert observed["manage_access"] == {"state": "allowed", "reason": None}
        folder_meta["createUid"] = "99999"
        non_creator = client.get("/audit-template-permissions", params=permissions_query).json()
        assert non_creator["creator_name"] is None
        assert non_creator["manage_access"] == {"state": "allowed", "reason": None}
        folder_meta["createUid"] = "12345"
        assert client.get("/audit-template-permissions", params={**permissions_query, "folder_id": str(uuid.uuid4())}).status_code == 403
        filtered_permissions = client.get("/audit-template-permissions", params={**permissions_query, "query": "未找到"}).json()
        assert filtered_permissions["members"] == []
        assert filtered_permissions["permission_revision"] == observed["permission_revision"]
        members[0]["perm"] = "view"
        read_only = client.get("/audit-template-permissions", params=permissions_query).json()
        assert read_only["permission_revision"] != observed["permission_revision"]
        assert read_only["members"][0]["role"] == "可阅读"
        assert read_only["manage_access"]["state"] == "unknown"
        assert read_only["actions"] == []
        members[0]["perm"] = "full_access"
        members.append({"member_type": "openchat", "member_id": "internal-group", "name": "模板协作组", "perm": "view", "type": "chat"})
        ordered = client.get("/audit-template-permissions", params=permissions_query).json()
        members.reverse()
        assert client.get("/audit-template-permissions", params=permissions_query).json() == ordered
        members.reverse()
        members.pop()
        members.append(dict(members[0]))
        assert client.get("/audit-template-permissions", params=permissions_query).status_code == 503
        members.pop()
        for field, unsupported in (("member_type", "department_id"), ("perm", "unexpected"), ("member_id", "../other-folder")):
            original_value = members[0][field]
            members[0][field] = unsupported
            assert client.get("/audit-template-permissions", params=permissions_query).status_code == 503
            members[0][field] = original_value
        permissions_denied = True
        assert client.get("/audit-template-permissions", params=permissions_query).status_code == 403
        permissions_denied = False
        # Creator and owner are different provider facts. Never substitute the
        # current viewer or a cached uploader for an unverified creator.
        for target, field, wrong in (
            (folder_meta, "createUid", "99999"),
            (folder_meta, "token", "another-folder"),
            (profile, "tenant_key", "another-tenant"),
            (profile, "open_id", "another-user"),
            (root_meta, "user_id", 0),
            (root_meta, "user_id", True),
            (root_meta, "user_id", None),
            (profile, "name", "  "),
        ):
            original_value = target[field]
            target[field] = wrong
            unverified = client.get("/audit-templates", params={"customer_id": "海岸线"})
            assert unverified.status_code == 200
            assert unverified.json()["creator_name"] is None
            assert unverified.json()["manage_access"]["state"] == ("unknown" if target is profile and field in {"tenant_key", "open_id"} else "allowed")
            assert unverified.json()["total"] == 1
            if target is profile and field in {"tenant_key", "open_id"}:
                assert client.get("/audit-template-permissions", params=permissions_query).status_code == 403
            target[field] = original_value
        metadata_unavailable = True
        assert client.get("/audit-templates", params={"customer_id": "海岸线"}).json()["creator_name"] is None
        metadata_unavailable = False
        assert client.get("/audit-templates", params={"customer_id": "海岸线"}).json()["creator_name"] == "模板创建人"
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
        missing_identity = client.get("/audit-template-permissions", params=permissions_query)
        assert missing_identity.status_code == 503
        assert missing_identity.json() == {"error": "feishu_identity_unavailable"}
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
        assert client.get("/audit-template-permissions", params=permissions_query).status_code == 403
        client.cookies.clear()
        assert client.get("/audit-templates", params={"customer_id": "海岸线"}).status_code == 401
        assert client.get("/audit-template-permissions", params=permissions_query).status_code == 401
