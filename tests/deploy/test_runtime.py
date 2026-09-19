"""Live checks against a `python tools/dev.py deploy` compose stack."""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]

from tools.paths import git_sha


def _get(path: str) -> tuple[int, dict | str]:
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=5) as response:
        body = response.read().decode()
        try:
            return response.status, json.loads(body)
        except json.JSONDecodeError:
            return response.status, body


class DeployRuntimeTests(unittest.TestCase):
    def test_api_health_version_and_ready_use_the_image_sha(self):
        sha = os.environ.get("PPAP_BUILD_SHA") or git_sha()
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

    def test_web_serves_the_workbench(self):
        with urllib.request.urlopen("http://127.0.0.1:4173/", timeout=5) as response:
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn("<", body)
