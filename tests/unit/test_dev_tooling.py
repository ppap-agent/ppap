"""Deploy contracts: compose interpolation, bake tags, nginx aliases, SQL migrations."""
from __future__ import annotations

import os
import re
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SILO_IMAGE = (
    "docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z"
    "@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46"
)
SILO_RELEASE = "RELEASE.2026-09-16T00-00-00Z"


def migration_files() -> list[Path]:
    return sorted(path for path in (ROOT / "migrations").glob("*.sql") if path.name[:4].isdigit())


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


class DeployContractTests(unittest.TestCase):
    def test_tools_orchestration_directory_is_gone(self):
        self.assertFalse((ROOT / "tools").exists())

    def test_migrations_are_ordered_sql_files(self):
        versions = [path.name for path in migration_files()]
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(versions, sorted(versions))
        apply = (ROOT / "migrations" / "apply.sql").read_text()
        for name in versions:
            self.assertIn(f"\\ir {name}", apply)
            self.assertIn(
                f"INSERT INTO product.schema_migrations (version) VALUES ('{name}')",
                (ROOT / "migrations" / name).read_text(),
            )
        self.assertTrue((ROOT / "migrations" / "bootstrap.sql").is_file())

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

    def test_bake_tags_api_worker_web_with_the_same_sha(self):
        bake = (ROOT / "docker-bake.hcl").read_text()
        self.assertIn('targets = ["api", "worker", "web"]', bake)
        self.assertIn("ppap-api:${PPAP_BUILD_SHA}", bake)
        self.assertIn("ppap-worker:${PPAP_BUILD_SHA}", bake)
        self.assertIn("ppap-web:${PPAP_BUILD_SHA}", bake)
        self.assertIn("PPAP_SERVICE   = \"api\"", bake)
        self.assertIn("PPAP_SERVICE   = \"worker\"", bake)
        app = (ROOT / "deploy" / "app" / "Dockerfile").read_text()
        self.assertNotIn("COPY tools", app)
        self.assertNotIn("postgresql-client", app)
        web = (ROOT / "deploy" / "web" / "Dockerfile").read_text()
        self.assertIn("FROM nginx:1.27-alpine", web)
        self.assertNotIn("web_server.py", web)
        self.assertIn("build-identity.json", web)

    def test_nginx_aliases_match_baseline_customer_entries(self):
        conf = (ROOT / "deploy" / "web" / "nginx.conf").read_text()
        self.assertIn("listen 4173", conf)
        self.assertIn("location = /audit-files", conf)
        self.assertIn("try_files /audit-files.html =404", conf)
        self.assertIn("location = /addon", conf)
        self.assertIn("try_files /addon.html =404", conf)
        self.assertIn("location = /usage", conf)
        self.assertIn("try_files /usage.html =404", conf)
        self.assertIn("return 301 /audit-files$is_args$args", conf)
        self.assertIn("try_files $uri $uri/ =404", conf)
        entry = (ROOT / "deploy" / "web" / "entrypoint.sh").read_text()
        self.assertIn("runtime-config.json", entry)
        self.assertIn("PPAP_PUBLIC_API_URL", entry)

    def test_web_entrypoint_writes_runtime_config_from_public_api_url(self):
        script = (ROOT / "deploy" / "web" / "entrypoint.sh").read_text()
        self.assertIn('printf \'{"api_url":"%s"}\\n\' "$api"', script)

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
        self.assertIn("bootstrap.sql", compose)
        self.assertIn("apply.sql", compose)
        self.assertNotIn("python tools/", compose)
        self.assertNotIn("minio/minio:", compose)
        self.assertNotIn("ppap-minio:", compose)
        self.assertFalse((ROOT / "deploy" / "minio" / "Dockerfile").exists())
        self.assertIn(SILO_IMAGE, compose)
        self.assertIn(SILO_RELEASE, compose)
        self.assertIn('command: ["server", "/data", "--console-address", ":9001"]', compose)
        self.assertIn("MINIO_ROOT_USER", compose)
        self.assertNotIn("distroless", compose)
        self.assertNotIn("pgsty/silo:latest", compose)
        isolated = (ROOT / "deploy" / "isolated-dev" / "compose.yaml").read_text()
        self.assertIn(SILO_IMAGE, isolated)
        self.assertNotIn("minio/minio:", isolated)
        self.assertIn("init-db", isolated)
        self.assertIn("ppap-api:", isolated)

        services = _compose_services(compose)
        self.assertIn("silo", services)
        self.assertNotIn("minio", services)
        self.assertIn("init-db", services)
        self.assertIn("init-bucket", services)
        self.assertNotIn("init", services)
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
        self.assertIn("PPAP_PG_BOOTSTRAP_PASSWORD", services["init-db"])
        self.assertIn("PPAP_PG_MIGRATOR_PASSWORD", services["init-db"])
        self.assertNotIn("PPAP_PG_API_PASSWORD", services["init-bucket"])

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
        init_db = _interpolated_environment(services["init-db"], selected)
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
        self.assertEqual(init_db["PGHOST"], "db.example.internal")
        self.assertEqual(init_db["PGPASSWORD"], "boot-secret")
        self.assertEqual(init_db["PPAP_PG_MIGRATOR_PASSWORD"], "mig-secret")
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
        self.assertEqual(selected["PPAP_PG_HOST"], api["PPAP_PG_HOST"])
        self.assertEqual(selected["PPAP_PG_PORT"], worker["PPAP_PG_PORT"])

    def test_init_waits_for_ready_postgres_and_silo(self):
        paths = [
            ROOT / "deploy" / "compose.yaml",
            ROOT / "deploy" / "isolated-dev" / "compose.yaml",
            ROOT / "deploy" / "insforge" / "compose.yaml",
        ]
        for path in paths:
            services = _compose_services(path.read_text())
            if "postgres" in services:
                self.assertIn("/proc/1/comm", services["postgres"])
            self.assertIn("minio/health/live", services["silo"])
            self.assertIn("PGCONNECT_TIMEOUT", services["init-db"])
            self.assertIn("bootstrap.sql", services["init-db"])
            self.assertIn("apply.sql", services["init-db"])
            self.assertIn("i=$$((i+1))", services["init-db"])
            self.assertIn("silo-data:/data", services["init-db"])
            self.assertIn("condition: service_healthy", services["init-bucket"])
            self.assertNotIn("condition: service_started", services["init-bucket"])
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn("up -d postgres --wait", workflow)
        self.assertIn("run --rm init-db", workflow)
        ensure = (ROOT / ".github" / "actions" / "ensure-docker" / "action.yml").read_text()
        self.assertIn("not installing docker.io", ensure)
        self.assertIn("iptables-legacy", ensure)
        self.assertIn("ppap-icc-probe", ensure)
        isolated = (ROOT / "deploy" / "isolated-dev" / "compose.yaml").read_text()
        self.assertNotIn("127.0.0.1:9000:9000", isolated)
        self.assertNotIn("127.0.0.1:9001:9001", isolated)


if __name__ == "__main__":
    unittest.main()
