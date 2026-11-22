"""Deep toolchain pin; clean distribution correctness is validated separately in CI."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_deep_toolchain_version_matches_runtime_identity(self):
        from audit.policy import PI_VERSION

        package = json.loads((ROOT / "deploy/deep/package.json").read_text())
        lock = json.loads((ROOT / "deploy/deep/package-lock.json").read_text())
        name = "@earendil-works/pi-coding-agent"
        self.assertEqual(package["dependencies"][name], PI_VERSION)
        self.assertEqual(lock["packages"][""]["dependencies"][name], PI_VERSION)
        for key, entry in lock["packages"].items():
            if key.startswith("node_modules/" + name):
                with self.subTest(package=key):
                    self.assertEqual(entry["version"], PI_VERSION)
