"""Product upload UI with controlled HTTP; real Feishu delivery is checked separately."""

import os

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    expect = sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None, reason="requires e2e dependency group")
WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@pytest.mark.parametrize("width", [1440, 390])
def test_choose_folder_cancel_and_resume_keep_original_upload_request(width):
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
        page.route("**/api/audit-customers", lambda r: r.fulfill(json={"customers": []}))
        page.route("**/api/audit-tasks?*", lambda r: r.fulfill(json={"tasks": [], "next_offset": None}))
        page.route("**/api/integrations/feishu/root-folder", lambda r: r.fulfill(json={"token": "personal-root", "name": "我的文件夹", "type": "folder"}))
        page.route(
            "**/api/integrations/feishu/files?*",
            lambda r: r.fulfill(json={"files": [{"token": "chosen-folder", "name": "项目资料", "type": "folder"}] if "personal-root" in r.request.url else [], "next_page_token": None}),
        )
        creates, finishes, blobs = [], [], []
        version = {}

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
        page.route(
            "**/api/file-uploads/*",
            lambda r: r.fulfill(
                json={"request_id": version["id"], "state": "ready" if finishes else "uploading", "file": version, "reason": None, "retry_allowed": False, "resume_allowed": not finishes}
            ),
        )
        page.route(
            "**/api/file-validations",
            lambda r: r.fulfill(
                json={
                    "id": r.request.post_data_json["request_id"],
                    "source_file_id": "file-one",
                    "source_file_version_id": version["id"],
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
        dialog.get_by_role("button", name="取消", exact=True).click()
        assert creates == [] and blobs == [] and finishes == []
        page.locator("input[type=file]").set_input_files(files)
        dialog.get_by_role("button", name="打开文件夹 项目资料", exact=True).click()
        dialog.get_by_role("button", name="上传到此文件夹", exact=True).click()
        expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="读取传输 审核资料.xlsx", exact=True)).to_be_enabled()
        assert creates[0]["feishu_folder_token"] == "chosen-folder"
        assert len(creates) == len(blobs) == len(finishes) == 1
        page.get_by_role("button", name="读取传输 审核资料.xlsx", exact=True).click()
        expect(page.get_by_role("checkbox", name="选择 审核资料.xlsx", exact=True)).to_be_checked()
        assert len(creates) == len(blobs) == len(finishes) == 1
        browser.close()
