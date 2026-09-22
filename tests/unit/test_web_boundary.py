"""The single React client talks only to the Product API."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"


class WebProductBoundaryTests(unittest.TestCase):
    def test_react_client_only_uses_product_api(self):
        sources = [path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".tsx"}]
        self.assertTrue(sources)
        blob = "\n".join(sources)
        for forbidden in ("/uac/", "transformer/file-audit", "@insforge/sdk", "postgrest", "/rest/v1", "ppap_documents", "ppap_jobs", "insforge.dev"):
            self.assertNotIn(forbidden, blob)
        client = (WEB / "src/api.ts").read_text()
        self.assertIn("credentials: 'include'", client)
        self.assertIn("runtime-config.json", client)
        for route in ("/me", "/drafts", "/rounds", "/usage", "/rules", "/file-analyses"):
            self.assertIn(route, blob)

    def test_one_react_interface_preserves_customer_routes(self):
        shell = (WEB / "src/App.tsx").read_text()
        for route in ('/', '/audit-files', '/rules', '/usage'):
            self.assertIn(f"path: '{route}'", shell)
        self.assertIn("href={link.path}", shell)
        self.assertNotIn("/v2", shell)
        self.assertIn("createRoot", (WEB / "src/main.tsx").read_text())
        self.assertIn('type="module"', (WEB / "index.html").read_text())
        for obsolete in ("app.js", "audit-files.html", "rules.html", "usage.html", "product-api.js"):
            self.assertFalse((WEB / obsolete).exists())
        self.assertFalse(any((WEB / "src").rglob("*.vue")))
        self.assertIn("COPY --from=web-build /app/dist", (ROOT / "deploy/web/Dockerfile").read_text())

    def test_rule_library_is_readable_and_does_not_add_an_execution_language(self):
        rules = (WEB / "src/RulesView.tsx").read_text()
        for content in ("item.rule_to_check", "sourceFile", "predecessor", "/rules/parse", "保存规则"):
            self.assertIn(content, rules)
        self.assertNotIn("eval(", rules)
        self.assertNotIn("new Function", rules)
        self.assertIn("明确标为未覆盖", rules)

    def test_production_ui_hides_deep_unless_offered(self):
        workbench = (WEB / "src/Workbench.tsx").read_text()
        api = (WEB / "src/api.ts").read_text()
        self.assertIn("deepOffered", workbench)
        self.assertIn("deep_offered", api)
        self.assertIn('deepOffered && <label className="mode-select">', workbench)

    def test_usage_page_does_not_report_real_usage(self):
        usage = (WEB / "src/UsageView.tsx").read_text()
        self.assertIn("/usage?", usage)
        self.assertIn("/integrations/feishu/usage/quota", usage)
        self.assertIn("无法读取 Partner AI", usage)
        self.assertNotIn("/report", usage)

    def test_clients_do_not_own_the_audit_cost_rate(self):
        addon = (WEB / "addon.js").read_text()
        workbench = (WEB / "src/Workbench.tsx").read_text()
        usage = (WEB / "src/UsageView.tsx").read_text()
        self.assertNotIn("AUDIT_COST_PER_FILE", addon + workbench + usage)
        self.assertNotIn("* 500", addon)
        self.assertIn("target_count", addon)
        self.assertIn("accept_allowed", addon)
        self.assertIn("target_count", workbench)
        self.assertIn("accept_allowed", workbench)
        self.assertIn("writeback", workbench)

    def test_shared_helpers_keep_review_excerpt_and_workspace_rules(self):
        helpers = (WEB / "src/helpers.ts").read_text()
        for marker in ("latestRules", "matchesFilter", "parseExcerptCells", "clampPreviewWidth", "workspaceStorageKey", "isSafeReturnTo"):
            self.assertIn(marker, helpers)
        self.assertIn("ppap-view:", helpers)
