"""The single Vue client talks only to the Product API."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"


class WebProductBoundaryTests(unittest.TestCase):
    def test_vue_client_only_uses_product_api(self):
        sources = [path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".vue"}]
        self.assertTrue(sources)
        blob = "\n".join(sources)
        for forbidden in ("/uac/", "transformer/file-audit", "@insforge/sdk", "postgrest", "/rest/v1", "ppap_documents", "ppap_jobs", "insforge.dev"):
            self.assertNotIn(forbidden, blob)
        client = (WEB / "src/api.ts").read_text()
        self.assertIn("credentials: 'include'", client)
        self.assertIn("runtime-config.json", client)
        for route in ("/me", "/drafts", "/rounds", "/usage", "/rules", "/file-analyses"):
            self.assertIn(route, blob)

    def test_one_vue_interface_preserves_customer_routes(self):
        shell = (WEB / "src/App.vue").read_text()
        for route in ('/', '/audit-files', '/rules', '/usage'):
            self.assertIn(f"path:'{route}'", shell)
        self.assertIn(':href="link.path"', shell)
        self.assertNotIn("/v2", shell)
        self.assertIn("createApp(App)", (WEB / "src/main.ts").read_text())
        self.assertIn('type="module"', (WEB / "index.html").read_text())
        for obsolete in ("app.js", "audit-files.html", "rules.html", "usage.html", "product-api.js"):
            self.assertFalse((WEB / obsolete).exists())
        self.assertIn("COPY --from=web-build /app/dist", (ROOT / "deploy/web/Dockerfile").read_text())

    def test_rule_library_is_readable_and_does_not_add_an_execution_language(self):
        rules = (WEB / "src/RulesView.vue").read_text()
        for content in ("item.rule_to_check", "sourceFile", "predecessor", "/rules/parse", "保存规则"):
            self.assertIn(content, rules)
        self.assertNotIn("eval(", rules)
        self.assertNotIn("new Function", rules)
        self.assertIn("不会自动改变内置审核程序", rules)

    def test_usage_page_does_not_report_real_usage(self):
        usage = (WEB / "src/UsageView.vue").read_text()
        self.assertIn("/usage?", usage)
        self.assertNotIn("/report", usage)
