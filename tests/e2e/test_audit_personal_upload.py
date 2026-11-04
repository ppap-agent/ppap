"""Product upload UI with controlled HTTP; real Feishu delivery is checked separately."""

import os

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    expect = sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="requires e2e dependency group")
WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@pytest.mark.parametrize("width", [1440, 768, 390])
@pytest.mark.parametrize("recover_with_cloud", [False, True])
def test_choose_folder_cancel_and_resume_keep_original_upload_request(width, recover_with_cloud):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 954})
        page.route("**/runtime-config.json", lambda r: r.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", lambda r: r.fulfill(status=404, json={"error": "unexpected_test_request"}))
        page.route("**/api/public-config", lambda r: r.fulfill(json={"profile": "production", "login": "feishu", "api_url": WEB + "/api", "personal_drive_upload": True}))
        page.route(
            "**/api/me",
            lambda r: r.fulfill(
                json={
                    "user_id": "owner",
                    "tenant_id": "tenant",
                    "membership": "member",
                    "display_name": "上传测试",
                    "identity": {"provider": "feishu", "app_id": "app", "tenant_key": "tenant", "subject": "owner"},
                }
            ),
        )
        page.route("**/api/audit-customers/default", lambda r: r.fulfill(json={"customer_preference": None, "revision": 0}))
        page.route("**/api/audit-customers", lambda r: r.fulfill(json={"customers": []}))
        page.route("**/api/audit-tasks?*", lambda r: r.fulfill(json={"tasks": [], "next_offset": None}))
        page.route("**/api/integrations/feishu/root-folder", lambda r: r.fulfill(json={"token": "personal-root", "name": "我的文件夹", "type": "folder"}))
        page.route(
            "**/api/integrations/feishu/files?*",
            lambda r: r.fulfill(
                json={
                    "files": [{"token": "chosen-folder", "name": "项目资料", "type": "folder"}]
                    if "personal-root" in r.request.url
                    else []
                    if "folder_token=" in r.request.url
                    else [{"token": "real-cloud-file", "name": "审核资料.xlsx", "type": "file"}],
                    "next_page_token": None,
                }
            ),
        )
        creates, finishes, blobs = [], [], []
        version = {}
        imported = {}
        import_inputs = []
        preparation = {}

        def prepare(route):
            if route.request.method == "POST":
                body = route.request.post_data_json
                preparation.update(
                    id=body["request_id"],
                    status="open",
                    task_id=None,
                    items=[],
                    upload_preparation=body["upload_preparation"],
                    preparation_revision=0,
                    analysis_mode="standard",
                    predecessor_id=None,
                    case_context={},
                    writeback={"available": False},
                    updated_at="2026-10-22T00:00:00Z",
                )
            elif route.request.method == "PUT":
                body = route.request.post_data_json
                assert body["expected_revision"] == preparation["preparation_revision"]
                preparation.update(upload_preparation=body["upload_preparation"], preparation_revision=body["expected_revision"] + 1)
            route.fulfill(json=preparation)

        page.route("**/api/drafts", prepare)
        page.route("**/api/drafts/*", prepare)

        def create(route):
            body = route.request.post_data_json
            creates.append(body)
            version.update(
                id=body["request_id"], file_id="file-one", original_filename=body["original_filename"], status="staging", byte_size=body["byte_size"], content_type=body["content_type"], sha256=None
            )
            route.fulfill(status=201, json=version)

        def blob(route):
            blobs.append(route.request.method)
            version.update(status="ready", sha256=creates[0]["sha256"])
            route.fulfill(json=version)

        def finish(route):
            finishes.append(route.request.url)
            route.fulfill(json={"request_id": version["id"], "state": "unknown", "file": version, "reason": "尚未确认飞书文件的创建结果。", "retry_allowed": False, "resume_allowed": False})

        page.route("**/api/files", create)
        page.route("**/api/files/*/versions/*/blob", blob)
        page.route("**/api/file-uploads/*/feishu", finish)

        def import_cloud(route):
            body = route.request.post_data_json
            import_inputs.append(body)
            imported.update({**version, "id": body["request_id"], "file_id": "cloud-file"})
            route.fulfill(status=201, json=imported)

        def receipt(route):
            cloud = route.request.url.endswith(imported.get("id", "not-yet-imported"))
            state = "ready" if cloud or finishes and not recover_with_cloud else "unknown" if finishes else "uploading"
            route.fulfill(
                json={
                    "request_id": imported["id"] if cloud else version["id"],
                    "state": state,
                    "file": imported if cloud else version,
                    "reason": None,
                    "retry_allowed": False,
                    "resume_allowed": not finishes,
                }
            )

        page.route("**/api/integrations/feishu/files/import", import_cloud)
        page.route(
            "**/api/file-uploads/*",
            receipt,
        )
        page.route(
            "**/api/file-validations",
            lambda r: r.fulfill(
                json={
                    "id": r.request.post_data_json["request_id"],
                    "source_file_id": r.request.post_data_json["source_file_id"],
                    "source_file_version_id": r.request.post_data_json["source_file_version_id"],
                    "status": "completed",
                    "result": {"state": "valid", "reasons": []},
                }
            ),
        )
        page.route("**/api/files/*/versions/*/validation", lambda r: r.fulfill(json={"validation": None}))
        page.goto(WEB + "/audit")
        expect(page.get_by_role("heading", name="上传 PPAP 文件", exact=True)).to_be_visible()
        files = [{"name": "审核资料.xlsx", "mimeType": "application/octet-stream", "buffer": b"abcde"}]
        page.locator("input[type=file]").set_input_files(files)
        dialog = page.get_by_role("dialog", name="保存到飞书", exact=True)
        expect(dialog.get_by_text("审核资料.xlsx", exact=True)).to_be_visible()
        expect(dialog.get_by_role("button", name="上传到此文件夹", exact=True)).to_be_enabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f"/tmp/ppap-174-personal-folder-{width}.png")
        dialog.get_by_role("button", name="取消", exact=True).click()
        assert creates == [] and blobs == [] and finishes == []
        page.locator("input[type=file]").set_input_files(files)
        dialog.get_by_role("button", name="打开文件夹 项目资料", exact=True).click()
        dialog.get_by_role("button", name="上传到此文件夹", exact=True).click()
        expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="读取传输 审核资料.xlsx", exact=True)).to_be_enabled()
        assert creates[0]["feishu_folder_token"] == "chosen-folder"
        assert len(creates) == len(blobs) == len(finishes) == 1
        if recover_with_cloud:
            original_id = version["id"]
            read_cloud = page.get_by_role("button", name="从飞书核对 审核资料.xlsx", exact=True)
            read_cloud.click()
            cloud_dialog = page.get_by_role("dialog", name="从飞书选择文件", exact=True)
            cloud_dialog.get_by_role("button", name="取消", exact=True).click()
            expect(page.get_by_role("heading", name="待处理传输 · 1 份", exact=True)).to_be_visible()
            assert import_inputs == [] and len(finishes) == 1
            read_cloud.click()
            cloud_dialog.get_by_role("checkbox", name="选择飞书文件 审核资料.xlsx", exact=True).check()
            cloud_dialog.get_by_role("button", name="添加 1 份文件", exact=True).click()
            expect(page.get_by_role("checkbox", name="选择 审核资料.xlsx", exact=True)).to_be_checked()
            expect(page.get_by_role("heading", name="待处理传输 · 1 份", exact=True)).to_be_visible()
            assert version["id"] == original_id and imported["id"] != original_id
            assert import_inputs[0]["file_token"] == "real-cloud-file"
            page.get_by_role("button", name="排除传输 审核资料.xlsx", exact=True).click()
            expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
            expect(page.get_by_role("heading", name="已排除传输 · 1 份", exact=True)).to_be_visible()
        else:
            page.get_by_role("button", name="读取传输 审核资料.xlsx", exact=True).click()
        expect(page.get_by_role("checkbox", name="选择 审核资料.xlsx", exact=True)).to_be_checked()
        assert len(creates) == len(blobs) == len(finishes) == 1
        browser.close()
