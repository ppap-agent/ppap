"""Canonical install/quality/test/build entry."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]

from tools.dev import cmd_build, cmd_quality
from tools.migrate import latest_version, migration_versions, previous_version
from tools.paths import PYTHONPATH_PARTS, git_sha, pythonpath
from tools.stack import pythonpath as stack_pythonpath
from tools.web_server import write_runtime_config


class DevToolingTests(unittest.TestCase):
    def test_pythonpath_is_shared_with_stack(self):
        self.assertEqual(pythonpath(), stack_pythonpath())
        self.assertTrue(any(str(path).endswith("ppap_excel_audit_engine/src") for path in PYTHONPATH_PARTS))

    def test_quality_compiles_canonical_tools(self):
        with patch.dict(os.environ, {"PPAP_BUILD_SHA": "a" * 40}):
            self.assertEqual(cmd_quality(), 0)

    def test_migrations_are_ordered_sql_files(self):
        versions = migration_versions()
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(previous_version(), versions[-2])
        self.assertEqual(latest_version(), versions[-1])
        self.assertTrue((ROOT / "migrations" / latest_version()).is_file())

    def test_build_writes_identity_and_web_for_the_git_revision(self):
        sha = "b" * 40
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            with patch("tools.dev.DIST", dist), patch("tools.dev.git_sha", return_value=sha):
                self.assertEqual(cmd_build(), 0)
            identity = json.loads((dist / "identity.json").read_text())
            self.assertEqual(identity["git_sha"], sha)
            self.assertEqual(identity["images"], {})
            self.assertTrue((dist / "web" / "index.html").is_file())
            self.assertTrue((dist / "web" / "addon.html").is_file())
            self.assertTrue((dist / "web" / "audit-files.html").is_file())
            self.assertEqual(json.loads((dist / "web" / "build-identity.json").read_text())["git_sha"], sha)
            self.assertFalse((dist / "web" / "runtime-config.json").exists())

    def test_web_runtime_config_comes_from_public_api_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            with patch.dict(os.environ, {"PPAP_PUBLIC_API_URL": "https://api.example/"}):
                write_runtime_config(dest)
            self.assertEqual(json.loads((dest / "runtime-config.json").read_text())["api_url"], "https://api.example")

    def test_forced_build_sha_wins(self):
        with patch.dict(os.environ, {"PPAP_BUILD_SHA": "c" * 40}):
            self.assertEqual(git_sha(), "c" * 40)

    def test_requirements_match_pyproject_pins(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        pins = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        expected = list(project["project"]["dependencies"]) + list(project["project"]["optional-dependencies"]["dev"])
        self.assertEqual(pins, expected)

    def test_setuptools_finds_product_packages(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        cfg = project["tool"]["setuptools"]["packages"]["find"]
        self.assertFalse(cfg.get("namespaces", True))
        self.assertIn("packages", cfg["where"])
        self.assertIn("services/api", cfg["where"])
        self.assertIn("ppap_core*", cfg["include"])
        self.assertIn("ppap_excel_audit_engine*", cfg["include"])

    def test_build_records_image_tags_for_the_same_revision(self):
        sha = "d" * 40
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            with (
                patch("tools.dev.DIST", dist),
                patch("tools.dev.git_sha", return_value=sha),
                patch.dict(os.environ, {"PPAP_BUILD_IMAGES": "1"}),
                patch("tools.dev.shutil.which", return_value="/usr/bin/docker"),
                patch("tools.dev.run", return_value=0) as run,
            ):
                self.assertEqual(cmd_build(), 0)
            identity = json.loads((dist / "identity.json").read_text())
            self.assertEqual(
                identity["images"],
                {"api": f"ppap-api:{sha}", "worker": f"ppap-worker:{sha}", "web": f"ppap-web:{sha}"},
            )
            self.assertEqual(run.call_count, 3)
            first = run.call_args_list[0].args[0]
            self.assertIn(f"PPAP_BUILD_SHA={sha}", first)
            self.assertIn("PPAP_SERVICE=api", first)
            self.assertIn("PPAP_SERVICE=worker", run.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
