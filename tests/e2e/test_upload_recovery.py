"""Real Product API/DB upload recovery; only reply delivery is interrupted."""

import io
import json
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from openpyxl import Workbook
from playwright.sync_api import expect, sync_playwright

from .conftest import isolated_dev_login

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@pytest.mark.parametrize("lost_reply", ["blob", "draft"])
def test_received_batch_survives_lost_receipts_and_browser_record_removal(lost_reply):
    workbook = Workbook()
    workbook.active.append(["序号", "检查项目"])
    workbook.active.append([1, "原请求回执核对"])
    output = io.BytesIO()
    workbook.save(output)
    payload = output.getvalue()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        api = isolated_dev_login(page, WEB)
        blobs, reads, creates = [], [], []
        unknown = {"request_id": None, "allow_read": False}

        def lose_blob_reply(route):
            response = route.fetch()
            assert response.status == 200
            blobs.append(response.json()["id"])
            if len(blobs) == 3 and lost_reply == "blob":
                unknown["request_id"] = blobs[-1]
                route.abort("failed")
            else:
                route.fulfill(response=response)

        def lose_draft_reply(route):
            response = route.fetch()
            assert response.status == 200
            files = json.loads(response.json()["case_context"]["registration"])["files"]
            pending = next((file for file in files if file["name"] == "recovery-3.xlsx"), None)
            if lost_reply == "draft" and pending and pending.get("uploadReceipt") and len(blobs) == 2 and not unknown["request_id"]:
                unknown["request_id"] = pending["uploadReceipt"]["request_id"]
                route.abort("failed")
            else:
                route.fulfill(response=response)

        def read_receipt(route):
            reads.append(route.request.url)
            if not unknown["allow_read"]:
                route.abort("failed")
            else:
                route.continue_()

        page.on("request", lambda request: creates.append(request.post_data_json["request_id"]) if request.method == "POST" and request.url == api + "/files" else None)
        page.route(api + "/files/*/versions/*/blob", lose_blob_reply)
        page.route(api + "/file-uploads/*", read_receipt)
        page.route(api + "/drafts/*", lambda route: lose_draft_reply(route) if route.request.method == "PUT" else route.continue_())
        page.get_by_role("button", name="上传资料", exact=True).click()
        page.get_by_label("上传文件", exact=True).set_input_files(
            [{"name": f"recovery-{number}.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": payload} for number in range(1, 4)]
        )
        table = page.get_by_role("table", name="审核资料", exact=True)
        pending_row = table.get_by_role("row").filter(has_text="recovery-3.xlsx")
        expect(pending_row.get_by_role("button", name="核对上传结果" if lost_reply == "blob" else "上传此文件", exact=True)).to_be_enabled()
        draft_id = parse_qs(urlsplit(page.url).query)["case"][0]
        saved = page.request.get(f"{api}/drafts/{draft_id}")
        assert saved.status == 200
        draft = saved.json()
        assert len(draft["items"]) == 2
        files = json.loads(draft["case_context"]["registration"])["files"]
        assert [file["name"] for file in files] == ["recovery-1.xlsx", "recovery-2.xlsx", "recovery-3.xlsx"]
        assert files[2]["uploadReceipt"]["request_id"] == unknown["request_id"]
        page.evaluate("localStorage.clear()")
        page.reload()
        table = page.get_by_role("table", name="审核资料", exact=True)
        expect(table).to_contain_text("recovery-1.xlsx")
        expect(table).to_contain_text("recovery-2.xlsx")
        unknown["allow_read"] = True
        with page.expect_response(lambda response: response.request.method == "PUT" and response.url == f"{api}/drafts/{draft_id}" and len(response.request.post_data_json["items"]) == 3) as recovered:
            page.get_by_role("button", name="核对上传结果" if lost_reply == "blob" else "核对并继续原传输", exact=True).click()
        assert recovered.value.status == 200
        expect(page.get_by_role("button", name="核对上传结果", exact=True)).to_have_count(0)
        ready = page.request.get(f"{api}/drafts/{draft_id}").json()
        assert [item["file_version_id"] for item in ready["items"]] == blobs
        assert creates == blobs
        assert len(creates) == 3
        assert len(reads) == (2 if lost_reply == "blob" else 1)
        receipt = page.request.get(f"{api}/file-uploads/{unknown['request_id']}").json()
        assert receipt["state"] == "ready"
        assert receipt["file"]["id"] == unknown["request_id"]
        browser.close()
