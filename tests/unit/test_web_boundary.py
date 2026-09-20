"""Web may call only the Product API."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"

FORBIDDEN = (
    "/uac/",
    "transformer/file-audit",
    "ppap-agent",
    "@insforge/sdk",
    "postgrest",
    "/rest/v1",
    "ppap_documents",
    "ppap_jobs",
    "ppap_findings",
    "insforge.dev",
)


class WebProductBoundaryTests(unittest.TestCase):
    def test_pc_client_only_uses_product_api(self):
        sources = []
        for path in WEB.iterdir():
            if path.suffix in {".js", ".html"}:
                sources.append((path.name, path.read_text()))
        self.assertTrue(sources)
        blob = "\n".join(text for _name, text in sources)
        for needle in FORBIDDEN:
            self.assertNotIn(needle, blob, needle)
        client = (WEB / "product-api.js").read_text()
        for route in ("/me", "/files", "/drafts", "/rounds", "/usage", "/rules"):
            self.assertIn(route, client)
        self.assertIn("product.usage.list", (WEB / "usage.js").read_text())
        self.assertIn("/usage", client)

    def test_pc_shell_and_usage_page_exist(self):
        workbench = (WEB / "index.html").read_text()
        history = (WEB / "audit-files.html").read_text()
        usage = (WEB / "usage.html").read_text()
        for page in (workbench, history, usage):
            self.assertIn('data-testid="pc-shell"', page)
            self.assertIn('href="./audit-files"', page)
            self.assertIn('href="./usage"', page)
            self.assertNotIn("audit-files.html", page)
            self.assertNotIn("usage.html", page)
        self.assertIn('data-testid="open-history"', workbench)
        self.assertIn('data-testid="file-preview"', workbench)
        self.assertIn('data-testid="review-panel"', workbench)
        self.assertIn('data-testid="file-version-list"', workbench)
        self.assertIn('data-testid="result-summary"', workbench)
        self.assertIn('data-testid="round-history-list"', workbench)
        self.assertIn('data-testid="usage-list"', usage)
        self.assertIn('data-testid="history-list"', history)
        self.assertIn('data-testid="history-search"', history)
        app = (WEB / "app.js").read_text()
        self.assertIn('data-testid="open-original"', app)
        self.assertIn('data-testid="finding-suggestion"', app)
        self.assertIn('data-testid="finding-evidence"', app)
        self.assertIn("openPreview", app)
        history_js = (WEB / "audit-files.js").read_text()
        self.assertIn("./?round=", history_js)
        self.assertIn("history-version", history_js)
        self.assertIn("history-result", history_js)
        self.assertIn("result_counts", history_js)
        addon = (WEB / "addon.html").read_text()
        self.assertNotIn("pc-shell", addon)
