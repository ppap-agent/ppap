"""Canonical install/quality/test/build entry."""
from __future__ import annotations

import json
import os
import re
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


_COMPOSE_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(:-([^}]*))?\}")


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        values[key] = value
    return values


def _service_environment(body: str) -> dict[str, str]:
    env: dict[str, str] = {}
    in_env = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == "environment:":
            in_env = True
            continue
        if not in_env:
            continue
        if line.startswith("    ") and not line.startswith("      ") and stripped.endswith(":"):
            break
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        env[key] = raw.strip().strip('"').strip("'")
    return env


def _interpolate(value: str, values: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        has_default = match.group(2) is not None
        default = match.group(3) or ""
        held = values.get(name)
        if held:
            return held
        if has_default:
            return default
        return held or ""

    return _COMPOSE_VAR.sub(repl, value)


def _interpolated_environment(body: str, values: dict[str, str]) -> dict[str, str]:
    return {key: _interpolate(raw, values) for key, raw in _service_environment(body).items()}


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
            self.assertTrue((dist / "web" / "usage.html").is_file())
            self.assertTrue((dist / "web" / "product-api.js").is_file())
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
        self.assertIn("LOG_FORMAT=json", env_text)
        self.assertIn("ppap-api:${PPAP_BUILD_SHA}", compose)
        self.assertIn("ppap-worker:${PPAP_BUILD_SHA}", compose)
        self.assertIn("ppap-web:${PPAP_BUILD_SHA}", compose)
        self.assertIn("python tools/dev.py provision", compose)
        self.assertNotIn("minio/minio:", compose)
        self.assertNotIn("ppap-minio:", compose)
        self.assertNotIn("context: minio", compose)
        self.assertFalse((ROOT / "deploy" / "minio" / "Dockerfile").exists())
        app_dockerfile = (ROOT / "deploy" / "app" / "Dockerfile").read_text()
        self.assertIn("postgresql-client", app_dockerfile)
        from tools.stack import SILO_ARCHIVE_SHA256, SILO_IMAGE, SILO_RELEASE

        self.assertIn(SILO_IMAGE, compose)
        self.assertIn(SILO_RELEASE, compose)
        self.assertIn('command: ["server", "/data", "--console-address", ":9001"]', compose)
        self.assertIn("MINIO_ROOT_USER", compose)
        self.assertNotIn("distroless", compose)
        self.assertNotIn("pgsty/silo:latest", compose)
        isolated = (ROOT / "deploy" / "isolated-dev" / "compose.yaml").read_text()
        self.assertIn(SILO_IMAGE, isolated)
        self.assertNotIn("minio/minio:", isolated)
        self.assertEqual(len(SILO_ARCHIVE_SHA256), 64)

        services = _compose_services(compose)
        self.assertIn("silo", services)
        self.assertNotIn("minio", services)
        self.assertIn("${PPAP_COMPOSE_ENV_FILE:-compose.env.example}", services["init"])
        self.assertNotIn("env_file:", services["api"])
        self.assertNotIn("env_file:", services["worker"])
        self.assertIn("PPAP_PG_HOST: ${PPAP_PG_HOST}", services["api"])
        self.assertIn("PPAP_PG_HOST: ${PPAP_PG_HOST}", services["worker"])
        self.assertNotIn("PPAP_PG_HOST: postgres", services["api"])
        self.assertNotIn("PPAP_PG_HOST: postgres", services["worker"])
        self.assertIn("FEISHU_APP_ID", services["api"])
        self.assertIn("FEISHU_APP_SECRET", services["api"])
        self.assertIn("FEISHU_VERIFICATION_TOKEN", services["api"])
        self.assertIn("FEISHU_AI_ITEM_ID", services["api"])
        self.assertNotIn("FEISHU_APP_ID", services["worker"])
        self.assertNotIn("FEISHU_APP_SECRET", services["worker"])
        self.assertIn("PPAP_PG_API_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", services["api"])
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", services["api"])
        self.assertIn("PPAP_PG_WORKER_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", services["worker"])
        self.assertNotIn("PPAP_PG_API_PASSWORD", services["worker"])
        self.assertIn("LOG_FORMAT: json", services["api"])
        self.assertIn("LOG_FORMAT: json", services["worker"])

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
        self.assertEqual(parsed["PPAP_S3_ENDPOINT"], "http://silo:9000")

    def test_compose_selects_explicit_env_file_for_init_and_runtime(self):
        from tools.dev import _compose, _compose_envfile, compose_env_file, compose_file, take_compose_env_args

        fixture = (ROOT / "deploy" / "compose.env.example").resolve()
        self.assertEqual(compose_env_file(), fixture)
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "production.env"
            selected.write_text(fixture.read_text() + "PPAP_WEB_URL=https://app.example\n", encoding="utf-8")
            with patch.dict(os.environ, {"PPAP_COMPOSE_ENV": str(selected)}, clear=False):
                self.assertEqual(compose_env_file(), selected.resolve())
                self.assertEqual(_compose_envfile()["PPAP_WEB_URL"], "https://app.example")
                leftover = take_compose_env_args(["--env-file", str(selected), "dist/backups/x"])
                self.assertEqual(leftover, ["dist/backups/x"])
                with patch("tools.dev.subprocess.call", return_value=0) as call:
                    self.assertEqual(_compose("deadbeef", "up", "-d"), 0)
                    cmd = call.call_args.args[0]
                    env = call.call_args.kwargs["env"]
                self.assertEqual(cmd[cmd.index("--env-file") + 1], str(selected.resolve()))
                self.assertEqual(cmd[cmd.index("-f") + 1], str(compose_file()))
                self.assertNotEqual(Path(cmd[cmd.index("--env-file") + 1]), fixture)
                self.assertEqual(env["PPAP_COMPOSE_ENV_FILE"], str(selected.resolve()))
        self.assertEqual(compose_env_file(), fixture)

    def test_selected_env_drives_pg_host_and_feishu_without_init_secrets(self):
        compose = (ROOT / "deploy" / "compose.yaml").read_text()
        services = _compose_services(compose)
        fixture = _parse_dotenv(ROOT / "deploy" / "compose.env.example")
        selected = dict(fixture)
        selected.update(
            {
                "PPAP_PG_HOST": "db.example.internal",
                "PPAP_PG_PORT": "6543",
                "PPAP_PG_BOOTSTRAP_PASSWORD": "boot-secret",
                "PPAP_PG_MIGRATOR_PASSWORD": "mig-secret",
                "PPAP_PG_API_PASSWORD": "api-secret",
                "PPAP_PG_WORKER_PASSWORD": "worker-secret",
                "FEISHU_APP_ID": "cli_prod",
                "FEISHU_APP_SECRET": "feishu-secret",
                "FEISHU_VERIFICATION_TOKEN": "verify-prod",
                "FEISHU_AI_ITEM_ID": "item-prod",
                "FEISHU_FREE_TRIAL_QUOTA_POINTS": "2500",
                "PPAP_PARTNER_AI_ENDPOINT": "https://partner.example",
            }
        )

        fixture_api = _interpolated_environment(services["api"], fixture)
        fixture_worker = _interpolated_environment(services["worker"], fixture)
        self.assertEqual(fixture_api["PPAP_PG_HOST"], "postgres")
        self.assertEqual(fixture_api["PPAP_PG_PORT"], "5432")
        self.assertEqual(fixture_worker["PPAP_PG_HOST"], "postgres")
        self.assertEqual(fixture_api["FEISHU_APP_ID"], "")

        api = _interpolated_environment(services["api"], selected)
        worker = _interpolated_environment(services["worker"], selected)
        self.assertEqual(api["PPAP_PG_HOST"], "db.example.internal")
        self.assertEqual(api["PPAP_PG_PORT"], "6543")
        self.assertEqual(worker["PPAP_PG_HOST"], "db.example.internal")
        self.assertEqual(worker["PPAP_PG_PORT"], "6543")
        self.assertEqual(api["PPAP_PG_API_PASSWORD"], "api-secret")
        self.assertEqual(worker["PPAP_PG_WORKER_PASSWORD"], "worker-secret")
        self.assertEqual(api["FEISHU_APP_ID"], "cli_prod")
        self.assertEqual(api["FEISHU_APP_SECRET"], "feishu-secret")
        self.assertEqual(api["FEISHU_VERIFICATION_TOKEN"], "verify-prod")
        self.assertEqual(api["FEISHU_AI_ITEM_ID"], "item-prod")
        self.assertEqual(api["FEISHU_FREE_TRIAL_QUOTA_POINTS"], "2500")
        self.assertNotIn("FEISHU_APP_ID", worker)
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", api)
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", api)
        self.assertNotIn("PPAP_PG_WORKER_PASSWORD", api)
        self.assertNotIn("PPAP_PG_BOOTSTRAP_PASSWORD", worker)
        self.assertNotIn("PPAP_PG_MIGRATOR_PASSWORD", worker)
        self.assertNotIn("PPAP_PG_API_PASSWORD", worker)
        self.assertNotIn("FEISHU_APP_SECRET", worker)
        self.assertNotIn("PADDLE_TOKEN", api)
        self.assertNotIn("OCR_OPENAI_API_KEY", api)

        self.assertIn("${PPAP_COMPOSE_ENV_FILE:-compose.env.example}", services["init"])
        self.assertNotIn("PPAP_PG_HOST:", services["init"])
        self.assertEqual(selected["PPAP_PG_HOST"], api["PPAP_PG_HOST"])
        self.assertEqual(selected["PPAP_PG_PORT"], worker["PPAP_PG_PORT"])
        self.assertEqual(selected["PPAP_PG_BOOTSTRAP_PASSWORD"], "boot-secret")
        self.assertEqual(selected["PPAP_PG_MIGRATOR_PASSWORD"], "mig-secret")

    def test_backup_restore_rollback_are_canonical_commands(self):
        from tools.dev import _sql_ident, cmd_restore, cmd_rollback, latest_backup

        source = (ROOT / "tools" / "dev.py").read_text()
        self.assertIn('"backup"', source)
        self.assertIn('"restore"', source)
        self.assertIn('"rollback"', source)
        self.assertIn("--no-deps", source)
        self.assertIn('OBJECT_STORE_SERVICE = "silo"', source)
        self.assertNotIn('exec", "-T", "minio"', source)
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
