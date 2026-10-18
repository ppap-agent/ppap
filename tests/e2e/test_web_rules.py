"""Customer rule review/publication against the actual isolated-dev stack."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from urllib.parse import parse_qs, urljoin, urlsplit

from .conftest import isolated_dev_login

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional until the e2e job installs Playwright
    sync_playwright = None

WEB = os.environ.get("PPAP_WEB_URL", "http://127.0.0.1:4173").rstrip("/")


@unittest.skipUnless(sync_playwright, "playwright not installed")
class RulePreparationTests(unittest.TestCase):
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
            page.get_by_role("navigation", name="主要导航").get_by_role("link", name="审核要求", exact=True).click()
            page.get_by_role("button", name="上传要求", exact=True).click()
            page.get_by_label("上传共用要求文件").set_input_files({"name": "控制计划规则.json", "mimeType": "application/json", "buffer": source_bytes})
            expect(page.get_by_label("要求名称", exact=True)).to_be_visible()
            page.get_by_label("适用资料类型").select_option("控制计划")
            page.get_by_role("button", name="提取条款", exact=True).click()
            clauses = page.get_by_role("region", name="要求条款", exact=True)
            expect(clauses.get_by_role("article")).to_have_count(1, timeout=30000)
            page.get_by_label("要求名称", exact=True).fill(title)
            page.get_by_label("适用客户", exact=True).fill("  测试客户  ")
            page.get_by_label("适用零件", exact=True).fill("  零件 P-1  ")
            page.get_by_label("不限定图纸修订", exact=True).check()
            page.get_by_label("来源说明／变更原因", exact=True).fill("依据结构化客户规则核对首个版本。")
            clauses.get_by_role("button", name="编辑条款", exact=True).click()
            expect(clauses.get_by_role("textbox", name="要执行的要求", exact=True)).to_be_visible()
            clauses.get_by_role("textbox", name="要执行的要求", exact=True).fill("必须填写零件号和版本")
            clauses.get_by_label("出处位置", exact=True).fill("rules[0].rule_to_check")
            clauses.get_by_label("1 已核对来源", exact=True).check()
            clauses.get_by_role("button", name="完成编辑", exact=True).click()
            page.get_by_label("已核对本版本的来源、范围和变更", exact=True).check()
            page.get_by_role("button", name="确认并保存版本", exact=True).click()
            expect(page.get_by_role("status").filter(has_text="版本 v1 已保存")).to_be_visible()
            saved_url = page.url
            page.get_by_role("link", name="返回审核要求", exact=True).click()
            page.get_by_role("searchbox", name="搜索审核要求", exact=True).fill(title)
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
            expect(page.get_by_role("combobox", name="要求版本", exact=True)).to_contain_text("v1")
            page.get_by_role("button", name="查看原件 · 控制计划规则.json", exact=True).click()
            reader = page.get_by_role("dialog", name="原件与证据", exact=True)
            expect(reader).to_be_visible()
            downloaded = page.request.get(urljoin(page.url, reader.get_by_role("link", name="下载原件", exact=True).get_attribute("href")))
            self.assertEqual(downloaded.status, 200)
            self.assertEqual(downloaded.body(), source_bytes)
            browser.close()


if __name__ == "__main__":
    unittest.main()
