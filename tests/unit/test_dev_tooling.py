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
sys.path[:0] = [str(ROOT), str(ROOT / "packages")]

from tools.dev import cmd_build, cmd_quality
from tools.migrate import latest_version, migration_versions, previous_version
from tools.paths import PYTHONPATH_PARTS, git_sha, pythonpath
from tools.stack import pythonpath as stack_pythonpath
from tools.web_server import write_runtime_config


def _compose_services(compose: str) -> dict[str, str]:
    services: dict[str, list[str]] = {}
    current = ""
    in_services = False
    for line in compose.splitlines():
        if line.startswith("services:"):
            in_services = True
            continue
        if not in_services:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            current = line.strip().rstrip(":")
            services[current] = []
            continue
        if current:
            services[current].append(line)
    return {name: "\n".join(body) for name, body in services.items()}


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

    def test_compose_fixture_is_production_and_not_isolated_dev(self):
        from ppap_core.settings import ISOLATED_DEV_S3_ENDPOINT, ISOLATED_DEV_SECRET, Settings

        env_text = (ROOT / "deploy" / "compose.env.example").read_text()
        compose = (ROOT / "deploy" / "compose.yaml").read_text()
        self.assertNotIn(ISOLATED_DEV_SECRET, env_text)
        self.assertNotIn(ISOLATED_DEV_S3_ENDPOINT, env_text)
        self.assertIn("PPAP_PROFILE=production", env_text)
        self.assertIn("http://minio:9000", env_text)
        self.assertIn("ppap-api:${PPAP_BUILD_SHA}", compose)
        self.assertIn("ppap-worker:${PPAP_BUILD_SHA}", compose)
        self.assertIn("ppap-web:${PPAP_BUILD_SHA}", compose)
        self.assertIn("python tools/dev.py provision", compose)
        self.assertIn("context: minio", compose)
        self.assertNotIn("minio/minio:", compose)
        self.assertNotIn("context: deploy/minio", compose)
        dockerfile = (ROOT / "deploy" / "minio" / "Dockerfile").read_text()
        from tools.stack import MINIO_RELEASE, MINIO_SHA256

        self.assertIn(MINIO_RELEASE, dockerfile)
        self.assertIn(MINIO_SHA256, dockerfile)

        services = _compose_services(compose)
        self.assertIn("compose.env.example", services["init"])
        self.assertNotIn("env_file:", services["api"])
        self.assertNotIn("env_file:", services["worker"])
        self.assertIn("PPAP_PG_API_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", services["api"])
        self.assertIn("PPAP_PG_WORKER_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_API_PASSWORD", services["worker"])

        parsed: dict[str, str] = {}
        for line in env_text.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, value = raw.partition("=")
            parsed[key] = value
        with patch.dict(os.environ, parsed, clear=False):
            settings = Settings()
        self.assertEqual(settings.profile, "production")
        self.assertTrue(all(
            getattr(settings, name) != ISOLATED_DEV_SECRET
            for name in ("pg_api_password", "pg_worker_password", "pg_migrator_password", "pg_bootstrap_password")
        ))
        self.assertNotEqual(settings.s3_endpoint, ISOLATED_DEV_S3_ENDPOINT)

    def test_backup_restore_rollback_are_canonical_commands(self):
        from tools.dev import _sql_ident, cmd_restore, cmd_rollback, latest_backup

        source = (ROOT / "tools" / "dev.py").read_text()
        self.assertIn('"backup"', source)
        self.assertIn('"restore"', source)
        self.assertIn('"rollback"', source)
        self.assertEqual(_sql_ident("ppap"), "ppap")
        with self.assertRaises(ValueError):
            _sql_ident("ppap;drop")
        self.assertEqual(cmd_restore([]), 1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "20260101T000000Z"
            new = root / "20260102T000000Z"
            old.mkdir()
            new.mkdir()
            (old / "manifest.json").write_text("{}\n")
            (new / "manifest.json").write_text("{}\n")
            with patch("tools.dev.BACKUP_ROOT", root):
                self.assertEqual(latest_backup(), new)
            with patch("tools.dev.BACKUP_ROOT", root), patch("tools.dev.shutil.which", return_value="/usr/bin/docker"):
                self.assertEqual(cmd_rollback(), 1)


if __name__ == "__main__":
    unittest.main()
