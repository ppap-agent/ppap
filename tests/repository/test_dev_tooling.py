"""Deploy contracts: compose interpolation, bake tags, nginx aliases, SQL migrations."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
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


def _schedule_crons(source: str) -> list[str]:
    return re.findall(r"cron:\s*[\"']([^\"']+)[\"']", source)


def _assert_weekly_or_coarser(testcase: unittest.TestCase, cron: str, *, msg: str) -> None:
    fields = cron.split()
    testcase.assertEqual(len(fields), 5, cron)
    minute, hour, day_of_month, _month, day_of_week = fields
    testcase.assertNotEqual(minute, "*")
    testcase.assertNotEqual(hour, "*")
    testcase.assertFalse(minute.startswith("*/"), cron)
    testcase.assertFalse(hour.startswith("*/"), cron)
    testcase.assertTrue(day_of_week != "*" or day_of_month != "*", msg=msg)


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


def _owner_python_test_directories(root: Path) -> set[str]:
    found: set[str] = set()
    for kind in ("packages", "services"):
        base = root / kind
        if not base.is_dir():
            continue
        for owner in base.iterdir():
            if not owner.is_dir() or owner.name.startswith("."):
                continue
            tests_dir = owner / "tests"
            if not tests_dir.is_dir():
                continue
            for path in tests_dir.rglob("*.py"):
                if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                    continue
                if path.name.startswith("test_") or path.name.endswith("_test.py"):
                    found.add(f"{kind}/{owner.name}/tests")
                    break
    return found


def _module_matrix_paths(modules_job: str) -> list[str]:
    paths: list[str] = []
    for line in modules_job.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if not stripped.startswith("paths:"):
            continue
        paths.extend(stripped.partition(":")[2].split())
    return paths


def _module_matrix_coverage_problems(root: Path, workflow: str) -> list[str]:
    listed = _module_matrix_paths(_workflow_jobs(workflow).get("python_modules", ""))
    owners = _owner_python_test_directories(root)
    problems: list[str] = []
    seen: set[str] = set()
    for path in listed:
        if path in seen:
            problems.append(f"duplicate matrix path: {path}")
        seen.add(path)
        if not (root / path).is_dir():
            problems.append(f"matrix path does not exist: {path}")
    for path in sorted(owners - set(listed)):
        problems.append(f"owner tests not in module matrix: {path}")
    for path in sorted(set(listed) - owners):
        if (root / path).is_dir():
            problems.append(f"matrix path is not an owner Python test directory: {path}")
    return problems


def _modules_workflow(*path_groups: str) -> str:
    lines = [
        "jobs:",
        "  python_modules:",
        "    strategy:",
        "      matrix:",
        "        include:",
    ]
    for index, paths in enumerate(path_groups):
        lines.append(f"          - id: group-{index}")
        lines.append(f"            paths: {paths}")
    return "\n".join(lines) + "\n"


def _write_python_test(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_ok():\n    assert True\n")


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
        self.assertNotIn("uses: ./.github/actions/assert-docker", jobs["repository"])
        self.assertIn("uses: ./.github/actions/assert-docker", lifecycle)

    def test_insforge_config_uses_native_compose_not_a_parser(self):
        source = (ROOT / "deploy" / "insforge" / "verify-config.mjs").read_text()
        self.assertIn("compose", source)
        self.assertIn("--env-file", source)
        self.assertIn("config", source)
        self.assertIn("--format", source)
        self.assertIn("json", source)
        self.assertNotIn("function interpolate(", source)
        self.assertNotIn("function composeServices(", source)
        self.assertNotIn("function parseDotenv(", source)
        self.assertNotIn("function serviceEnvironment(", source)
        self.assertNotIn("function interpolatedEnvironment(", source)
        self.assertNotIn("docker info", source)
        self.assertNotIn("docker buildx", source)
        self.assertIn("Compose CLI", source)
        jobs = _workflow_jobs((ROOT / ".github" / "workflows" / "tests.yml").read_text())
        self.assertIn("node deploy/insforge/verify-config.mjs", jobs["repository"])
        self.assertNotIn("uses: ./.github/actions/assert-docker", jobs["repository"])
        self.assertNotIn("docker info", jobs["repository"])
        self.assertNotIn("buildx", jobs["repository"])

    def test_insforge_lifecycle_is_path_filtered_live_authority(self):
        lifecycle = (ROOT / ".github" / "workflows" / "insforge-lifecycle.yml").read_text()
        self.assertIn("workflow_dispatch:", lifecycle)
        self.assertIn("pull_request:", lifecycle)
        self.assertIn("schedule:", lifecycle)
        self.assertIn("deploy/insforge/**", lifecycle)
        self.assertIn(".github/workflows/insforge-lifecycle.yml", lifecycle)
        self.assertIn(".node-version", lifecycle)
        self.assertIn("bash deploy/insforge/smoke-local.sh", lifecycle)
        self.assertIn("uses: ./.github/actions/assert-docker", lifecycle)
        crons = _schedule_crons(lifecycle)
        self.assertEqual(len(crons), 1, lifecycle)
        _assert_weekly_or_coarser(
            self,
            crons[0],
            msg="InsForge lifecycle schedule must be weekly or coarser",
        )
        smoke = (ROOT / "deploy" / "insforge" / "smoke-local.sh").read_text()
        self.assertIn("redact_json", smoke)
        self.assertIn("trap cleanup EXIT", smoke)
        self.assertIn("local stop", smoke)
        self.assertIn("@insforge/cli@${CLI_VERSION}", smoke)

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
        self.assertIn("pytest tests/integration/runtime", coverage)
        self.assertNotIn("tests/" + "deploy", coverage)
        self.assertIn("tests/e2e", coverage)

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

    def test_ci_uses_physical_ownership_matrix_and_stable_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        jobs = _workflow_jobs(workflow)

        self.assertEqual(
            set(jobs),
            {"web", "repository", "python_modules", "schema", "corpus", "integration", "ci_gate"},
        )

        repository = jobs["repository"]
        self.assertIn("name: Repository contract", repository)
        self.assertIn("pytest tests/repository", repository)
        self.assertIn("node deploy/insforge/verify-config.mjs", repository)

        modules = jobs["python_modules"]
        self.assertIn("name: core/deep", modules)
        self.assertIn("name: documents", modules)
        self.assertIn("name: engine", modules)
        self.assertIn("name: services", modules)
        self.assertIn("postgres: true", modules)
        self.assertIn("pytest ${{ matrix.paths }}", modules)

        corpus = jobs["corpus"]
        for name in ("Excel", "PDF", "scanned", "V3", "PSW"):
            self.assertIn(f"name: {name}", corpus)
        self.assertIn("tests/corpus/Dockerfile.psw", corpus)

        integration = jobs["integration"]
        self.assertIn("name: Product integration", integration)
        self.assertIn("pytest tests/integration/product", integration)
        self.assertIn("pytest tests/integration/runtime", integration)
        self.assertIn("pytest tests/e2e", integration)
        self.assertEqual(len(_full_canonical_bakes(integration)), 1)

        gate = jobs["ci_gate"]
        self.assertIn("name: CI Gate", gate)
        self.assertIn("if: always()", gate)
        for dependency in ("web", "repository", "python_modules", "schema", "corpus", "integration"):
            self.assertIn(f"- {dependency}", gate)
            self.assertIn(f"needs.{dependency}.result", gate)
        self.assertIn('if [ "$result" != "success" ]', gate)

        self.assertNotIn("dorny/paths-filter", workflow)
        self.assertNotIn("changed-files", workflow)
        self.assertNotIn("git diff", workflow)
        self.assertNotIn("tests/live", workflow)

        for path in (
            ROOT / "docs" / "TESTING.md",
            ROOT / "docs" / "DEVELOPMENT.md",
            ROOT / "tests" / "README.md",
        ):
            text = path.read_text()
            self.assertIn("CI Gate", text, str(path))
            self.assertIn("tests/integration/product", text, str(path))
            self.assertIn("services/api/tests services/worker/tests", text, str(path))

    def test_ci_module_matrix_covers_physical_owners(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertEqual(_module_matrix_coverage_problems(ROOT, workflow), [])

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write_python_test(root, "packages/ppap_core/tests/test_core.py")
            _write_python_test(root, "packages/ppap_new/tests/test_new.py")
            missing = _module_matrix_coverage_problems(
                root,
                _modules_workflow("packages/ppap_core/tests"),
            )
            self.assertTrue(any("owner tests not in module matrix: packages/ppap_new/tests" in item for item in missing), missing)

            duplicated = _module_matrix_coverage_problems(
                root,
                _modules_workflow("packages/ppap_core/tests", "packages/ppap_core/tests packages/ppap_new/tests"),
            )
            self.assertTrue(any("duplicate matrix path: packages/ppap_core/tests" in item for item in duplicated), duplicated)

            nonexistent = _module_matrix_coverage_problems(
                root,
                _modules_workflow("packages/ppap_core/tests packages/ppap_new/tests packages/missing/tests"),
            )
            self.assertTrue(any("matrix path does not exist: packages/missing/tests" in item for item in nonexistent), nonexistent)

    def test_merge_contract_is_required_not_deterministic(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        jobs = _workflow_jobs(workflow)
        gate = jobs["ci_gate"]
        self.assertIn("name: CI Gate", gate)
        self.assertIn("- corpus", gate)
        self.assertIn("needs.corpus.result", gate)
        self.assertIn("Require merge contract jobs to succeed", gate)
        self.assertNotIn("deterministic", gate)

        forbidden = (
            "deterministic groups",
            "required deterministic",
            "default deterministic",
            "Require deterministic",
            "默认 unit CI",
        )
        for path in (
            ROOT / ".github" / "workflows" / "tests.yml",
            ROOT / "docs" / "TESTING.md",
            ROOT / "docs" / "DEVELOPMENT.md",
            ROOT / "tests" / "README.md",
        ):
            text = path.read_text()
            for phrase in forbidden:
                self.assertNotIn(phrase, text, str(path))

        testing = (ROOT / "docs" / "TESTING.md").read_text()
        self.assertIn("默认 merge contract", testing)
        self.assertIn("required groups", testing)
        self.assertIn("cache miss 时可以调用真实 OCR / model", testing)
        self.assertIn("不进入默认 merge contract、需要显式执行", testing)
        self.assertIn("外部依赖本身不改变 test ownership", testing)

    def test_schema_ci_discovers_authoritative_postgres_suite(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        testing = (ROOT / "docs" / "TESTING.md").read_text()
        tests_readme = (ROOT / "tests" / "README.md").read_text()
        development = (ROOT / "docs" / "DEVELOPMENT.md").read_text()
        migrations = (ROOT / "migrations" / "README.md").read_text()
        entry = "pytest tests/schema"
        self.assertIn("uv run --no-sync " + entry, workflow)
        self.assertIn(entry, testing)
        self.assertIn(entry, tests_readme)
        self.assertIn(entry, development)
        self.assertIn(entry, migrations)
        schema_job = _workflow_jobs(workflow)["schema"]
        self.assertIn("up -d postgres --wait", schema_job)
        self.assertIn("run --rm init-db", schema_job)
        self.assertNotIn("postgresql-client", schema_job)
        self.assertNotIn("python -m unittest", schema_job)
        modules = {path.name for path in (ROOT / "tests" / "schema").glob("test_*.py")}
        self.assertIn("test_upgrade.py", modules)
        self.assertIn("test_rule_parse.py", modules)
        self.assertIn("test_audit_bindings.py", modules)
        self.assertIn("test_usage_acceptance.py", modules)


    def test_root_tests_are_system_level_contracts(self):
        tests_root = ROOT / "tests"
        top_level_dirs = {
            path.name
            for path in tests_root.iterdir()
            if path.is_dir() and not path.name.startswith((".", "_"))
        }
        self.assertEqual(
            top_level_dirs,
            {"repository", "schema", "corpus", "integration", "e2e", "live"},
        )
        self.assertFalse((tests_root / "unit").exists())
        self.assertFalse((tests_root / "deploy").exists())
        self.assertFalse((tests_root / "fixtures").exists())
        self.assertTrue((tests_root / "corpus" / "fixtures" / "legacy_doc").is_dir())
        self.assertTrue((tests_root / "corpus" / "fixtures" / "ocr_cache").is_dir())
        self.assertTrue((tests_root / "integration" / "product").is_dir())
        self.assertTrue((tests_root / "integration" / "runtime").is_dir())

        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn("paths: services/api/tests services/worker/tests", workflow)
        self.assertIn("pytest tests/integration/product", workflow)
        self.assertIn("pytest tests/integration/runtime", workflow)
        legacy_paths = ("tests/" + "unit", "tests/" + "deploy", "tests/" + "fixtures")
        for legacy in legacy_paths:
            self.assertNotIn(legacy, workflow)

        text_suffixes = {".py", ".md", ".yml", ".yaml", ".toml", ".sh", ".mjs", ".json", ".hcl"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in text_suffixes:
                continue
            if any(part.startswith(".") or part in {"__pycache__", "node_modules"} for part in path.parts):
                continue
            text = path.read_text(errors="ignore")
            for legacy in legacy_paths:
                self.assertNotIn(legacy, text, str(path))
        self.assertNotIn("tests/live", workflow)

    def test_v2_corpus_baseline_exceptions_are_per_rule(self):
        conftest = (ROOT / "tests" / "corpus" / "conftest.py").read_text()
        self.assertNotIn("BASELINE_XFAIL_IDS", conftest)
        self.assertNotIn("pytest.mark.xfail", conftest)
        from tests.corpus.v2_assets.baseline_exceptions import BASELINE_RULE_MISMATCHES

        self.assertTrue(BASELINE_RULE_MISMATCHES, "V2 baseline exceptions must be recorded")
        seen: set[tuple[str, str, int]] = set()
        for item in BASELINE_RULE_MISMATCHES:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 3, item)
            file_name, file_type, rule_index = item
            self.assertIsInstance(file_name, str)
            self.assertTrue(file_name)
            self.assertIn(file_type, {"excel", "pdf", "scanned"})
            self.assertIsInstance(rule_index, int)
            self.assertGreaterEqual(rule_index, 1)
            self.assertNotIn(item, seen)
            seen.add(item)

    def test_llm_and_feishu_readiness_are_scheduled_outside_gate(self):
        llm = (ROOT / ".github" / "workflows" / "llm-readiness.yml").read_text()
        feishu = (ROOT / ".github" / "workflows" / "feishu-readiness.yml").read_text()
        tests = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        jobs = _workflow_jobs(tests)
        gate = jobs["ci_gate"]

        for name, source in (("LLM readiness", llm), ("Feishu readiness", feishu)):
            self.assertIn("workflow_dispatch:", source, name)
            self.assertIn("schedule:", source, name)
            self.assertNotIn("pull_request:", source, name)
            crons = _schedule_crons(source)
            self.assertEqual(len(crons), 1, name)
            _assert_weekly_or_coarser(self, crons[0], msg=f"{name} schedule must be weekly or coarser")

        self.assertIn("secrets.OPENAI_API_KEY", llm)
        self.assertIn("secrets.OPENAI_BASE_URL", llm)
        self.assertIn("secrets.MODEL_NAME", llm)
        self.assertIn("${LLM_BASE_URL%/}/responses", llm)
        self.assertIn(":?missing OPENAI_API_KEY", llm)
        self.assertNotIn("localhost", llm)

        self.assertIn("secrets.FEISHU_APP_ID", feishu)
        self.assertIn("secrets.FEISHU_APP_SECRET", feishu)
        self.assertIn("https://open.feishu.cn/open-apis/auth/v3/app_ticket/resend", feishu)
        self.assertIn(":?missing FEISHU_APP_ID", feishu)
        self.assertNotIn("localhost", feishu)

        self.assertNotIn("llm-readiness", tests)
        self.assertNotIn("feishu-readiness", tests)
        self.assertNotIn("LLM readiness", gate)
        self.assertNotIn("Feishu readiness", gate)

        testing = (ROOT / "docs" / "TESTING.md").read_text()
        readme = (ROOT / "tests" / "README.md").read_text()
        development = (ROOT / "docs" / "DEVELOPMENT.md").read_text()
        self.assertIn("workflow_dispatch", testing)
        self.assertIn("workflow_dispatch", readme)
        for text in (testing, readme, development):
            self.assertIn("CI Gate", text)
            self.assertIn("LLM", text)
            self.assertIn("Feishu", text)

    def test_required_v3_corpus_does_not_skip_missing_assets(self):
        grouped = (ROOT / "tests" / "corpus" / "v3_poc" / "test_poc_assets_grouped.py").read_text()
        self.assertNotIn("pytest.skip", grouped)
        self.assertNotIn("allow_module_level", grouped)
        oracle = (ROOT / "tests" / "corpus" / "v3_poc" / "poc_oracle.py").read_text()
        self.assertIn("Required V3 dataset is missing", oracle)
        self.assertIn("Required V3 expected XLSX is missing", oracle)
        self.assertIn("No valid V3 cases", oracle)

    def test_python_tests_use_pytest_and_installed_imports(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        pytest_config = project.get("tool", {}).get("pytest", {}).get("ini_options", {})
        self.assertEqual(pytest_config.get("asyncio_mode"), "strict")
        self.assertFalse((ROOT / "pytest.ini").exists())
        self.assertFalse((ROOT / "tests" / "pathsetup.py").exists())

        for path in ROOT.rglob("*.py"):
            if ".venv" in path.parts or path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(errors="ignore")
            self.assertNotIn("import tests.pathsetup", text, str(path))
            self.assertNotIn("from tests import pathsetup", text, str(path))

        for path in [
            ROOT / ".github" / "workflows" / "tests.yml",
            ROOT / "docs" / "TESTING.md",
            ROOT / "docs" / "DEVELOPMENT.md",
            ROOT / "tests" / "README.md",
            ROOT / "migrations" / "README.md",
        ]:
            text = path.read_text()
            self.assertNotIn("python -m unittest", text, str(path))
            self.assertNotIn("unittest discover", text, str(path))
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn("uv run --no-sync pytest", workflow)
        psw = (ROOT / "tests" / "corpus" / "Dockerfile.psw").read_text()
        self.assertNotIn("pytest.ini", psw)
        self.assertIn("COPY pyproject.toml", psw)

        imported = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import ppap_core, ppap_deep, ppap_docops, "
                    "ppap_excel_audit_engine, ppap_ocr_adapter, "
                    "ppap_pdf_to_excel, ppap_vision, ppap_api, ppap_worker"
                ),
            ],
            cwd=ROOT / "tests",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)

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
