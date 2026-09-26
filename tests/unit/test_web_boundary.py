"""Repository/product boundaries: the web client talks only to the Product API.

Frontend helper, UI, and browser behavior live in apps/web native tests.
This module only scans shipped sources for contracts Python still owns:
no InsForge/PostgREST/legacy APIs, no client-owned cost rate, and the
web image is the Vite dist.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"


def _web_sources() -> str:
    return "\n".join(path.read_text() for path in (WEB / "src").rglob("*") if path.suffix in {".ts", ".tsx"})


class WebProductBoundaryTests(unittest.TestCase):
    def test_react_client_only_uses_product_api(self):
        blob = _web_sources()
        self.assertTrue(blob)
        for forbidden in (
            "/uac/",
            "transformer/file-audit",
            "@insforge/sdk",
            "postgrest",
            "/rest/v1",
            "ppap_documents",
            "ppap_jobs",
            "insforge.dev",
            "X-Token",
            "仅海岸线",
        ):
            self.assertNotIn(forbidden, blob)
        self.assertNotIn("/v2", blob)
        self.assertFalse(any((WEB / "src").rglob("*.vue")))
        for obsolete in ("app.js", "audit-files.html", "rules.html", "usage.html", "product-api.js"):
            self.assertFalse((WEB / obsolete).exists())
        self.assertIn("COPY --from=web-build /app/dist", (ROOT / "deploy/web/Dockerfile").read_text())

    def test_web_usage_is_read_only(self):
        blob = _web_sources()
        self.assertIn("/usage?", blob)
        self.assertIn("/integrations/feishu/usage/quota", blob)
        self.assertNotIn("/integrations/feishu/usage/report", blob)

    def test_clients_do_not_own_the_audit_cost_rate(self):
        blob = _web_sources()
        self.assertNotIn("AUDIT_COST_PER_FILE", blob)
        self.assertNotIn("* 500", blob)
        self.assertIn("target_count", blob)
        self.assertIn("accept_allowed", blob)


if __name__ == "__main__":
    unittest.main()
