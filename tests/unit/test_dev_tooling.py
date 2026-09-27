"""Deploy contracts: compose interpolation, bake tags, nginx aliases, SQL migrations."""
from __future__ import annotations

import json
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


def _workflow_jobs(workflow: str) -> dict[str, str]:
    jobs: dict[str, list[str]] = {}
    current = ""
    in_jobs = False
    for line in workflow.splitlines():
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            current = line.strip().rstrip(":")
            jobs[current] = []
            continue
        if current:
            jobs[current].append(line)
    return {name: "\n".join(body) for name, body in jobs.items()}


def _workflow_steps(job: str) -> list[str]:
    steps: list[list[str]] = []
    current: list[str] = []
    in_steps = False
    for line in job.splitlines():
        if line.startswith("    steps:"):
            in_steps = True
            continue
        if not in_steps:
            continue
        if line.startswith("      -"):
            if current:
                steps.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        steps.append(current)
    return ["\n".join(body) for body in steps]


def _run_scoped_idents(text: str) -> set[str]:
    idents: set[str] = set()
    for line in text.splitlines():
        if "github.run_id" not in line:
            continue
        value = line.partition(":")[2]
        if "github.run_id" not in value:
            value = line.partition("=")[2]
        value = value.strip().strip("\"'")
        if "github.run_id" in value:
            idents.add(value)
    return idents


def _full_canonical_bakes(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped.startswith("docker buildx bake"):
            continue
        rest = stripped[len("docker buildx bake") :].strip()
        if not rest or rest.startswith("-"):
            found.append(stripped)
    return found


def _github_yaml_files() -> list[Path]:
    return [
        path
        for path in (ROOT / ".github").rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    ]


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
        self.assertIn("ppap_deep*", cfg["include"])
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
        self.assertLess(app.index("apt-get"), app.index("ARG PPAP_BUILD_SHA"))
        self.assertIn("Acquire::Retries=5", app)
        web = (ROOT / "deploy" / "web" / "Dockerfile").read_text()
        self.assertIn("FROM nginx:1.27-alpine", web)
        self.assertNotIn("web_server.py", web)
        self.assertIn("build-identity.json", web)
        self.assertLess(web.index("COPY apps/web"), web.index("ARG PPAP_BUILD_SHA"))

    def test_nginx_aliases_match_baseline_customer_entries(self):
        conf = (ROOT / "deploy" / "web" / "nginx.conf").read_text()
        self.assertIn("listen 4173", conf)
        self.assertIn("location = /audit-files", conf)
        self.assertIn("try_files /index.html =404", conf)
        self.assertNotIn("/audit-files.html", conf)
        self.assertIn("location = /addon", conf)
        self.assertIn("try_files /addon.html =404", conf)
        self.assertIn("location = /usage", conf)
        self.assertNotIn("/usage.html", conf)
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
        self.assertIn("PPAP_CURRENT_TENANT_ALLOWLIST", services["api"])
        self.assertIn("PPAP_USAGE_CHARGE_ENABLED", services["api"])
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
        isolated = (ROOT / "deploy" / "isolated-dev" / "compose.yaml").read_text()
        self.assertNotIn("127.0.0.1:9000:9000", isolated)
        self.assertNotIn("127.0.0.1:9001:9001", isolated)
        self.assertIn("github.event.pull_request.head.sha", workflow)

    def test_ci_asserts_docker_capability_without_host_repair(self):
        self.assertFalse((ROOT / ".github" / "actions" / "ensure-docker").exists())
        action = (ROOT / ".github" / "actions" / "assert-docker" / "action.yml").read_text()
        self.assertIn("runner does not satisfy ppap-docker capability", action)
        self.assertIn("command -v docker", action)
        self.assertIn("docker info", action)
        self.assertIn("docker compose version", action)
        self.assertIn("docker buildx version", action)
        for needle in (
            "sudo",
            "apt-get",
            "apt ",
            "yum ",
            "apk ",
            "systemctl",
            "service docker",
            "dockerd",
            "iptables",
            "firewall",
            "chmod",
            "chown",
            "docker.sock",
            "ppap-icc-probe",
        ):
            self.assertNotIn(needle, action)

        github_yaml = _github_yaml_files()
        self.assertTrue(github_yaml)
        for path in github_yaml:
            text = path.read_text()
            self.assertNotIn("ensure-docker", text)
            self.assertNotIn("postgresql-client", text)
            self.assertNotIn("libreoffice-writer", text)
            self.assertNotIn("libc6-dev", text)
            self.assertNotIn("legacy_doc_sandbox.c", text)

        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        lifecycle = (ROOT / ".github" / "workflows" / "insforge-lifecycle.yml").read_text()
        jobs = _workflow_jobs(workflow)
        docker_jobs = [
            body
            for body in jobs.values()
            if "docker compose" in body or "docker buildx" in body or "docker build " in body
        ]
        self.assertTrue(docker_jobs)
        for body in docker_jobs:
            self.assertIn("uses: ./.github/actions/assert-docker", body)
        self.assertIn("uses: ./.github/actions/assert-docker", lifecycle)

    def test_ci_docker_integration_bakes_once_and_always_cleans(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        jobs = _workflow_jobs(workflow)
        lifecycle = [
            body
            for body in jobs.values()
            if "deploy/compose.yaml" in body or "deploy/isolated-dev/compose.yaml" in body
        ]
        self.assertTrue(lifecycle)

        for body in lifecycle:
            self.assertTrue("COMPOSE_PROJECT_NAME" in body or "docker compose -p" in body)
            self.assertIn("github.run_id", body)
            self.assertTrue(
                any(
                    "if: always()" in step
                    and "docker compose" in step
                    and re.search(r"\bdown\b", step)
                    for step in _workflow_steps(body)
                )
            )
            self.assertTrue(
                any(
                    "if: failure()" in step and "ps" in step and "logs" in step
                    for step in _workflow_steps(body)
                )
            )

        coverage = "\n".join(lifecycle)
        self.assertIn("deploy/compose.yaml", coverage)
        self.assertIn("deploy/isolated-dev/compose.yaml", coverage)
        self.assertGreaterEqual(len(_run_scoped_idents(coverage)), 3)
        self.assertIn("tests.deploy.test_runtime", coverage)
        self.assertIn("tests.deploy.test_backup", coverage)
        self.assertIn("tests.e2e", coverage)

        isolated_full_up = [
            step
            for body in lifecycle
            for step in _workflow_steps(body)
            if "deploy/isolated-dev/compose.yaml" in step
            and re.search(r"\bup\b", step)
            and "up -d postgres" not in step
        ]
        self.assertTrue(isolated_full_up)
        for step in isolated_full_up:
            self.assertIn("--no-build", step)
            self.assertNotIn("up --build", step)

        production_e2e = "\n".join(
            body
            for body in jobs.values()
            if "deploy/compose.yaml" in body or "tests.e2e" in body
        )
        self.assertLessEqual(len(_full_canonical_bakes(production_e2e)), 1)

    def test_schema_ci_discovers_authoritative_postgres_suite(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        testing = (ROOT / "docs" / "TESTING.md").read_text()
        tests_readme = (ROOT / "tests" / "README.md").read_text()
        entry = "python -m unittest discover -s tests/schema"
        self.assertIn("uv run --no-sync " + entry, workflow)
        self.assertIn(entry, testing)
        self.assertIn(entry, tests_readme)
        modules = {path.name for path in (ROOT / "tests" / "schema").glob("test_*.py")}
        self.assertIn("test_upgrade.py", modules)
        self.assertIn("test_rule_parse.py", modules)

    def test_ci_uses_uv_lock_as_python_dependency_authority(self):
        lock = ROOT / "uv.lock"
        self.assertTrue(lock.is_file())
        lock_text = lock.read_text()
        self.assertIn('name = "playwright"', lock_text)
        self.assertIn("sha256:", lock_text)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertIn("playwright", project["project"]["optional-dependencies"]["e2e"])
        self.assertTrue((ROOT / ".python-version").read_text().strip())

        github_yaml = _github_yaml_files()
        self.assertTrue(github_yaml)
        for path in github_yaml:
            text = path.read_text()
            self.assertNotIn("uv pip install", text)
            self.assertNotIn("pip install -e", text)
            self.assertNotIn("rm -rf .venv", text)
            self.assertNotIn("uv venv", text)
            self.assertNotIn(".venv/bin", text)

        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        jobs = _workflow_jobs(workflow)
        python_jobs = [body for body in jobs.values() if "setup-uv" in body]
        self.assertTrue(python_jobs)
        for body in python_jobs:
            self.assertIn("uv sync --locked", body)
            self.assertIn("--extra dev", body)
            self.assertIn("uv run --no-sync", body)
            self.assertNotIn("uv python install", body)
        self.assertTrue(any("--extra e2e" in body for body in python_jobs))
        self.assertTrue(any("playwright install" in body for body in python_jobs))
        self.assertTrue(any("pytest" in body for body in python_jobs))

    def test_pnpm_version_authority_is_package_manager_metadata(self):
        package = json.loads((ROOT / "apps" / "web" / "package.json").read_text())
        self.assertRegex(package["packageManager"], r"^pnpm@\d+\.\d+\.\d+$")
        self.assertTrue((ROOT / ".node-version").read_text().strip())
        for path in (
            ROOT / ".github" / "workflows" / "tests.yml",
            ROOT / ".github" / "workflows" / "insforge-lifecycle.yml",
            ROOT / "deploy" / "web" / "Dockerfile",
            ROOT / "deploy" / "isolated-dev" / "web-dev.yaml",
        ):
            text = path.read_text()
            self.assertNotIn("corepack prepare pnpm@", text)
            self.assertNotIn("node-version: 22", text)
        node_jobs = "\n".join(
            [
                (ROOT / ".github" / "workflows" / "tests.yml").read_text(),
                (ROOT / ".github" / "workflows" / "insforge-lifecycle.yml").read_text(),
            ]
        )
        self.assertIn("node-version-file: .node-version", node_jobs)
        self.assertIn("pnpm install --frozen-lockfile", node_jobs)
        web = (ROOT / "deploy" / "web" / "Dockerfile").read_text()
        self.assertIn("corepack enable", web)
        self.assertIn("pnpm install --frozen-lockfile", web)

    def test_third_party_actions_are_sha_pinned(self):
        uses = re.compile(r"^\s+uses:\s+(\S+)", re.M)
        sha_ref = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
        for path in _github_yaml_files():
            for match in uses.finditer(path.read_text()):
                ref = match.group(1)
                if ref.startswith("./") or ref.startswith(".github/"):
                    continue
                self.assertRegex(
                    ref,
                    sha_ref,
                    msg=f"{path}: {ref} must be an immutable commit SHA",
                )


if __name__ == "__main__":
    unittest.main()
