"""Thin black-box checks against a docker compose production stack."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _sha() -> str:
    forced = os.environ.get("PPAP_BUILD_SHA", "").strip()
    if forced:
        return forced
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _get(path: str) -> tuple[int, dict | str]:
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=5) as response:
        body = response.read().decode()
        try:
            return response.status, json.loads(body)
        except json.JSONDecodeError:
            return response.status, body


def _page(path: str) -> tuple[int, str]:
    with urllib.request.urlopen("http://127.0.0.1:4173" + path, timeout=5) as response:
        return response.status, response.read().decode()


class DeployRuntimeTests(unittest.TestCase):
    def test_api_health_version_and_ready_use_the_image_sha(self):
        sha = _sha()
        status, health = _get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "alive")
        status, version = _get("/version")
        self.assertEqual(status, 200)
        self.assertEqual(version["git_sha"], sha)
        status, ready = _get("/ready")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["git_sha"], sha)
        self.assertEqual(ready["postgres"]["state"], "ok")
        self.assertEqual(ready["object_storage"]["state"], "ok")

    def test_web_serves_the_workbench_and_customer_aliases(self):
        status, body = _page("/")
        self.assertEqual(status, 200)
        self.assertIn('id="app"', body)
        self.assertIn("PPAP Audit", body)
        for path in ("/audit-files", "/usage", "/rules"):
            status, page = _page(path)
            self.assertEqual(status, 200)
            self.assertIn('id="app"', page)
        status, addon = _page("/addon")
        self.assertEqual(status, 200)
        self.assertIn("PPAP Audit", addon)
        self.assertIn('id="addon-root"', addon)
        status, config_body = _page("/runtime-config.json")
        self.assertEqual(status, 200)
        config = json.loads(config_body)
        self.assertEqual(config["api_url"], "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()
