"""The single React client talks only to the Product API."""
import re
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

    def test_single_react_entry_keeps_auth_and_product_boundaries(self):
        sources = "\n".join(path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".tsx"})
        self.assertNotIn("/v2", sources)
        self.assertIn("createRoot", (WEB / "src/main.tsx").read_text())
        html = (WEB / "index.html").read_text()
        self.assertIn('type="module"', html)
        self.assertIn("h5-js-sdk", html)
        for required in ("/login/feishu/host", "/login/feishu?return_to=", "isFeishuHost", "/enter-legacy", "tenant_not_allowed"):
            self.assertIn(required, sources)
        self.assertNotIn("仅海岸线", sources)
        self.assertNotIn("X-Token", sources)
        self.assertNotIn("/uac/", sources)
        for obsolete in ("app.js", "audit-files.html", "rules.html", "usage.html", "product-api.js"):
            self.assertFalse((WEB / obsolete).exists())
        self.assertFalse(any((WEB / "src").rglob("*.vue")))
        self.assertIn("COPY --from=web-build /app/dist", (ROOT / "deploy/web/Dockerfile").read_text())
        self.assertNotIn("vite.config.ts", (WEB / "tsconfig.json").read_text())

    def test_rule_ui_does_not_add_an_execution_language(self):
        rules = "\n".join(path.read_text() for path in (WEB / "src/rules").rglob("*.tsx"))
        self.assertTrue(rules)
        self.assertNotIn("eval(", rules)
        self.assertNotIn("new Function", rules)

    def test_deep_mode_control_is_gated_by_public_config(self):
        sources = "\n".join(path.read_text() for path in (WEB / "src").rglob("*.tsx"))
        api = (WEB / "src/api.ts").read_text()
        self.assertIn("deep_offered", api)
        self.assertRegex(sources, re.compile(r"deepOffered\s*&&[\s\S]{0,300}data-testid=\"analysis-mode\""))

    def test_web_usage_is_read_only(self):
        sources = "\n".join(path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".tsx"})
        self.assertIn("/usage?", sources)
        self.assertIn("/integrations/feishu/usage/quota", sources)
        self.assertNotIn("/integrations/feishu/usage/report", sources)

    def test_clients_do_not_own_the_audit_cost_rate(self):
        addon = (WEB / "src/addon/main.tsx").read_text()
        sources = "\n".join(path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".tsx"})
        self.assertNotIn("AUDIT_COST_PER_FILE", addon + sources)
        self.assertNotIn("* 500", addon)
        self.assertIn("target_count", addon + sources)
        self.assertIn("accept_allowed", addon + sources)

    def test_shared_host_edition_proxy_has_no_v2_prefix(self):
        conf = (ROOT / "deploy" / "tke" / "edition-proxy.conf").read_text()
        self.assertIn("ppap_edition", conf)
        self.assertIn("/enter-current", conf)
        self.assertIn("/enter-legacy", conf)
        self.assertIn("ppap-current-api:8080/enter-current", conf)
        self.assertIn("ppap-current-api:8080/enter-legacy", conf)
        self.assertNotIn("Set-Cookie", conf)
        self.assertNotIn("切换到新版", conf)
        self.assertIn("/ppap/", conf)
        self.assertIn("/addon", conf)
        self.assertNotIn("/v2", conf)
        self.assertIn("web-base.ppap-test.svc.cluster.local", conf)
        self.assertNotIn("ppap-agent.xixifusi.online", conf)

    def test_shared_helpers_keep_review_excerpt_and_workspace_rules(self):
        helpers = (WEB / "src/helpers.ts").read_text()
        for marker in ("latestRules", "matchesFilter", "parseExcerptCells", "workspaceStorageKey", "isSafeReturnTo"):
            self.assertIn(marker, helpers)
        self.assertIn("ppap-view:", helpers)
