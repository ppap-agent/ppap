"""Native Web at its public HTTP boundary; not a database or delivery proof."""

import copy
import os
import re
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


class PreparationHTTP:
    def __init__(self, lose_prepare_reply=False, lose_task_reply=False):
        self.customer_default = {"customer_preference": None, "revision": 0}
        self.default_writes = []
        self.lose_default_reply = False
        self.lose_prepare_reply = lose_prepare_reply
        self.lose_task_reply = lose_task_reply
        self.fail_draft_read = False
        self.allow_resume = False
        self.draft = None
        self.versions = {}
        self.validation_ids = {}
        self.creates = []
        self.blobs = []
        self.reads = []
        self.preparation_creates = []
        self.task_commands = []
        self.task = None
        self.allow_receipts = False

    def install(self, page):
        page.route("**/runtime-config.json", lambda route: route.fulfill(json={"api_url": WEB + "/api"}))
        page.route("**/api/**", self.respond)

    def respond(self, route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        method = route.request.method
        body = route.request.post_data_json if method in ("POST", "PUT") and path != "/files" and not path.endswith("/blob") else None
        if path == "/public-config":
            route.fulfill(json={"profile": "production", "login": "feishu", "api_url": WEB + "/api", "personal_drive_upload": False})
        elif path == "/me":
            route.fulfill(
                json={
                    "user_id": "owner",
                    "tenant_id": "tenant",
                    "membership": "member",
                    "display_name": "上传准备测试",
                    "identity": {"provider": "feishu", "app_id": "app", "tenant_key": "tenant", "subject": "owner"},
                }
            )
        elif path == "/audit-customers/default":
            if method == "PUT":
                self.default_writes.append(copy.deepcopy(body))
                assert body["confirmed"] is True
                if body["expected_revision"] != self.customer_default["revision"]:
                    route.fulfill(status=409, json={"error": "customer_default_conflict"})
                    return
                self.customer_default = {"customer_preference": body["customer_preference"], "revision": body["expected_revision"] + 1}
                if self.lose_default_reply:
                    route.abort("failed")
                    return
            route.fulfill(json=self.customer_default)
        elif path == "/audit-customers":
            route.fulfill(json={"customers": []})
        elif path == "/drafts" and method == "POST":
            self.preparation_creates.append(copy.deepcopy(body))
            self.draft = {
                "id": body["request_id"],
                "status": "open",
                "task_id": None,
                "upload_preparation": body["upload_preparation"],
                "preparation_revision": 0,
                "items": [],
                "analysis_mode": "standard",
                "predecessor_id": None,
                "case_context": {},
                "writeback": {"available": False},
                "updated_at": "2026-10-22T00:00:00Z",
            }
            if self.lose_prepare_reply:
                self.lose_prepare_reply = False
                route.abort("failed")
            else:
                route.fulfill(status=201, json=self.draft)
        elif self.draft and path == f"/drafts/{self.draft['id']}":
            if method == "GET" and self.fail_draft_read:
                route.fulfill(status=503, json={"error": "test_read_unavailable"})
                return
            if method == "PUT":
                assert self.draft["task_id"] is None
                assert body["expected_revision"] == self.draft["preparation_revision"]
                self.draft.update(upload_preparation=body["upload_preparation"], preparation_revision=body["expected_revision"] + 1)
            snapshot = copy.deepcopy(self.draft)
            snapshot["items"] = [
                {
                    "file_id": self.versions[row["file_version_id"]]["file_id"],
                    "file_version_id": row["file_version_id"],
                    "original_filename": self.versions[row["file_version_id"]]["original_filename"],
                    "role": "reference",
                    "rule": None,
                    "rule_version_id": None,
                    "status": "ready",
                }
                for row in snapshot["upload_preparation"]["files"]
            ]
            route.fulfill(json=snapshot)
        elif path in ("/drafts", "/rounds", "/audit-tasks") and method == "GET":
            route.fulfill(json={"drafts": [], "rounds": [], "tasks": [], "next_offset": None})
        elif path == "/files" and method == "POST":
            body = route.request.post_data_json
            assert self.draft is not None
            prepared = self.draft["upload_preparation"]["uploads"]
            assert any(row["request_id"] == body["request_id"] and row["sha256"] == body["sha256"] for row in prepared)
            original = next(row for row in prepared if row["request_id"] == body["request_id"])
            assert body["original_filename"] == original["name"] and body["content_type"] == original["content_type"]
            self.creates.append(body)
            version = {
                "id": body["request_id"],
                "file_id": "file-" + body["request_id"],
                "original_filename": body["original_filename"],
                "status": "staging",
                "sha256": None,
                "content_type": body["content_type"],
                "byte_size": body["byte_size"],
            }
            self.versions[version["id"]] = version
            route.fulfill(status=201, json=version)
        elif path.startswith("/files/") and path.endswith("/blob"):
            version = self.versions[path.split("/")[-2]]
            self.blobs.append(version["id"])
            version.update(status="ready", sha256=next(row["sha256"] for row in self.creates if row["request_id"] == version["id"]))
            if version["original_filename"] == "待核对.xlsx" and not self.allow_receipts:
                route.abort("failed")
            else:
                route.fulfill(json=version)
        elif path.startswith("/file-uploads/"):
            request_id = path.rsplit("/", 1)[-1]
            self.reads.append(request_id)
            version = self.versions[request_id]
            ready = self.allow_receipts or version["original_filename"] != "待核对.xlsx"
            route.fulfill(
                json={
                    "request_id": request_id,
                    "state": "ready" if ready else "uploading" if self.allow_resume else "unknown",
                    "file": version if ready else None,
                    "reason": None if ready else "尚未取得原上传结果。",
                    "retry_allowed": False,
                    "resume_allowed": self.allow_resume and not ready,
                }
            )
        elif path.startswith("/files/") and path.endswith("/validation"):
            version = self.versions[path.split("/")[-2]]
            key = self.validation_ids.setdefault(version["id"], "validation-" + version["id"])
            route.fulfill(json={"validation": self.validation(key, version)})
        elif path.startswith("/file-validations/"):
            key = path.rsplit("/", 1)[-1]
            version = next(row for version_id, row in self.versions.items() if self.validation_ids.get(version_id) == key)
            route.fulfill(json=self.validation(key, version))
        elif path.startswith("/files/") and method == "GET":
            route.fulfill(json=self.versions[path.rsplit("/", 1)[-1]])
        elif path == "/audit-tasks" and method == "POST":
            assert body["source_draft_id"] == self.draft["id"]
            assert body["expected_draft_revision"] == self.draft["preparation_revision"]
            self.task_commands.append(body)
            self.task = {
                "id": self.draft["id"],
                "number": "PPAP-恢复-001",
                "state": "matching",
                "analysis_mode": "standard",
                "customer_preference": body["customer_preference"],
                "source_draft_id": self.draft["id"],
                "round_id": None,
                "completion_reason": None,
                "estimated_points": None,
                "created_at": "2026-10-22T00:00:00Z",
                "updated_at": "2026-10-22T00:00:00Z",
                "input_revision": 0,
                "recognition_request_id": body["request_id"],
                "files": [
                    {
                        "file": self.versions[row["file_version_id"]],
                        "selection": row["selection"],
                        "removal_reason": row["removal_reason"],
                        "validation_id": row["validation_id"],
                        "validation": {"state": "valid", "reasons": []},
                        **({"reference_only": True} if row.get("reference_only") else {}),
                        "match": None,
                    }
                    for row in body["files"]
                ],
            }
            self.draft.update(task_id=self.draft["id"], preparation_revision=None)
            if self.lose_task_reply:
                self.lose_task_reply = False
                route.abort("failed")
            else:
                route.fulfill(json=self.task)
        elif self.task and path == f"/audit-tasks/{self.task['id']}/recognitions/{self.task['recognition_request_id']}":
            route.fulfill(json={"task_id": self.task["id"], "request_id": self.task["recognition_request_id"], "state": "accepted", "input_revision": 0, "task": self.task})
        elif self.task and path == f"/audit-tasks/{self.task['id']}":
            route.fulfill(json=self.task)
        else:
            route.fulfill(status=404, json={"error": "unexpected_test_request"})

    @staticmethod
    def validation(key, version):
        return {"id": key, "source_file_id": version["file_id"], "source_file_version_id": version["id"], "status": "completed", "result": {"state": "valid", "reasons": []}}


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("lose_prepare_reply", [False, True])
def test_unknown_upload_and_other_success_survive_a_fresh_page(width, lose_prepare_reply):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 954})
        http = PreparationHTTP(lose_prepare_reply)
        http.install(page)
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files([{"name": name, "mimeType": "application/octet-stream", "buffer": b"original"} for name in ("已接收.xlsx", "待核对.xlsx")])
        expect(page.get_by_role("checkbox", name="选择 已接收.xlsx", exact=True)).to_be_checked()
        expect(page.get_by_role("button", name="读取传输 待核对.xlsx", exact=True)).to_be_enabled()
        saved_url = page.url
        assert "uploadDraft=" in saved_url
        original_requests = [row["request_id"] for row in http.creates]
        assert len(original_requests) == len(http.blobs) == 2
        assert len(http.preparation_creates) == 1
        http.allow_receipts = True
        page.close()
        page = browser.new_page(viewport={"width": width, "height": 954})
        http.install(page)
        page.goto(saved_url)
        expect(page.get_by_role("checkbox", name="选择 已接收.xlsx", exact=True)).to_be_checked()
        expect(page.get_by_role("checkbox", name="选择 待核对.xlsx", exact=True)).to_be_checked()
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        assert [row["request_id"] for row in http.creates] == original_requests
        assert len(http.blobs) == 2 and len(http.preparation_creates) == 1
        assert set(original_requests).issubset(http.reads)
        assert http.task_commands == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=f"/tmp/ppap-native-preparation-restored-{width}.png")
        browser.close()


@pytest.mark.parametrize("lose_task_reply", [False, True])
def test_begin_recognition_promotes_and_reopens_the_same_draft_without_resubmission(lose_task_reply):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        http = PreparationHTTP(lose_task_reply=lose_task_reply)
        http.allow_receipts = True
        http.install(page)
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "已接收.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        saved_url = page.url
        draft_id = http.draft["id"]
        page.get_by_role("button", name="开始识别", exact=True).click()
        if lose_task_reply:
            page.get_by_role("button", name="读取识别回执", exact=True).click()
        expect(page).to_have_url(re.compile(rf"/tasks/{draft_id}(?:\?|$)"))
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        assert len(http.task_commands) == 1 and http.task_commands[0]["request_id"] != draft_id
        page.goto(saved_url)
        expect(page).to_have_url(re.compile(rf"/tasks/{draft_id}(?:\?|$)"))
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        assert len(http.task_commands) == 1 and len(http.creates) == len(http.blobs) == 1
        browser.close()


def test_failed_preparation_read_after_back_does_not_leave_the_previous_task_active():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        http = PreparationHTTP()
        http.allow_receipts = True
        http.install(page)
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "已接收.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        saved_url = page.url
        # Keep the prepared address in browser history, then open the promoted Task.
        page.goto(WEB + "/requirements")
        page.goto(saved_url)
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        page.get_by_role("button", name="开始识别", exact=True).click()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        http.fail_draft_read = True
        # Recognition first replaces the prepared address with its readback URL.
        # The earlier prepared history entry reproduces restoration from a Task.
        page.go_back()
        page.go_back()
        page.go_back()
        expect(page).to_have_url(saved_url)
        expect(page.get_by_role("button", name="读取准备记录", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_have_count(0)
        http.fail_draft_read = False
        page.get_by_role("button", name="读取准备记录", exact=True).click()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        assert len(http.task_commands) == 1
        browser.close()


@pytest.mark.parametrize("fail_resume_read", [False, True])
def test_same_bytes_can_be_reselected_without_changing_the_original_upload_identity(fail_resume_read):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        http = PreparationHTTP()
        http.allow_resume = True
        http.install(page)
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "待核对.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.get_by_role("button", name="读取传输 待核对.xlsx", exact=True)).to_be_enabled()
        saved_url = page.url
        original_request = http.creates[0]["request_id"]
        page.close()
        page = browser.new_page()
        http.install(page)
        if fail_resume_read:
            page.add_init_script("""(() => {
              const read = File.prototype.arrayBuffer;
              let fail = true;
              File.prototype.arrayBuffer = function() {
                if (fail && this.name === '待核对.xlsx') {
                  fail = false;
                  return Promise.reject(new DOMException('unreadable', 'NotReadableError'));
                }
                return read.call(this);
              };
            })();""")
        page.goto(saved_url)
        expect(page.get_by_role("button", name="重新选择原件 待核对.xlsx", exact=True)).to_be_enabled()
        page.get_by_role("button", name="重新选择原件 待核对.xlsx", exact=True).click()
        page.get_by_label("重新选择当时原件", exact=True).set_input_files({"name": "改名.xlsx", "mimeType": "application/vnd.ms-excel", "buffer": b"changed!"})
        expect(page.get_by_role("alert").filter(has_text="所选文件与当时原件内容不同")).to_be_visible()
        assert len(http.creates) == len(http.blobs) == 1
        page.get_by_role("button", name="重新选择原件 待核对.xlsx", exact=True).click()
        page.get_by_label("重新选择当时原件", exact=True).set_input_files({"name": "改名.xlsx", "mimeType": "application/vnd.ms-excel", "buffer": b"original"})
        expect(page.get_by_role("button", name="继续传输 待核对.xlsx", exact=True)).to_be_enabled()
        assert len(http.creates) == len(http.blobs) == 1
        http.allow_receipts = True
        page.get_by_role("button", name="继续传输 待核对.xlsx", exact=True).click()
        if fail_resume_read:
            expect(page.get_by_text("未能读取本地原件，本次未继续传输。请读取原请求，不要新建上传。", exact=True)).to_be_visible()
            assert len(http.creates) == len(http.blobs) == 1
            # Read the same unresolved request, then replace only its local bytes.
            http.allow_receipts = False
            page.get_by_role("button", name="读取传输 待核对.xlsx", exact=True).click()
            expect(page.get_by_role("button", name="重新选择原件 待核对.xlsx", exact=True)).to_be_enabled()
            page.get_by_role("button", name="重新选择原件 待核对.xlsx", exact=True).click()
            page.get_by_label("重新选择当时原件", exact=True).set_input_files({"name": "改名.xlsx", "mimeType": "application/vnd.ms-excel", "buffer": b"original"})
            expect(page.get_by_role("button", name="继续传输 待核对.xlsx", exact=True)).to_be_enabled()
            http.allow_receipts = True
            page.get_by_role("button", name="继续传输 待核对.xlsx", exact=True).click()
        expect(page.get_by_role("checkbox", name="选择 待核对.xlsx", exact=True)).to_be_checked()
        assert [row["request_id"] for row in http.creates] == [original_request, original_request]
        assert len(http.blobs) == 2 and len(http.preparation_creates) == 1
        browser.close()


def test_one_unreadable_local_file_does_not_block_the_other_files_before_transfer():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        http = PreparationHTTP()
        http.allow_receipts = True
        http.install(page)
        # Control the browser file-reading boundary, not audit or upload state.
        page.add_init_script("""(() => {
          const read = File.prototype.arrayBuffer;
          File.prototype.arrayBuffer = function() {
            return this.name === '不可读取.xlsx' ? Promise.reject(new DOMException('unreadable', 'NotReadableError')) : read.call(this);
          };
        })();""")
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files(
            [{"name": name, "mimeType": "application/octet-stream", "buffer": b"original"} for name in ("已接收.xlsx", "不可读取.xlsx", "另一份.xlsx")]
        )
        expect(page.get_by_role("checkbox", name="选择 已接收.xlsx", exact=True)).to_be_checked()
        expect(page.get_by_role("checkbox", name="选择 另一份.xlsx", exact=True)).to_be_checked()
        expect(page.get_by_text("本地原件无法读取，未开始上传。请排除此文件后重新选择。", exact=True)).to_be_visible()
        assert len(http.creates) == len(http.blobs) == 2
        assert {row["original_filename"] for row in http.creates} == {"已接收.xlsx", "另一份.xlsx"}
        assert len(http.preparation_creates) == 1
        page.get_by_role("button", name="排除传输 不可读取.xlsx", exact=True).click()
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        assert http.task_commands == []
        browser.close()


def test_prepared_ordinary_task_resolves_an_explicit_candidate_in_the_same_task():
    """UI request binding only; actual matching/freeze is owned by PG tests."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        http = PreparationHTTP()
        http.allow_receipts = True
        http.install(page)
        rules = [{"id": "rule-" + key, "title": "规则 " + key.upper(), "body": {"kind": "presence"}} for key in ("a", "b")]
        confirms, errors = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def rematch(route):
            if route.request.method != "POST" or not http.task:
                route.fallback()
                return
            value = route.request.post_data_json
            assert value["task_id"] == http.task["id"] and value["expected_revision"] == 0
            assert value["files"][0]["preferred_rule_version_id"] == rules[1]["id"]
            assert "source_draft_id" not in value
            http.task_commands.append(value)
            http.task.update(state="matching", input_revision=1, recognition_request_id=value["request_id"])
            http.task["files"][0].update(preferred_rule_version_id=rules[1]["id"], match=None)
            route.fulfill(status=202, json=http.task)

        def confirm(route):
            value = route.request.post_data_json
            assert value["expected_revision"] == 1
            assert http.task["files"][0]["match"]["rule"]["id"] == rules[1]["id"]
            confirms.append(value)
            http.task.update(state="reviewing", round_id="accepted-round", acceptance={"request_id": value["request_id"], "state": "accepted"}, progress={"completed": 0, "total": 1})
            route.fulfill(json=http.task)

        page.route("**/api/audit-tasks", rematch)
        page.route("**/api/audit-tasks/*/confirm", confirm)
        page.goto(WEB + "/tasks/new")
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "已接收.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.get_by_role("button", name="开始识别", exact=True)).to_be_enabled()
        page.get_by_role("button", name="开始识别", exact=True).click()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        original_id = http.task["id"]
        metadata = {"customer": None, "model": None, "part": None, "part_number": None}
        http.task.update(state="pending_confirm")
        http.task["files"][0]["match"] = {"state": "failed", "metadata": metadata, "document_type": "控制计划", "rule": None, "candidates": rules, "gaps": ["ambiguous_available_rules"]}
        page.reload()
        expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
        page.get_by_role("combobox", name="已接收.xlsx 的适用规则", exact=True).click()
        page.get_by_role("option", name="规则 B", exact=True).click()
        expect(page.get_by_role("heading", name="确认每份文件的审核规则", exact=True)).to_be_visible()
        expect(page.get_by_role("combobox", name="已接收.xlsx 的适用规则", exact=True)).to_contain_text("规则 B")
        expect(page.get_by_role("status").filter(has_text="文件或规则选择已有变更，请重新识别后再确认或结束任务。")).to_be_visible()
        expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_disabled()
        assert http.task["id"] == original_id and len(http.task_commands) == 1
        page.get_by_role("button", name="按所选规则重新识别", exact=True).click()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        http.task.update(state="pending_confirm", estimated_points=500)
        http.task["files"][0]["match"] = {"state": "matched", "metadata": metadata, "document_type": "控制计划", "rule": rules[1], "gaps": []}
        page.reload()
        expect(page.get_by_text("规则 B", exact=True)).to_be_visible()
        page.get_by_role("button", name="开始审核", exact=True).click()
        expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(0)
        assert http.task["id"] == original_id and len(confirms) == 1
        assert len(http.task_commands) == 2 and len(http.preparation_creates) == 1
        assert http.task_commands[0]["request_id"] != http.task_commands[1]["request_id"]
        assert len(http.creates) == len(http.blobs) == 1 and errors == []
        browser.close()


def test_customer_mode_keeps_task_selection_separate_from_confirmed_default():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        server = PreparationHTTP()
        server.customer_default = {"customer_preference": "UAES", "revision": 2}
        server.install(page)
        page.goto(WEB + "/tasks/new")
        choice = page.get_by_role("combobox", name="审核客户", exact=True)
        expect(choice).to_contain_text("UAES（联电）")
        choice.click()
        page.get_by_role("option", name="理想", exact=True).click()
        expect(choice).to_contain_text("理想")
        assert server.default_writes == []
        page.get_by_role("button", name="设为企业默认", exact=True).click()
        dialog = page.get_by_role("dialog", name="更改企业默认客户？")
        expect(dialog).to_contain_text("仅影响本企业以后新建的任务")
        dialog.get_by_role("button", name="取消", exact=True).click()
        assert server.default_writes == []
        expect(choice).to_contain_text("理想")
        page.get_by_role("button", name="设为企业默认", exact=True).click()
        page.get_by_role("button", name="确认更改", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("已保存企业默认")
        assert server.default_writes == [{"customer_preference": "理想", "expected_revision": 2, "confirmed": True}]
        choice.click()
        page.get_by_role("option", name="蔚来", exact=True).click()
        server.lose_default_reply = True
        page.get_by_role("button", name="设为企业默认", exact=True).click()
        page.get_by_role("button", name="确认更改", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("保存结果待核对")
        expect(page.get_by_text("本次切换只影响当前任务。企业默认：蔚来。", exact=True)).to_be_visible()
        assert len(server.default_writes) == 2
        choice.click()
        page.get_by_role("option", name="自动识别", exact=True).click()
        page.get_by_role("button", name="重新读取企业默认", exact=True).click()
        expect(choice).to_contain_text("自动识别")
        assert len(server.default_writes) == 2
        browser.close()


def test_pending_default_cannot_silently_create_an_auto_customer_upload():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        server = PreparationHTTP()
        server.customer_default = {"customer_preference": "UAES", "revision": 0}
        server.install(page)
        pending = []
        page.route("**/api/audit-customers/default", lambda route: pending.append(route))
        page.goto(WEB + "/tasks/new")
        expect(page.get_by_role("button", name="选择本地文件", exact=True)).to_be_disabled()
        expect(page.get_by_label("选择审核文件", exact=True)).to_be_disabled()
        assert server.preparation_creates == []
        pending.pop().fulfill(json=server.customer_default)
        expect(page.get_by_role("combobox", name="审核客户", exact=True)).to_contain_text("UAES（联电）")
        expect(page.get_by_role("button", name="选择本地文件", exact=True)).to_be_enabled()
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "资料.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        expect(page.get_by_role("checkbox", name="选择 资料.xlsx", exact=True)).to_be_checked()
        assert server.preparation_creates[0]["upload_preparation"]["customer_preference"] == "UAES"
        browser.close()


def test_explicit_auto_selection_can_continue_while_default_read_is_pending():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        server = PreparationHTTP()
        server.install(page)
        pending = []
        page.route("**/api/audit-customers/default", lambda route: pending.append(route))
        page.goto(WEB + "/tasks/new")
        choice = page.get_by_role("combobox", name="审核客户", exact=True)
        expect(choice).to_contain_text("请选择本次审核客户")
        choice.click()
        page.get_by_role("option", name="自动识别", exact=True).click()
        expect(page.get_by_role("button", name="选择本地文件", exact=True)).to_be_enabled()
        expect(choice).to_contain_text("自动识别")
        pending.pop().fulfill(json={"customer_preference": "UAES", "revision": 3})
        expect(page.get_by_text("本次切换只影响当前任务。企业默认：UAES（联电）。", exact=True)).to_be_visible()
        expect(choice).to_contain_text("自动识别")
        assert server.default_writes == []
        browser.close()


def test_v4_reference_choice_reaches_preparation_and_recognition():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        server = PreparationHTTP()
        server.customer_default = {"customer_preference": "蔚来", "revision": 0}
        server.install(page)
        page.goto(WEB + "/tasks/new")
        expect(page.get_by_role("combobox", name="审核客户", exact=True)).to_contain_text("蔚来")
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "特殊特性清单.xlsx", "mimeType": "application/octet-stream", "buffer": b"original"})
        role = page.get_by_role("combobox", name="特殊特性清单.xlsx 本轮用途", exact=True)
        role.click()
        page.get_by_role("option", name="比对资料 · 不单独审核或收费", exact=True).click()
        expect(role).to_contain_text("比对资料")
        page.get_by_role("button", name="开始识别", exact=True).click()
        expect(page.get_by_role("heading", name="正在识别文件与匹配规则", exact=True)).to_be_visible()
        assert server.task_commands[0]["files"][0]["reference_only"] is True
        assert server.draft["upload_preparation"]["files"][0]["reference_only"] is True
        browser.close()


class PhotoHTTP(PreparationHTTP):
    def __init__(self):
        super().__init__()
        self.customer_default = {"customer_preference": "UAES", "revision": 0}
        self.photo_commands = []
        self.photo_reads = []
        self.photo_job = None

    def respond(self, route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        if path == "/file-validations" and route.request.method == "POST":
            body = route.request.post_data_json
            assert body["scope"] == {"customer_preference": "UAES", "preset_key": "U13"}
            self.photo_commands.append(copy.deepcopy(body))
            version = self.versions[body["source_file_version_id"]]
            self.photo_job = {**self.validation(body["request_id"], version), "scope": body["scope"]}
            route.abort("failed")
        elif self.photo_job and path == "/file-validations/" + self.photo_job["id"]:
            self.photo_reads.append(self.photo_job["id"])
            route.fulfill(json=self.photo_job)
        else:
            super().respond(route)

    @staticmethod
    def validation(key, version):
        value = PreparationHTTP.validation(key, version)
        if version["original_filename"].endswith(".png"):
            value["result"] = {"state": "invalid", "reasons": ["普通审核类型不支持图片。"]}
        return value


@pytest.mark.parametrize("width", [1440, 390])
def test_photo_purpose_is_explicit_and_lost_reply_reads_same_scope(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 954})
        http = PhotoHTTP()
        http.install(page)
        page.goto(WEB + "/tasks/new")
        expect(page.get_by_label("选择审核文件", exact=True)).to_have_attribute("accept", re.compile(r"\.png"))
        page.get_by_label("选择审核文件", exact=True).set_input_files({"name": "样品.png", "mimeType": "image/png", "buffer": b"controlled-original"})
        purpose = page.get_by_role("button", name="作为生产件样品照片检查", exact=True)
        expect(purpose).to_be_visible()
        assert http.photo_commands == []
        purpose.click()
        expect(page.get_by_role("button", name="重新读取 样品.png 检查结果", exact=True)).to_be_visible()
        assert len(http.photo_commands) == 1
        # Replace the controlled receipt with its actual scoped decoding result.
        http.photo_job["result"] = {"state": "valid", "reasons": []}
        page.get_by_role("button", name="重新读取 样品.png 检查结果", exact=True).click()
        expect(page.get_by_role("checkbox", name="选择 样品.png", exact=True)).to_be_checked()
        expect(page.get_by_text("本轮用途：生产件样品照片；内容仍需人工确认", exact=True)).to_be_visible()
        assert len(http.photo_commands) == 1 and http.photo_reads == [http.photo_commands[0]["request_id"]]
        page.screenshot(path=f"/tmp/ppap-v4-photo-ui-{width}.png", full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="上一步", exact=True).click()
        page.get_by_role("combobox", name="审核客户", exact=True).click()
        page.get_by_role("option", name="理想", exact=True).click()
        expect(page.get_by_label("选择审核文件", exact=True)).not_to_have_attribute("accept", re.compile(r"\.png"))
        page.get_by_role("button", name="查看已选文件", exact=True).click()
        expect(page.get_by_text("普通审核类型不支持图片。", exact=True)).to_be_visible()
        expect(page.get_by_role("checkbox", name="选择 样品.png", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="作为生产件样品照片检查", exact=True)).to_have_count(0)
        assert len(http.photo_commands) == 1
        browser.close()


class PrecheckHTTP(PreparationHTTP):
    def __init__(self):
        super().__init__()
        version = {
            "id": "version-one",
            "file_id": "file-one",
            "original_filename": "长文件名称-控制计划-原件.xlsx",
            "status": "ready",
            "sha256": "a" * 64,
            "content_type": "application/octet-stream",
            "byte_size": 10,
        }
        rule = {
            "id": "v4-rule",
            "title": "蔚来控制计划",
            "body": {"kind": "engine", "rule_profile": "v4", "preset_key": "N03", "type": "控制计划", "customers": ["蔚来"], "models": [], "parts": [], "rules": []},
        }
        row = {
            "file": version,
            "selection": "selected",
            "removal_reason": None,
            "validation": {"state": "valid", "reasons": []},
            "match": {"state": "matched", "document_type": "控制计划", "metadata": {"customer": "蔚来", "model": None, "part": None, "part_number": None}, "rule": rule, "gaps": []},
        }
        self.task = {
            "id": "task-one",
            "number": "PPAP-预检-001",
            "state": "pending_confirm",
            "analysis_mode": "standard",
            "customer_preference": "蔚来",
            "source_draft_id": "task-one",
            "round_id": None,
            "completion_reason": None,
            "estimated_points": 500,
            "created_at": "2026-10-22T00:00:00Z",
            "updated_at": "2026-10-22T00:00:00Z",
            "input_revision": 1,
            "files": [row],
        }
        snapshot_input = {"files": [{"file": version}], "input_revision": 1}
        self.old = {
            "id": "old-snapshot",
            "task_id": "task-one",
            "input_revision": 0,
            "current": False,
            "status": "succeeded",
            "input": {"files": [{"file": version}], "input_revision": 0},
            "result": {"files": [{"file_version_id": version["id"], "facts": [{"id": "old", "status": "satisfied", "message": "旧输入的检查事实"}]}]},
            "created_at": "2026-10-21T00:00:00Z",
            "updated_at": "2026-10-21T00:00:00Z",
        }
        self.current = {
            "id": "failed-snapshot",
            "task_id": "task-one",
            "input_revision": 1,
            "current": True,
            "status": "failed",
            "input": snapshot_input,
            "result": None,
            "created_at": "2026-10-22T00:00:00Z",
            "updated_at": "2026-10-22T00:00:00Z",
        }
        self.task["precheck"] = copy.deepcopy(self.current)
        self.task["recognition_request_id"] = "recognition-one"
        self.retry_commands = []
        self.alias_reads = []
        self.export_reads = []

    def respond(self, route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        prefix = "/audit-tasks/task-one/prechecks"
        if path == prefix and route.request.method == "POST":
            body = route.request.post_data_json
            assert body["expected_revision"] == 1
            self.retry_commands.append(body)
            self.current = {
                **self.current,
                "id": "retried-snapshot",
                "status": "succeeded",
                "request_id": body["request_id"],
                "created_at": "2026-10-23T00:00:00Z",
                "result": {
                    "files": [
                        {
                            "file_version_id": "version-one",
                            "checker": {"profile": "v4"},
                            "facts": [
                                {"id": "S1", "status": "satisfied", "message": "原件结构已核实"},
                                {"id": "S2", "status": "not_checked", "message": "签章尚未核实"},
                                {"id": "technical", "status": "error", "message": "图形读取失败"},
                            ],
                        }
                    ]
                },
            }
            route.abort("failed")
        elif path == prefix:
            route.fulfill(json={"snapshots": [self.current, self.old]})
        elif self.retry_commands and path == prefix + "/" + self.retry_commands[0]["request_id"]:
            self.alias_reads.append(self.retry_commands[0]["request_id"])
            route.fulfill(json=self.current)
        elif path == prefix + "/old-snapshot/report":
            self.export_reads.append("old-snapshot")
            route.fulfill(content_type="text/html; charset=utf-8", body="<!doctype html><p>旧输入的检查事实</p>")
        elif path == prefix + "/retried-snapshot":
            route.fulfill(json=self.current)
        else:
            super().respond(route)


@pytest.mark.parametrize("width", [1440, 390])
def test_precheck_readback_history_and_export_keep_one_formal_action(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 954})
        http = PrecheckHTTP()
        http.install(page)
        page.goto(WEB + "/tasks/task-one")
        region = page.get_by_role("region", name="免费内容预检", exact=True)
        expect(region.get_by_role("heading", name="可审核 · 1", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="开始审核", exact=True)).to_have_count(1)
        region.get_by_role("button", name="重新检查当前资料", exact=True).click()
        expect(region.get_by_text("预检回执尚未核对。请读取原检查，已有文件仍保留。", exact=True)).to_be_visible()
        assert "precheckRequest=" in page.url
        page.reload()
        expect(region.get_by_text("有预检请求待核对，请读取原检查。不会再次提交检查请求。", exact=True)).to_be_visible()
        region.get_by_role("button", name="读取原检查", exact=True).click()
        expect(region.get_by_text("部分要求未核实", exact=True)).to_be_visible()
        assert len(http.retry_commands) == 1 and http.alias_reads == [http.retry_commands[0]["request_id"]]
        expect(region.get_by_role("heading", name="需要处理 · 0", exact=True)).to_be_visible()
        expect(region.get_by_role("heading", name="可审核 · 1", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="开始审核", exact=True)).to_be_enabled()
        region.get_by_role("button", name="查看检查依据与未覆盖要求", exact=True).click()
        expect(region.get_by_text("图形读取失败", exact=False)).to_be_visible()
        expect(region.get_by_text("签章尚未核实", exact=False)).to_be_visible()
        region.get_by_role("button", name="读取预检历史", exact=True).click()
        region.get_by_role("button", name="修订 0", exact=False).click()
        expect(region.get_by_text("历史快照，不代表当前资料", exact=False)).to_be_visible()
        with page.expect_download() as download:
            region.get_by_role("button", name="导出此预检记录", exact=True).click()
        assert download.value.suggested_filename == "PPAP-预检-old-snapshot.html"
        assert http.export_reads == ["old-snapshot"]
        page.screenshot(path=f"/tmp/ppap-v4-precheck-ui-{width}.png", full_page=True)
        assert page.get_by_role("button", name="开始审核", exact=True).count() == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
