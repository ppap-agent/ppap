"""Customer path aliases are owned by the nginx config, not a Python server."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = (ROOT / "deploy" / "web" / "nginx.conf").read_text()


class WebAliasTests(unittest.TestCase):
    def test_aliased_paths_match_baseline_customer_entries(self):
        self.assertIn("location = /audit-files", CONF)
        self.assertIn("location = /audit-files {\n        try_files /index.html =404", CONF)
        self.assertIn("location = /addon", CONF)
        self.assertIn("try_files /addon.html =404", CONF)
        self.assertIn("location = /usage", CONF)
        self.assertIn("location = /usage {\n        try_files /index.html =404", CONF)
        self.assertIn("location = /rules", CONF)
        self.assertIn("location = /rules {\n        try_files /index.html =404", CONF)
        self.assertNotIn("location = /v2", CONF)
        self.assertNotIn("try_files $uri /index.html", CONF)

    def test_directory_aliases_redirect_without_html_suffix(self):
        self.assertIn("location = /audit-files/", CONF)
        self.assertIn("return 301 /audit-files$is_args$args", CONF)
        self.assertIn("location = /addon/", CONF)
        self.assertIn("return 301 /addon$is_args$args", CONF)
        self.assertIn("location = /usage/", CONF)
        self.assertIn("return 301 /usage$is_args$args", CONF)
        self.assertIn("return 301 /rules$is_args$args", CONF)

    def test_unknown_path_is_not_rewritten_to_the_workbench(self):
        self.assertIn("try_files $uri $uri/ =404", CONF)


if __name__ == "__main__":
    unittest.main()
