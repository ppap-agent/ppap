"""Isolated-dev static web aliases for baseline customer paths."""
from __future__ import annotations

import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from tools.web_server import Handler, aliased_path

WEB = ROOT / "apps" / "web"


class WebAliasTests(unittest.TestCase):
    def test_aliased_paths_match_baseline_customer_entries(self):
        self.assertEqual(aliased_path("/audit-files"), "/audit-files.html")
        self.assertEqual(aliased_path("/audit-files?round=1"), "/audit-files.html")
        self.assertEqual(aliased_path("/addon"), "/addon.html")
        self.assertEqual(aliased_path("/index.html"), "/index.html")
        self.assertEqual(aliased_path("/"), "/")

    def test_serves_audit_files_without_html_suffix(self):
        def factory(*args, **kwargs):
            return Handler(*args, directory=str(WEB), **kwargs)

        server = ThreadingHTTPServer(("127.0.0.1", 0), factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/audit-files") as response:
                body = response.read().decode()
                status = response.status
            self.assertEqual(status, 200)
            self.assertIn("历史审核记录", body)
            self.assertIn('data-testid="history-list"', body)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/addon") as response:
                addon = response.read().decode()
            self.assertIn("PPAP Audit", addon)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/audit-files/") as response:
                self.assertEqual(response.geturl().rstrip("/").endswith("/audit-files"), True)
                self.assertIn("历史审核记录", response.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_unknown_path_is_not_aliased(self):
        def factory(*args, **kwargs):
            return Handler(*args, directory=str(WEB), **kwargs)

        server = ThreadingHTTPServer(("127.0.0.1", 0), factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/missing")
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
