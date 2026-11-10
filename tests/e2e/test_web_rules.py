"""Customer rule review/publication against the actual isolated-dev stack."""

from __future__ import annotations

import json
import os
import re
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from .conftest import isolated_dev_login

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


def confirm_clause_source(page, clause, location, excerpt):
    from playwright.sync_api import expect

    clause.get_by_role("button", name="核对出处", exact=True).click()
    page.get_by_role("combobox", name="原文位置", exact=True).click()
    page.get_by_role("option", name=re.compile("^" + re.escape(location) + " ·")).click()
    expect(page.get_by_role("blockquote")).to_contain_text(excerpt)
    page.get_by_role("button", name="返回条款", exact=True).click()


@unittest.skipUnless(sync_playwright, "playwright not installed")
class RulePreparationTests(unittest.TestCase):
    def test_original_docx_rules_are_reviewed_saved_and_restored(self):
        from playwright.sync_api import expect

        rule_root = Path(__file__).resolve().parents[2] / "assets" / "poc测试" / "poc规则"
        sources = [
            ("8规则-过程流程图.docx", "过程流程图", 9),
            ("9规则-潜在过程失效模式及后果分析（PFMEA）.docx", "潜在过程失效模式及后果分析", 7),
            ("10规则-控制计划.docx", "控制计划", 11),
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for filename, document_type, count in sources:
                with self.subTest(document_type=document_type):
                    original = rule_root / filename
                    content = original.read_bytes()
                    title = original.stem + " " + uuid.uuid4().hex[:8]
                    context = browser.new_context()
                    page = context.new_page()
                    publication_requests = []
                    page.on("request", lambda request, observed=publication_requests: observed.append(True) if request.method == "POST" and urlsplit(request.url).path.endswith("/rules") else None)
                    api = isolated_dev_login(page, WEB)
                    page.goto(WEB + "/requirements?rulesArea=requirements")
                    page.get_by_label("上传共用规则文件").set_input_files(original)
                    page.get_by_label("适用资料类型").select_option(document_type)
                    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path.endswith("/rules/parse")) as parsing:
                        page.get_by_role("button", name="提取条款", exact=True).click()
                    self.assertEqual(parsing.value.status, 202)
                    source = parsing.value.request.post_data_json
                    clauses = page.get_by_role("region", name="要求条款", exact=True)
                    expect(clauses.get_by_role("article")).to_have_count(count, timeout=30000)
                    parsed = page.request.get(f"{api}/rules/parse/{parsing.value.json()['id']}")
                    self.assertEqual(parsed.status, 200)
                    self.assertEqual(parsed.json()["status"], "succeeded")
                    candidate = parsed.json()["result"]["body"]
                    self.assertEqual(candidate["rule_profile"], "v3")
                    self.assertEqual(candidate["type"], document_type)
                    self.assertEqual([rule["id"] for rule in candidate["rules"]], [str(index) for index in range(1, count + 1)])
                    expected = [(rule["id"], rule["rule_to_check"]) for rule in candidate["rules"]]
                    units = page.request.get(f"{api}/files/{source['source_file_id']}/versions/{source['source_file_version_id']}/rule-sources")
                    self.assertEqual(units.status, 200)
                    excerpts = {unit["excerpt"] for unit in units.json()["units"]}
                    for identity, text in expected:
                        self.assertIn(text, excerpts)
                        expect(clauses.get_by_role("article", name=f"条款 {identity}", exact=True)).to_contain_text(text)
                    self.assertEqual(publication_requests, [], "Parsing must not submit a publication")
                    listed = page.request.get(f"{api}/rules")
                    self.assertEqual(listed.status, 200)
                    # Other tests can publish confirmed rules in this tenant.
                    # This parse must not publish a version from its own source.
                    for rule in listed.json()["rules"]:
                        detail = page.request.get(f"{api}/rules/{rule['id']}")
                        self.assertEqual(detail.status, 200)
                        self.assertNotEqual(detail.json()["source_file_version_id"], source["source_file_version_id"], "Parsing must not publish an unconfirmed rule")
                    page.get_by_label("要求名称", exact=True).fill(title)
                    for label in ("不限定客户", "不限定零件", "不限定图纸修订"):
                        page.get_by_label(label, exact=True).check()
                    page.get_by_label("来源说明／变更原因", exact=True).fill("按 POC 原始 DOCX 核对完整条款及出处。")
                    for identity, text in expected:
                        unit = next(unit for unit in units.json()["units"] if unit["excerpt"] == text)
                        clause = clauses.get_by_role("article", name=f"条款 {identity}", exact=True)
                        confirm_clause_source(page, clause, unit["location"], text)
                        clause.get_by_label(f"{identity} 已核对来源", exact=True).check()
                    page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
                    save = page.get_by_role("button", name="确认并保存版本", exact=True)
                    expect(save).to_be_enabled()
                    with page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path.endswith("/rules")) as publication:
                        save.click()
                    self.assertEqual(publication.value.status, 201)
                    version_id = publication.value.json()["id"]
                    expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
                    saved_url = page.url
                    loaded = page.request.get(f"{api}/rules/{version_id}")
                    self.assertEqual(loaded.status, 200)
                    version = loaded.json()
                    self.assertEqual(version["source_file_version_id"], source["source_file_version_id"])
                    self.assertEqual(version["body"]["rule_profile"], "v3")
                    self.assertEqual([(rule["id"], rule["rule_to_check"]) for rule in version["body"]["rules"]], expected)
                    registration = json.loads(version["case_context"]["registration"])
                    self.assertEqual(len(registration["basis"]["clauses"]), count)
                    self.assertTrue(all(clause["checked"] and clause["provenance"]["fileVersionId"] == source["source_file_version_id"] for clause in registration["basis"]["clauses"]))
                    downloaded = page.request.get(f"{api}/files/{source['source_file_id']}/versions/{source['source_file_version_id']}/download")
                    self.assertEqual(downloaded.status, 200)
                    self.assertEqual(downloaded.body(), content)
                    context.close()
                    context = browser.new_context()
                    page = context.new_page()
                    isolated_dev_login(page, WEB)
                    page.goto(saved_url)
                    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                    expect(page.get_by_role("region", name="要求条款", exact=True).get_by_role("article")).to_have_count(count)
                    expect(page.get_by_role("button", name="确认并保存版本", exact=True)).to_have_count(0)
                    self.assertEqual(parse_qs(urlsplit(page.url).query)["reqVersion"], [version_id])
                    context.close()
            browser.close()

    def test_import_review_publish_and_revisit(self):
        from playwright.sync_api import expect

        title = "规则导入 " + uuid.uuid4().hex[:8]
        source = {
            "kind": "engine",
            "type": "控制计划",
            "rule_profile": "v3",
            "customers": ["测试客户"],
            "models": [],
            "parts": [],
            "rules": [{"id": "1", "category": "基本信息", "priority": None, "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
        }
        source_bytes = json.dumps(source, ensure_ascii=False).encode()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(5000)
            api = isolated_dev_login(page, WEB)
            page.get_by_role("navigation", name="产品导航").get_by_role("button", name="审核规则", exact=True).click()
            page.get_by_role("tab", name="客户要求", exact=True).click()
            page.get_by_role("button", name="上传规则", exact=True).click()
            page.get_by_label("上传共用规则文件").set_input_files({"name": "控制计划规则.json", "mimeType": "application/json", "buffer": source_bytes})
            expect(page.get_by_label("要求名称", exact=True)).to_be_visible()
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            clauses = page.get_by_role("region", name="要求条款", exact=True)
            expect(clauses.get_by_role("article")).to_have_count(1, timeout=30000)
            clause = clauses.get_by_role("article", name="条款 1", exact=True)
            confirm_clause_source(page, clause, "rules[0].rule_to_check", "必须填写零件号")
            page.get_by_label("要求名称", exact=True).fill(title)
            page.get_by_label("适用客户", exact=True).fill("  测试客户  ")
            page.get_by_label("适用零件", exact=True).fill("  零件 P-1  ")
            page.get_by_label("不限定图纸修订", exact=True).check()
            page.get_by_label("来源说明／变更原因", exact=True).fill("依据结构化客户规则核对首个版本。")
            clauses.get_by_role("button", name="编辑条款", exact=True).click()
            expect(clauses.get_by_role("textbox", name="要执行的要求", exact=True)).to_be_visible()
            clauses.get_by_role("textbox", name="要执行的要求", exact=True).fill("必须填写零件号和版本")
            expect(clauses.get_by_label("出处位置", exact=True)).to_have_value("rules[0].rule_to_check")
            expect(clauses.get_by_label("出处位置", exact=True)).not_to_be_editable()
            clauses.get_by_label("1 已核对来源", exact=True).check()
            clauses.get_by_role("button", name="完成编辑", exact=True).click()
            page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            saved_url = page.url
            page.get_by_role("link", name="返回客户要求", exact=True).click()
            page.get_by_role("searchbox", name="搜索审核规则", exact=True).fill(title)
            link = page.get_by_role("link", name=title, exact=True).filter(visible=True)
            expect(link).to_have_count(1)
            link.click()
            saved_query, reopened_query = parse_qs(urlsplit(saved_url).query), parse_qs(urlsplit(page.url).query)
            self.assertEqual(reopened_query["requirement"], saved_query["requirement"])
            published = page.request.get(f"{api}/rules/{saved_query['reqVersion'][0]}")
            self.assertEqual(published.status, 200)
            self.assertEqual(published.json()["body"]["customers"], ["测试客户"])
            self.assertEqual(published.json()["body"]["parts"], ["零件 P-1"])
            page.close()
            page = browser.new_page()
            page.set_default_timeout(5000)
            isolated_dev_login(page, WEB)
            page.goto(saved_url)
            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expect(page.get_by_role("textbox", name="要执行的要求", exact=True)).to_have_count(0)
            expect(page.get_by_role("region", name="要求条款", exact=True)).to_contain_text("必须填写零件号和版本")
            expect(page.get_by_role("main")).to_contain_text("测试客户")
            expect(page.get_by_role("combobox", name="规则版本", exact=True)).to_contain_text("v1")
            page.get_by_role("button", name="查看原件 · 控制计划规则.json", exact=True).click()
            reader = page.get_by_role("dialog", name="原件与证据", exact=True)
            expect(reader).to_be_visible()
            downloaded = page.request.get(urljoin(page.url, reader.get_by_role("link", name="下载原件", exact=True).get_attribute("href")))
            self.assertEqual(downloaded.status, 200)
            self.assertEqual(downloaded.body(), source_bytes)
            browser.close()


if __name__ == "__main__":
    unittest.main()
