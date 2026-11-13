"""Built-in template library routes over a controlled template root and Feishu HTTP."""

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product import template_library as library
from product.settings import Settings


def template_root(tmp_path, *, skip=()):
    for customer in library.CUSTOMERS:
        folder = tmp_path / customer.id / "templates"
        folder.mkdir(parents=True)
        for template in customer.templates:
            if template.id not in skip:
                (folder / template.source).write_bytes(("模板:" + template.id).encode())
    return tmp_path


def client_for(root, session, http=None, **settings):
    from api.template_routes import register_template_routes

    app = FastAPI()
    register_template_routes(app, None, lambda _request: session, http or httpx.Client(), Settings(template_root=str(root), **settings))
    return TestClient(app)


def signed_in(token="user-access"):
    return SimpleNamespace(principal=SimpleNamespace(user_id=str(uuid.uuid4()), tenant_id=str(uuid.uuid4())), access_token=token)


def test_every_route_requires_login(tmp_path):
    client = client_for(template_root(tmp_path), None)
    for path in ("/template-library", "/template-library/li/files/li-pfmea", "/template-library/li/archive"):
        assert client.get(path).status_code == 401
    assert client.post("/template-library/li/feishu", json={"folder_token": "folder"}).status_code == 401
    assert client.post("/template-submissions", json={}).status_code == 401


def test_catalog_lists_clean_names_and_only_present_files(tmp_path):
    client = client_for(template_root(tmp_path, skip={"nio-control-plan"}), signed_in())
    response = client.get("/template-library")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    customers = {row["id"]: row for row in response.json()["customers"]}
    assert [customers[key]["name"] for key in ("li", "nio", "uaes")] == ["理想汽车", "蔚来汽车", "联合汽车电子（UAES）"]
    present = {customer.id: {template.id for template in customer.templates} - {"nio-control-plan"} for customer in library.CUSTOMERS}
    assert {key: {template["id"] for template in row["templates"]} for key, row in customers.items()} == present
    approval = next(row for row in customers["uaes"]["templates"] if row["id"] == "uaes-sample-approval")
    size = len("模板:uaes-sample-approval".encode())
    # The part number and " (1)" copy suffix stay in the original only.
    expected = {"id": "uaes-sample-approval", "name": "样品认可报告", "document_type": "样品认可报告", "extension": ".xls", "filename": "样品认可报告.xls", "byte_size": size}
    # The sample approval report is the U19 顾客工程批准 template (PPAP-00-A §4.4.1).
    assert approval == {**expected, "type_code": "E11"}


def test_coverage_counts_against_each_customers_own_rule_types(tmp_path):
    from audit.presets import customer_presets

    body = client_for(template_root(tmp_path), signed_in()).get("/template-library").json()
    rows = {row["id"]: row for row in body["customers"]}
    for template_customer, preset_customer in library.PRESET_CUSTOMERS.items():
        assert rows[template_customer]["standard_type_codes"] == sorted({preset.source_type for preset in customer_presets() if preset.customer == preset_customer})


def test_download_one_template_with_utf8_filename(tmp_path):
    client = client_for(template_root(tmp_path), signed_in())
    response = client.get("/template-library/li/files/li-pfmea")
    assert response.status_code == 200
    assert response.content == "模板:li-pfmea".encode()
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "filename*=UTF-8''" + quote("潜在过程失效模式及后果分析（PFMEA）.xlsx") in response.headers["content-disposition"]
    assert client.get("/template-library/li/files/nio-pfmea").status_code == 404
    assert client.get("/template-library/li/files/..%2F..%2Fsecret").status_code == 404
    assert client.get("/template-library/unknown/archive").status_code == 404


def test_customer_archive_holds_every_template_under_its_clean_name(tmp_path):
    client = client_for(template_root(tmp_path), signed_in())
    response = client.get("/template-library/uaes/archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "filename*=UTF-8''" + quote("联合汽车电子（UAES）模板.zip") in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = sorted(archive.namelist())
        assert names == sorted(template.filename for template in library.find_customer("uaes").templates)
        assert archive.read("零件追溯检查单.xlsx") == "模板:uaes-traceability".encode()


class Drive:
    """Controlled Feishu Drive: a folder listing and the three upload calls."""

    def __init__(self, existing=(), finish=None):
        self.existing = list(existing)
        self.finish = finish or (lambda name: httpx.Response(200, json={"code": 0, "data": {"file_token": "file-" + str(len(self.finished))}}))
        self.prepared: list[str] = []
        self.finished: list[str] = []
        self.plans: dict[str, str] = {}

    def __call__(self, request):
        path = request.url.path
        if path.endswith("/drive/v1/files") and request.method == "GET":
            assert request.headers["authorization"] == "Bearer user-access"
            return httpx.Response(200, json={"code": 0, "data": {"files": [{"token": "t" + name, "name": name, "type": "file"} for name in self.existing], "has_more": False}})
        if path.endswith("upload_prepare"):
            body = json.loads(request.content)
            assert body["parent_node"] == "target-folder"
            upload_id = "upload-" + str(len(self.prepared))
            self.prepared.append(body["file_name"])
            self.plans[upload_id] = body["file_name"]
            return httpx.Response(200, json={"code": 0, "data": {"upload_id": upload_id, "block_size": 4 * 1024 * 1024, "block_num": 1}})
        if path.endswith("upload_part"):
            return httpx.Response(200, json={"code": 0})
        assert path.endswith("upload_finish")
        name = self.plans[json.loads(request.content)["upload_id"]]
        self.finished.append(name)
        return self.finish(name)


def test_save_to_feishu_reads_the_folder_back_and_never_duplicates(tmp_path):
    root = template_root(tmp_path)

    def finish(name):
        if name == "过程流程图.xlsx":
            return httpx.Response(500, text="lost")
        return httpx.Response(200, json={"code": 0, "data": {"file_token": "tok-" + str(abs(hash(name)))}})

    drive = Drive(existing=["控制计划.xlsx"], finish=finish)
    with httpx.Client(transport=httpx.MockTransport(drive)) as http:
        client = client_for(root, signed_in(), http, feishu_app_id="cli_test", feishu_app_secret="secret")
        response = client.post("/template-library/nio/feishu", json={"folder_token": "target-folder"})
        assert response.status_code == 200
        states = {row["filename"]: row["state"] for row in response.json()["results"]}
        assert states == {"尺寸测量报告.xlsm": "saved", "过程流程图.xlsx": "unknown", "潜在过程失效模式及后果分析.xlsx": "saved", "控制计划.xlsx": "exists", "特殊特性清单.xlsx": "saved"}
        assert "控制计划.xlsx" not in drive.prepared
        # The unconfirmed finish is not retried; a repeated request reconciles by reading the folder.
        assert drive.finished.count("过程流程图.xlsx") == 1
        drive.existing += drive.finished
        repeat = client.post("/template-library/nio/feishu", json={"folder_token": "target-folder", "template_ids": ["nio-process-flow"]})
        [row] = repeat.json()["results"]
        assert (row["template_id"], row["filename"], row["state"], row["file_token"]) == ("nio-process-flow", "过程流程图.xlsx", "exists", None)
        assert drive.finished.count("过程流程图.xlsx") == 1


def test_save_to_feishu_rejections_and_preconditions(tmp_path):
    root = template_root(tmp_path)
    drive = Drive(finish=lambda name: httpx.Response(400, json={"code": 1061043, "msg": "quota"}))
    with httpx.Client(transport=httpx.MockTransport(drive)) as http:
        assert client_for(root, signed_in(), http).post("/template-library/nio/feishu", json={"folder_token": "target-folder"}).json() == {"error": "feishu_disabled"}
        enabled = {"feishu_app_id": "cli_test", "feishu_app_secret": "secret"}
        assert client_for(root, signed_in(token=""), http, **enabled).post("/template-library/nio/feishu", json={"folder_token": "target-folder"}).json() == {"error": "feishu_user_token_missing"}
        client = client_for(root, signed_in(), http, **enabled)
        assert client.post("/template-library/nio/feishu", json={"folder_token": "../x"}).status_code == 400
        assert client.post("/template-library/nio/feishu", json={"folder_token": "target-folder", "template_ids": ["li-pfmea"]}).status_code == 404
        response = client.post("/template-library/nio/feishu", json={"folder_token": "target-folder", "template_ids": ["nio-control-plan"]})
        [row] = response.json()["results"]
        assert row["state"] == "failed" and row["file_token"] is None

    def unreadable(request):
        return httpx.Response(403, json={"code": 99991672})

    with httpx.Client(transport=httpx.MockTransport(unreadable)) as http:
        client = client_for(root, signed_in(), http, feishu_app_id="cli_test", feishu_app_secret="secret")
        # Without a read-back nothing is written.
        assert client.post("/template-library/nio/feishu", json={"folder_token": "target-folder"}).status_code == 502


def test_submission_records_only_readable_ready_versions_once(auth_store_factory):
    from api import create_app
    from product.feishu import UserTokens
    from product.identity import SESSION_COOKIE

    store = auth_store_factory(Settings(feishu_app_id="template-submission-test"), role="api")
    owner = store.upsert_feishu_identity("templates-" + uuid.uuid4().hex, "owner")
    other = store.upsert_feishu_identity("templates-" + uuid.uuid4().hex, "other")
    now = datetime.now(timezone.utc)
    tokens = UserTokens("user-access", "refresh", now + timedelta(hours=1), now + timedelta(days=1))
    version = store.create_staging(owner, "补充模板.xlsx", "application/octet-stream", 4)
    store.put_blob(version, b"abcd")
    ready = store.finalize_version(version)
    staging = store.create_staging(owner, "未完成.xlsx", "application/octet-stream", 4)
    with TestClient(create_app(store.settings, auth_store=store)) as client:
        client.cookies.set(SESSION_COOKIE, store.create_session(owner, tokens))
        request_id = str(uuid.uuid4())
        body = {"request_id": request_id, "customer": "理想汽车", "files": [{"file_id": ready.file_id, "version_id": ready.id}]}
        first = client.post("/template-submissions", json=body)
        assert first.status_code == 200
        assert first.json()["state"] == "received" and first.json()["file_count"] == 1
        assert client.post("/template-submissions", json=body).json() == first.json()
        assert client.post("/template-submissions", json={**body, "customer": "蔚来汽车"}).json() == {"error": "template_submission_conflict"}
        assert client.post("/template-submissions", json={"request_id": str(uuid.uuid4()), "files": [{"file_id": staging.file_id, "version_id": staging.id}]}).json() == {"error": "file_not_ready"}
        assert client.post("/template-submissions", json={"request_id": "x", "files": []}).status_code == 400
        client.cookies.set(SESSION_COOKIE, store.create_session(other, tokens))
        assert client.post("/template-submissions", json={**body, "request_id": str(uuid.uuid4())}).status_code == 403
    with store._conn() as conn:
        rows = conn.execute("SELECT s.tenant_id, s.customer, f.file_version_id FROM product.template_submissions s JOIN product.template_submission_files f ON f.submission_id = s.id").fetchall()
    assert [(str(tenant), customer, str(file)) for tenant, customer, file in rows] == [(owner.tenant_id, "理想汽车", ready.id)]
