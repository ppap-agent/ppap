"""Representative browser audit loop against isolated-dev stack."""

from __future__ import annotations

import io
import json
import os
import re
import time
import unittest
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from zipfile import BadZipFile

import pytest
from openpyxl import Workbook
from product.fixtures import RULE_A

from .conftest import isolated_dev_login

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None
else:
    expect.set_options(timeout=20_000)


WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def workbook_bytes(text):
    book = Workbook()
    book.active.append(["检查内容", text])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def upload_version(page, api, name, body, file_id=None):
    path = f"/files/{file_id}/versions" if file_id else "/files"
    created = page.request.post(api + path, data={"original_filename": name, "content_type": "application/octet-stream", "byte_size": len(body)})
    assert created.status == 201
    version = created.json()
    path = f"{api}/files/{version['file_id']}/versions/{version['id']}"
    assert page.request.put(path + "/blob", data=body).status == 200
    assert page.request.post(path + "/finalize").status == 200
    return version


PRESENCE_CHECKER = """import json
from io import BytesIO
from openpyxl import load_workbook
from audit.contracts import AuditEvidence, AuditResultItem, EvidenceSource

def check(context):
    book = load_workbook(BytesIO(context.sources[context.audit_file.audit_id]), read_only=True)
    try:
        sheet = book.active
        present = bool(sheet["A1"].value)
        locator = json.dumps({"kind": "xlsx_cell", "sheet": sheet.title, "cell": "A1"})
        return AuditResultItem(classification="资料", item="固定原件 A1 已填写", priority="P0",
            status="PASS" if present else "NG", suggestions="已核对固定原件 A1。", clause_id=context.clause_id, checks_performed=1,
            evidence=AuditEvidence(required="structure", used="structure", source_format="xlsx", prepared_format="xlsx",
                sources=(EvidenceSource(kind="structure", locator=locator),)))
    finally:
        book.close()
"""


def adopt_presence_rule(page, api):
    """Publish an independent, explicit test Suite through the real product API.

    A shared seed Rule is an unbound starter, not permission to execute a hidden
    presence fallback. Per-test rules also avoid changing another xdist case's
    selection while its round is being accepted.
    """
    stamp = uuid.uuid4().hex
    source = upload_version(page, api, f"presence-rule-{stamp}.txt", "固定原件 A1 必须已填写。".encode())
    response = page.request.post(
        f"{api}/rules",
        data={
            "title": f"A1 fixture {stamp}",
            "source_file_version_id": source["id"],
            "request_id": str(uuid.uuid4()),
            "body": {
                "kind": "engine",
                "type": "控制计划",
                "customers": [],
                "models": [],
                "parts": [],
                "rules": [{"id": "presence", "category": "资料", "priority": "P0", "rule_to_check": "固定原件 A1 必须已填写。", "type_of_rule": "text"}],
            },
        },
    )
    assert response.status == 201, response.text()
    rule = response.json()
    suite_url = f"{api}/suites/{quote(rule['body']['suite_key'], safe='')}/active"
    response = page.request.get(suite_url)
    assert response.status == 200, response.text()
    active = response.json()
    response = page.request.get(f"{api}/suite-revisions/{active['current_revision_id']}")
    assert response.status == 200, response.text()
    rules = json.loads(response.json()["files"]["rules.json"])
    assert rules["checker_binding"] == {}
    rules["checker_binding"] = {"presence": "checker:check"}
    response = page.request.post(
        f"{api}/suite-revisions/{active['current_revision_id']}/candidates",
        data={"operation_id": str(uuid.uuid4()), "files": {"rules.json": json.dumps(rules, ensure_ascii=False), "checker.py": PRESENCE_CHECKER}},
    )
    assert response.status == 201, response.text()
    candidate = response.json()
    response = page.request.put(
        suite_url,
        data={"revision_id": candidate["id"], "expected_revision": active["revision"], "operation_id": str(uuid.uuid4()), "force_unverified": True},
    )
    assert response.status == 200, response.text()
    assert response.json()["revision_id"] == candidate["id"]
    assert response.json()["verification_status"] == "unverified"
    return rule["id"]


def test_presence_fixture_checks_frozen_a1_and_does_not_fabricate_pass():
    namespace = {}
    exec(compile(PRESENCE_CHECKER, "presence-fixture.py", "exec"), namespace)
    for value, expected in (("Frozen original", "PASS"), (None, "NG")):
        book = Workbook()
        book.active["A1"] = value
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        context = SimpleNamespace(audit_file=SimpleNamespace(audit_id="original"), sources={"original": stream.getvalue()}, clause_id="presence")
        result = namespace["check"](context)
        assert result.status == expected
        assert result.checks_performed == 1
        assert result.clause_id == "presence"
        assert json.loads(result.evidence.sources[0].locator) == {"kind": "xlsx_cell", "sheet": "Sheet", "cell": "A1"}
    with pytest.raises(BadZipFile):
        namespace["check"](SimpleNamespace(audit_file=SimpleNamespace(audit_id="original"), sources={"original": b"broken workbook"}, clause_id="presence"))


def completed_round(page, api, round_id):
    deadline = time.monotonic() + 20
    while True:
        response = page.request.get(f"{api}/rounds/{round_id}")
        assert response.status == 200
        result = response.json()
        if result["execution"] in {"completed", "failed"}:
            assert result["execution"] == "completed", result.get("failure")
            assert result["findings"] and all(row["status"] == "PASS" for row in result["findings"])
            assert result["executor"] == "ppap-checker-core"
            assert all(row["locator"]["semantic"]["checks_performed"] == 1 for row in result["findings"])
            return result
        assert time.monotonic() < deadline, "The isolated Checker Core did not finish the accepted round"
        page.wait_for_timeout(50)


def accept_round(page, api, items, predecessor=None, draft_id=None):
    if draft_id:
        assert page.request.put(f"{api}/drafts/{draft_id}", data={"items": items}).status == 200
    else:
        response = page.request.post(f"{api}/drafts", data={"items": items, "predecessor_id": predecessor})
        assert response.status == 201
        draft_id = response.json()["id"]
    response = page.request.post(f"{api}/drafts/{draft_id}/accept", data={"idempotency_key": str(uuid.uuid4())})
    assert response.status == 201
    return completed_round(page, api, response.json()["id"])


@unittest.skipUnless(sync_playwright, "playwright not installed")
class WebAuditLoopTests(unittest.TestCase):
    def test_uploaded_versions_reopen_frozen_round_and_review_after_an_explicit_new_round(self):
        # UI upload + real Product API/DB/object storage/Worker + current history UI.
        # Presence is an integration fixture, not proof of PPAP rule quality or V3 matching.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            rule_id = adopt_presence_rule(page, api)
            stamp = uuid.uuid4().hex[:8]
            name, context = f"report-{stamp}.xlsx", f"context-{stamp}.txt"
            target_bytes, reference_bytes = workbook_bytes("第一版"), b"frozen reference"
            # Existing drafts retain their original bindings and upload recovery.
            draft_id = page.request.post(f"{api}/drafts", data={}).json()["id"]
            page.goto(f"{WEB}/?case={draft_id}")
            # The existing draft must finish its read before accepting user input.
            # set_input_files alone can dispatch change on the disabled hidden input.
            with page.expect_file_chooser() as chooser:
                page.get_by_role("button", name="添加文件", exact=True).click()
            with page.expect_response(
                lambda reply: reply.request.method == "PUT" and reply.url.endswith(f"/drafts/{draft_id}") and len(reply.request.post_data_json.get("items", [])) == 2
            ) as uploaded:
                chooser.value.set_files(
                    [
                        {"name": name, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": target_bytes},
                        {"name": context, "mimeType": "text/plain", "buffer": reference_bytes},
                    ]
                )
            self.assertEqual(uploaded.value.status, 200)
            table = page.get_by_role("table", name="审核资料", exact=True)
            expect(table.get_by_role("button", name="阅读 " + name, exact=True)).to_be_visible()
            expect(table.get_by_role("button", name="阅读 " + context, exact=True)).to_be_visible()
            draft_id = parse_qs(urlsplit(page.url).query)["case"][0]
            draft = page.request.get(f"{api}/drafts/{draft_id}").json()
            self.assertEqual(len(draft["items"]), 2)
            target = next(row for row in draft["items"] if row["original_filename"] == name)
            reference = next(row for row in draft["items"] if row["original_filename"] == context)
            original = accept_round(
                page,
                api,
                [
                    {"file_version_id": target["file_version_id"], "role": "target", "rule_version_id": rule_id},
                    {"file_version_id": reference["file_version_id"], "role": "reference", "rule_version_id": None},
                ],
                draft_id=draft_id,
            )

            page.goto(f"{WEB}/audit?round={original['id']}")
            expect(page.get_by_role("heading", name="历史审核", exact=True)).to_be_visible()
            # Core findings are formal audit items, not unmapped historical records.
            detail = page.get_by_role("region", name="审核详情", exact=True)
            expect(detail.get_by_role("heading", name=name, exact=True)).to_be_visible()
            checks = detail.get_by_role("region", name="审核项", exact=True)
            presence = checks.get_by_role("list", name="审核项列表", exact=True).get_by_role("button", name="查看依据 presence", exact=True)
            expect(presence.get_by_text("符合", exact=True)).to_be_visible()
            presence.click()
            original_reader = page.get_by_role("dialog", name="presence · 资料", exact=True)
            basis = original_reader.get_by_role("article", name="条款判定依据", exact=True)
            expect(basis.get_by_text("符合", exact=True)).to_be_visible()
            expect(basis.get_by_text("固定原件 A1 必须已填写。", exact=True)).to_be_visible()
            basis.get_by_role("button", name="核对 " + name, exact=True).click()
            original_panel = original_reader.get_by_role("region", name="原件预览", exact=True)
            expect(original_panel).to_have_attribute("data-file-version", target["file_version_id"])
            download = original_panel.get_by_role("link", name="下载原件", exact=True)
            download_url = urljoin(page.url, download.get_attribute("href"))
            self.assertEqual(download_url, f"{api}/rounds/{original['id']}/files/{target['file_id']}/versions/{target['file_version_id']}/download")
            self.assertEqual(page.request.get(download_url).body(), target_bytes)
            page.keyboard.press("Escape")
            expect(original_reader).not_to_be_visible()
            detail.get_by_role("button", name="逐条复核", exact=True).click()
            focus = detail.get_by_role("region", name="专注复核", exact=True)
            expect(focus.get_by_text("人工已复核 0/1 · 不改变机器结果", exact=True)).to_be_visible()
            focus.get_by_role("button", name="记录人工复核", exact=True).click()
            editor = focus.get_by_role("region", name="人工复核编辑", exact=True)
            editor.get_by_role("combobox", name="人工判断", exact=True).click()
            page.get_by_role("option", name="确认机器结果", exact=True).click()
            editor.get_by_role("textbox", name="判断理由", exact=True).fill("已核对第一版原件，记录不等于正式批准。")
            editor.get_by_role("textbox", name="后续建议", exact=True).fill("保留版本与原件。")
            with page.expect_response(lambda reply: reply.request.method == "PUT" and reply.url.endswith(f"/rounds/{original['id']}/reviews")) as saved:
                editor.get_by_role("button", name="保存人工复核", exact=True).click()
            self.assertEqual(saved.value.status, 200)
            self.assertEqual(saved.value.request.post_data_json["item_key"], original["findings"][0]["item_key"])
            expect(editor.get_by_text("人工复核已保存。", exact=True)).to_be_visible()
            editor.get_by_role("button", name="完成", exact=True).click()
            saved_original = page.request.get(f"{api}/rounds/{original['id']}").json()
            self.assertEqual(saved_original["findings"][0]["review"]["judgment"], "confirm")
            self.assertEqual(saved_original["findings"][0]["status"], "PASS")
            self.assertEqual(saved_original["inputs"], original["inputs"])
            self.assertEqual(saved_original["result_attempt"], original["result_attempt"])
            self.assertEqual(
                [{key: value for key, value in row.items() if key != "review"} for row in saved_original["findings"]],
                [{key: value for key, value in row.items() if key != "review"} for row in original["findings"]],
            )
            expect(focus.get_by_text("人工已复核 1/1 · 不改变机器结果", exact=True)).to_be_visible()
            expect(focus.get_by_role("region", name="已有人工复核", exact=True)).to_contain_text("已核对第一版原件，记录不等于正式批准。")

            newer_bytes = workbook_bytes("第二版")
            newer = upload_version(page, api, name, newer_bytes, target["file_id"])
            self.assertNotEqual(newer["id"], target["file_version_id"])
            second = accept_round(
                page,
                api,
                [
                    {"file_version_id": newer["id"], "role": "target", "rule_version_id": rule_id},
                    {"file_version_id": reference["file_version_id"], "role": "reference", "rule_version_id": None},
                ],
                predecessor=original["id"],
            )
            self.assertEqual(second["predecessor_id"], original["id"])
            self.assertIsNone(second["findings"][0]["review"])
            page.goto(f"{WEB}/audit?round={second['id']}")
            expect(detail.get_by_role("group", name="审核结果筛选", exact=True).get_by_role("button", name="符合 1", exact=True)).to_be_visible()
            detail.get_by_role("button", name="查看原件", exact=True).click()
            original_panel = original_reader.get_by_role("region", name="原件预览", exact=True)
            expect(original_panel).to_have_attribute("data-file-version", newer["id"])
            download = original_panel.get_by_role("link", name="下载原件", exact=True)
            download_url = urljoin(page.url, download.get_attribute("href"))
            self.assertEqual(download_url, f"{api}/rounds/{second['id']}/files/{newer['file_id']}/versions/{newer['id']}/download")
            self.assertEqual(page.request.get(download_url).body(), newer_bytes)

            page.goto(WEB)
            page.goto(f"{WEB}/audit?round={original['id']}")
            detail.get_by_role("button", name="逐条复核", exact=True).click()
            recorded = focus.get_by_role("region", name="已有人工复核", exact=True)
            expect(recorded).to_contain_text("已核对第一版原件，记录不等于正式批准。")
            expect(recorded).to_contain_text("保留版本与原件。")
            review_address = page.url
            self.assertEqual(parse_qs(urlsplit(review_address).query)["round"], [original["id"]])
            self.assertEqual(parse_qs(urlsplit(review_address).query)["clause"], ["presence"])
            self.assertEqual(parse_qs(urlsplit(review_address).query)["focus"], ["review"])
            page.reload()
            expect(page).to_have_url(review_address)
            expect(recorded).to_contain_text("已核对第一版原件，记录不等于正式批准。")
            expect(focus.get_by_role("article", name="条款判定依据", exact=True).get_by_text("符合", exact=True)).to_be_visible()
            original_panel = focus.get_by_role("region", name="原件预览", exact=True)
            expect(original_panel).to_have_attribute("data-file-version", target["file_version_id"])
            download = original_panel.get_by_role("link", name="下载原件", exact=True)
            download_url = urljoin(page.url, download.get_attribute("href"))
            self.assertEqual(download_url, f"{api}/rounds/{original['id']}/files/{target['file_id']}/versions/{target['file_version_id']}/download")
            self.assertEqual(page.request.get(download_url).body(), target_bytes)
            self.assertEqual(page.request.get(f"{api}/rounds/{original['id']}").json(), saved_original)
            writes = []
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            with page.expect_response(lambda reply: reply.request.method == "POST" and reply.url == f"{api}/drafts") as preparation_saved:
                page.get_by_role("button", name="历史操作", exact=True).click()
                page.get_by_role("menuitem", name="补件／重新审核", exact=True).click()
            self.assertEqual(preparation_saved.value.status, 201)
            preparation = preparation_saved.value.json()
            self.assertIsNone(preparation["task_id"])
            self.assertIsNone(preparation["predecessor_id"])
            snapshot = preparation["upload_preparation"]
            self.assertEqual(snapshot["predecessor_id"], original["id"])
            self.assertEqual(snapshot["analysis_mode"], original["analysis_mode"])
            self.assertEqual(snapshot["uploads"], [])
            prepared = {row["file_version_id"]: row for row in snapshot["files"]}
            self.assertEqual(set(prepared), {target["file_version_id"], reference["file_version_id"]})
            self.assertTrue(all(row["selection"] == "selected" for row in prepared.values()))
            self.assertEqual(prepared[target["file_version_id"]]["preferred_rule_version_id"], rule_id)
            self.assertTrue(prepared[reference["file_version_id"]]["reference_only"])
            self.assertIsNone(page.request.get(f"{api}/drafts/{preparation['id']}/round").json())
            expect(page.get_by_role("heading", name="核对本次文件", exact=True)).to_be_visible()
            expect(page.get_by_role("region", name="重审文件与规则", exact=True)).to_contain_text(name)
            expect(page.get_by_role("region", name="重审文件与规则", exact=True)).to_contain_text(context)
            self.assertEqual(urlsplit(page.url).path, "/tasks/new")
            self.assertEqual(parse_qs(urlsplit(page.url).query)["fromRound"], [original["id"]])
            # Persisting preparation is allowed; recognition, acceptance, confirm and resubmit are not.
            self.assertEqual(writes, [f"{api}/drafts"])
            self.assertEqual(page.request.get(f"{api}/rounds/{original['id']}").json(), saved_original)
            browser.close()

    def test_docs_addon_entry_reports_missing_host(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 560, "height": 600})
            page.goto(f"{WEB}/addon")
            page.wait_for_selector("text=当前环境不支持 docs-addon 登录")
            expect(page.get_by_test_id("addon-history")).to_be_visible()
            href = page.get_by_test_id("addon-history").get_attribute("href")
            self.assertTrue(href and href.rstrip("/").endswith("audit-files"))
            self.assertNotIn(".html", href)
            expect(page.get_by_test_id("addon-submit")).to_be_disabled()
            browser.close()

    def test_audit_files_path_loads_history_after_login(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            name = f"history-{uuid.uuid4().hex[:8]}.txt"
            created = page.request.post(f"{api}/files", data={"original_filename": name, "content_type": "text/plain", "byte_size": 7})
            self.assertEqual(created.status, 201)
            version = created.json()
            path = f"{api}/files/{version['file_id']}/versions/{version['id']}"
            uploaded = page.request.put(path + "/blob", data=b"history", headers={"Content-Type": "text/plain"})
            self.assertEqual(uploaded.status, 200)
            self.assertEqual(page.request.post(path + "/finalize").status, 200)
            draft = page.request.post(f"{api}/drafts", data={})
            self.assertEqual(draft.status, 201)
            draft_id = draft.json()["id"]
            saved = page.request.put(f"{api}/drafts/{draft_id}", data={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]})
            self.assertEqual(saved.status, 200)
            accepted = page.request.post(f"{api}/drafts/{draft_id}/accept", data={"idempotency_key": str(uuid.uuid4())})
            self.assertEqual(accepted.status, 201)
            round_id = accepted.json()["id"]

            requests = []
            page.on("request", lambda request: requests.append((request.method, urlsplit(request.url).path)))
            page.goto(f"{WEB}/audit-files")
            expect(page.get_by_role("heading", name="工作台", exact=True)).to_be_visible()
            page.get_by_role("textbox", name="搜索审核任务", exact=True).fill(name)
            history = page.get_by_role("table", name="全部任务审核记录", exact=True)
            row = history.get_by_role("row").filter(has_text=name)
            expect(row).to_have_count(1)
            expect(row.get_by_role("link")).to_have_attribute("href", f"/audit?round={round_id}")
            expect(page.get_by_role("alert")).to_have_count(0)
            self.assertIn(("GET", urlsplit(api + "/rounds").path), requests)
            self.assertIn(("GET", urlsplit(api + "/drafts").path), requests)
            self.assertEqual([method for method, _ in requests if method in {"POST", "PUT", "PATCH", "DELETE"}], [])
            self.assertEqual(urlsplit(page.url).path, "/workbench")
            browser.close()

    def test_return_from_requirements_restores_review_file_clause_filter_and_next_pending(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            rule_id = adopt_presence_rule(page, api)
            stamp = uuid.uuid4().hex[:8]
            names = [f"alpha-{stamp}.xlsx", f"beta-{stamp}.xlsx"]
            versions = [upload_version(page, api, name, workbook_bytes(name)) for name in names]
            original = accept_round(page, api, [{"file_version_id": row["id"], "role": "target", "rule_version_id": rule_id} for row in versions])
            # Existing stage deep links retain their legacy review context.
            page.goto(f"{WEB}/?round={original['id']}&stage=decision")
            expect(page.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            directory = page.get_by_role("dialog", name="审核条款", exact=True)
            directory.get_by_role("radio", name="全部", exact=True).check()
            links = directory.get_by_role("navigation", name="审核条款", exact=True).get_by_role("link")
            expect(links).to_have_count(2)
            first_clause, next_clause = [parse_qs(urlsplit(link.get_attribute("href")).query)["clause"][0] for link in (links.first, links.nth(1))]
            findings = {row["item_key"]: row for row in original["findings"]}
            self.assertEqual({first_clause, next_clause}, set(findings))
            by_version = {version["id"]: version for version in versions}
            first_version, next_version = [by_version[findings[key]["locator"]["file_version_id"]] for key in (first_clause, next_clause)]
            self.assertNotEqual(first_version["id"], next_version["id"])
            self.assertEqual([findings[key]["locator"]["semantic"]["clause_id"] for key in (first_clause, next_clause)], ["presence", "presence"])
            links.nth(1).click()
            page.wait_for_function("(clause) => new URL(location.href).searchParams.get('clause') === clause", arg=next_clause)
            article = page.get_by_role("article", name="当前条款 presence", exact=True)
            evidence = article.get_by_role("region", name="机器检查依据", exact=True)
            expect(evidence.get_by_role("button", name=next_version["original_filename"] + " · Sheet · A1", exact=True)).to_be_visible()
            source_download = page.get_by_role("region", name="当前原件", exact=True).get_by_role("link", name="下载原件", exact=True)
            expect(source_download).to_have_attribute("href", f"{api}/rounds/{original['id']}/files/{next_version['file_id']}/versions/{next_version['id']}/download")
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            links.first.click()
            page.wait_for_function("(clause) => new URL(location.href).searchParams.get('clause') === clause", arg=first_clause)
            # Both targets use the same clause title; that title is not an identity.
            expect(evidence.get_by_role("button", name=first_version["original_filename"] + " · Sheet · A1", exact=True)).to_be_visible()
            expect(source_download).to_have_attribute("href", f"{api}/rounds/{original['id']}/files/{first_version['file_id']}/versions/{first_version['id']}/download")
            heading = article.get_by_role("heading").inner_text()
            page.get_by_role("combobox", name="复核原件", exact=True).click()
            page.get_by_role("option", name=names[0], exact=True).click()
            page.get_by_role("combobox", name="复核原件", exact=True).click()
            page.get_by_role("option", name=names[1], exact=True).click()
            expect(page).to_have_url(re.compile(r"[?&]reviewFile="))
            expected = parse_qs(urlsplit(page.url).query)
            self.assertEqual(expected["round"], [original["id"]])
            self.assertEqual(expected["reviewFilter"], ["all"])
            self.assertEqual(expected["clause"], [first_clause])
            self.assertEqual(json.loads(expected["reviewFile"][0])[-1], versions[1]["id"])
            selected_download_url = f"{api}/rounds/{original['id']}/files/{versions[1]['file_id']}/versions/{versions[1]['id']}/download"
            expect(source_download).to_have_attribute("href", selected_download_url)
            page.get_by_role("link", name="工作台", exact=True).click()
            page.get_by_role("navigation", name="产品导航", exact=True).get_by_role("button", name="审核规则", exact=True).click()
            expect(page.get_by_role("heading", name="审核规则", exact=True)).to_be_visible()
            page.go_back()
            page.go_back()
            expect(page.get_by_role("heading", name="人工复核", exact=True)).to_be_visible()
            page.reload()
            expect(article.get_by_role("heading")).to_have_text(heading)
            expect(evidence.get_by_role("button", name=first_version["original_filename"] + " · Sheet · A1", exact=True)).to_be_visible()
            expect(page.get_by_role("combobox", name="复核原件", exact=True)).to_contain_text(names[1])
            expect(source_download).to_have_attribute("href", selected_download_url)
            restored = parse_qs(urlsplit(page.url).query)
            for key in ("round", "clause", "reviewFile", "reviewFilter"):
                self.assertEqual(restored[key], expected[key])
            page.get_by_role("button", name=re.compile("^条款目录")).click()
            expect(directory.get_by_role("radio", name="全部", exact=True)).to_be_checked()
            active = directory.get_by_role("navigation", name="审核条款", exact=True).locator("[aria-current=page]")
            self.assertEqual(parse_qs(urlsplit(active.get_attribute("href")).query)["clause"], expected["clause"])
            page.keyboard.press("Escape")
            article.get_by_role("combobox", name=re.compile(" 人工判断$")).click()
            page.get_by_role("option", name="确认机器结果", exact=True).click()
            article.get_by_role("textbox", name=re.compile(" 复核依据$")).fill("返回后核对原轮文件并保存。")
            with page.expect_response(lambda reply: reply.request.method == "PUT" and reply.url.endswith(f"/rounds/{original['id']}/reviews")) as saved:
                article.get_by_role("button", name="保存并继续", exact=True).click()
            self.assertEqual(saved.value.status, 200)
            page.wait_for_function("(next) => new URL(location.href).searchParams.get('clause') === next", arg=next_clause)
            expect(article.get_by_role("heading")).to_have_text(heading)
            expect(evidence.get_by_role("button", name=next_version["original_filename"] + " · Sheet · A1", exact=True)).to_be_visible()
            expect(source_download).to_have_attribute("href", f"{api}/rounds/{original['id']}/files/{next_version['file_id']}/versions/{next_version['id']}/download")
            expect(article.get_by_role("combobox", name=re.compile(" 人工判断$"))).to_contain_text("尚未判断")
            updated = page.request.get(f"{api}/rounds/{original['id']}").json()
            reviewed = [row for row in updated["findings"] if row["review"]]
            self.assertEqual(len(reviewed), 1)
            self.assertEqual(reviewed[0]["item_key"], expected["clause"][0])
            self.assertEqual(reviewed[0]["review"]["rationale"], "返回后核对原轮文件并保存。")
            self.assertIsNone(next(row for row in updated["findings"] if row["item_key"] == next_clause)["review"])
            self.assertEqual(updated["inputs"], original["inputs"])
            self.assertEqual([row["status"] for row in updated["findings"]], ["PASS", "PASS"])
            self.assertEqual(parse_qs(urlsplit(page.url).query)["round"], [original["id"]])
            browser.close()


if __name__ == "__main__":
    unittest.main()
