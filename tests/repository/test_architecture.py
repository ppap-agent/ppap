"""PPAP layout invariants that uv, pytest, and actionlint do not enforce.

Native tools own their own semantics: uv installs the workspace, pytest
collects with importlib, Docker Compose renders Compose, Buildx Bake checks
the build graph, actionlint/GitHub Actions own workflow syntax and execution,
nginx checks nginx, and Kustomize plus kubeconform check Kubernetes objects.
This module only rejects repository-owned structures those tools still accept;
it does not parse workflow/config source to proxy native behavior.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("packages", "apps")
ROOT_TEST_GROUPS = {"repository", "schema", "corpus", "integration", "e2e", "live"}
GUEST_ENV = ROOT / "packages/agent/src/agent/isolation/container.py"
FORBIDDEN_IMPORT = re.compile(r"^(?:from|import)\s+(?:tests(?:\.|\s|$)|test_|store_fixture\b|sample_rule\b|memory_objects\b|conftest\b)")
SYS_PATH = re.compile(r"sys\.path\.(?:insert|append|extend)\s*\(")


def _repository_files() -> set[Path]:
    """Check repository inputs, including new files, without scanning ignored runs."""
    names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).decode().split("\0")
    return {ROOT / name for name in names if name and (ROOT / name).is_file()}


def _pyproject(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _compose_config(path: Path, env_file: Path | None = None, *, env: dict[str, str] | None = None) -> dict:
    args = ["docker", "compose", "--progress", "quiet", "-f", str(path)]
    if env_file is not None:
        args.extend(["--env-file", str(env_file)])
    args.extend(["config", "--format", "json"])
    completed = subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def _member_dirs() -> set[Path]:
    found = set()
    for base in PRODUCTION_ROOTS:
        for project in (ROOT / base).glob("*/pyproject.toml"):
            found.add(project.parent)
    corpus = ROOT / "tests/corpus/contracts/pyproject.toml"
    if corpus.is_file():
        found.add(corpus.parent)
    return found


def _dependency_name(spec: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", spec)
    if match is None:
        raise AssertionError(f"invalid dependency spec: {spec}")
    return match.group(0).lower().replace("_", "-")


def _production_graph() -> dict[str, set[str]]:
    projects = {}
    for member in _member_dirs():
        project = _pyproject(member / "pyproject.toml")["project"]
        projects[project["name"]] = project
    names = set(projects)
    return {name: {dependency for spec in project.get("dependencies", []) if (dependency := _dependency_name(spec)) in names} for name, project in projects.items()}


class ArchitectureTests(unittest.TestCase):
    def test_isolated_compose_requires_explicit_opt_in_for_customer_charging(self):
        for value, expected in (("", "false"), ("true", "true")):
            with self.subTest(charging=value):
                services = _compose_config(ROOT / "deploy/isolated-dev/compose.yaml", env={"PPAP_USAGE_CHARGE_ENABLED": value})["services"]
                for service in ("api", "worker"):
                    self.assertEqual(services[service]["environment"].get("PPAP_USAGE_CHARGE_ENABLED"), expected)
        production = _compose_config(ROOT / "deploy/compose.yaml", ROOT / "deploy/compose.env.example", env={"PPAP_USAGE_CHARGE_ENABLED": "true"})["services"]
        self.assertEqual(production["api"]["environment"]["PPAP_USAGE_CHARGE_ENABLED"], "true")

    def test_compose_supports_isolated_host_ports_and_matching_public_addresses(self):
        env = {
            "PPAP_API_HOST_PORT": "0",
            "PPAP_WEB_HOST_PORT": "0",
            "PPAP_TEST_POSTGRES_PORT": "0",
            "PPAP_PUBLIC_API_URL": "http://127.0.0.1:55123",
            "PPAP_WEB_URL": "http://127.0.0.1:55124",
        }
        for path, env_file in (
            (ROOT / "deploy/compose.yaml", ROOT / "deploy/compose.env.example"),
            (ROOT / "deploy/isolated-dev/compose.yaml", None),
        ):
            with self.subTest(compose=path):
                services = _compose_config(path, env_file, env=env)["services"]
                for service, target in (("api", 8080), ("web", 4173)):
                    binding = services[service]["ports"][0]
                    self.assertEqual(binding["host_ip"], "127.0.0.1")
                    self.assertEqual(str(binding["published"]), "0")
                    self.assertEqual(binding["target"], target)
                for service in ("api", "web"):
                    self.assertEqual(services[service]["environment"]["PPAP_PUBLIC_API_URL"], env["PPAP_PUBLIC_API_URL"])
                for service in ("api", "worker"):
                    self.assertEqual(services[service]["environment"]["PPAP_WEB_URL"], env["PPAP_WEB_URL"])

    def test_workspace_members_match_physical_owners(self):
        root = _pyproject(ROOT / "pyproject.toml")
        self.assertFalse(root["tool"]["uv"]["package"])
        self.assertNotIn("setuptools", root.get("tool", {}))
        self.assertNotIn("[tool.setuptools]", (ROOT / "pyproject.toml").read_text())
        self.assertFalse((ROOT / "requirements.txt").exists())
        members = {ROOT / path for path in root["tool"]["uv"]["workspace"]["members"]}
        self.assertEqual(members, _member_dirs())
        # One PDF conversion owner; no duplicate OCR adapter distribution.
        self.assertFalse(any(path.relative_to(ROOT).parts[:2] == ("packages", "ppap_ocr_adapter") for path in _repository_files()))
        sources = root["tool"]["uv"]["sources"]
        for member in members:
            project = _pyproject(member / "pyproject.toml")
            name = project["project"]["name"]
            import_name = name.replace("-", "_")
            self.assertEqual(sources[name], {"workspace": True})
            self.assertEqual(
                project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
                [f"src/{import_name}"],
            )
            self.assertTrue((member / "src" / import_name / "__init__.py").is_file())
            self.assertFalse((member / import_name).exists())
        self.assertNotIn("optional-dependencies", root.get("project", {}))
        dev = root["dependency-groups"]["dev"]
        self.assertIn("corpus-contracts", dev)
        self.assertNotIn("corpus-contracts", root["project"]["dependencies"])
        self.assertNotIn("pytest", root["dependency-groups"]["e2e"])

    def test_production_dependency_graph_is_acyclic_and_owner_directed(self):
        graph = _production_graph()
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                cycle = visiting[visiting.index(name) :] + [name]
                self.fail("production dependency cycle: " + " -> ".join(cycle))
            if name in visited:
                return
            visiting.append(name)
            for dependency in sorted(graph[name]):
                visit(dependency)
            visiting.pop()
            visited.add(name)

        for name in sorted(graph):
            visit(name)

        self.assertNotIn("ppap-core", graph)
        self.assertNotIn("api", graph["worker"])
        self.assertNotIn("product", graph["audit"])
        self.assertNotIn("audit", graph["documents"])
        self.assertNotIn("documents", graph["product"])
        self.assertIn("product", graph["api"])
        self.assertIn("worker", graph["api"])
        self.assertIn("product", graph["worker"])
        self.assertNotIn("product", graph["agent"])
        self.assertIn("audit", graph["agent"])
        self.assertNotIn("api", graph["agent"])

        worker_source = "\n".join(path.read_text() for path in (ROOT / "apps/worker/src/worker").glob("*.py"))
        store_source = "\n".join(path.read_text() for path in (ROOT / "packages/product/src/product/store").glob("*.py"))
        self.assertFalse((ROOT / "packages/ppap_core").exists())
        self.assertNotRegex(worker_source, r"(?m)^(?:from api\b|import api\b)")
        self.assertNotIn("audit.engine", store_source)
        self.assertNotIn("agent", store_source)

        legacy_orm_imports = []
        for path in (ROOT / "packages/audit/src").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                if any(module.split(".")[0] == "ppap_db" for module in modules):
                    legacy_orm_imports.append(str(path.relative_to(ROOT)))
        self.assertEqual(legacy_orm_imports, [])

    def test_pytest_importlib_is_the_only_import_authority(self):
        options = _pyproject(ROOT / "pyproject.toml")["tool"]["pytest"]["ini_options"]
        self.assertIn("--import-mode=importlib", options["addopts"])
        self.assertNotIn("pythonpath", options)
        self.assertIn("live", options["norecursedirs"])
        self.assertIn("node_modules", options["norecursedirs"])
        self.assertNotIn("tests/live", options["norecursedirs"])
        self.assertFalse((ROOT / "tests" / "pathsetup.py").exists())
        self.assertFalse((ROOT / "tests" / "common").exists())
        self.assertFalse((ROOT / "tests" / "utils").exists())

    def test_root_tests_are_system_groups_only(self):
        groups = {path.name for path in (ROOT / "tests").iterdir() if path.is_dir() and path.name != "__pycache__"}
        self.assertEqual(groups, ROOT_TEST_GROUPS)

    def test_tests_do_not_import_each_other_or_mutate_sys_path(self):
        offenders = []
        for path in _repository_files():
            if path.suffix != ".py":
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text()
            rel = path.relative_to(ROOT)
            if SYS_PATH.search(text):
                offenders.append(f"sys.path mutation in {rel}")
            if "tests" in rel.parts:
                for token in ("spec_from_file_location", "exec_module("):
                    if token in text:
                        offenders.append(f"dynamic test loader in {rel}: {token}")
            if "tests" not in rel.parts or path.name.startswith("conftest"):
                continue
            library = rel.parts[:4] == ("tests", "corpus", "contracts", "src")
            if "__init__.py" in rel.parts and "tests" in rel.parts and not library:
                offenders.append(f"tests package marker {rel}")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if FORBIDDEN_IMPORT.match(stripped):
                    offenders.append(f"{rel}: {stripped}")
        self.assertEqual(offenders, [])

    def test_test_support_uses_pytest_native_lifecycle(self):
        forbidden = ("spec_from_file_location", "exec_module(", "request.instance")
        offenders = []
        for path in _repository_files():
            if path.name != "conftest.py":
                continue
            text = path.read_text()
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(offenders, [])
        self.assertFalse((ROOT / "apps/api/tests/store_fixture.py").exists())
        self.assertFalse((ROOT / "apps/worker/tests/store_fixture.py").exists())
        self.assertFalse((ROOT / "tests/integration/product/store_fixture.py").exists())
        self.assertFalse((ROOT / "tests/schema/memory_objects.py").exists())
        self.assertFalse((ROOT / "tests/schema/sample_rule.py").exists())

    def test_corpus_contract_tests_follow_their_owner(self):
        owner = ROOT / "tests/corpus/contracts/tests"
        self.assertTrue((owner / "test_execution_outcome.py").is_file())
        self.assertTrue((owner / "test_baseline_exceptions.py").is_file())
        self.assertTrue((owner / "test_cross_format_contract.py").is_file())
        self.assertTrue((owner / "test_oracle_reconciliation.py").is_file())
        self.assertTrue((owner / "test_psw_cross_format_regression.py").is_file())
        self.assertFalse((ROOT / "tests/corpus/v2_assets/test_baseline_exceptions.py").exists())
        corpus_conftest = (ROOT / "tests/corpus/conftest.py").read_text()
        self.assertNotIn("BASELINE_XFAIL_IDS", corpus_conftest)
        self.assertNotIn("pytest.mark.xfail", corpus_conftest)
        moved_core_tests = "\n".join(path.read_text() for path in (ROOT / "packages/product/tests").glob("test_settings_priority.py"))
        self.assertNotIn("audit.engine", moved_core_tests)

    def test_corpus_pairs_and_psw_have_physical_suite_ownership(self):
        for suite in ("pairs", "psw"):
            directory = ROOT / "tests/corpus" / suite
            self.assertTrue(directory.is_dir())
            self.assertTrue(any(directory.glob("test_*.py")))

    def test_owner_tests_do_not_read_root_corpus(self):
        offenders = []
        for base in PRODUCTION_ROOTS:
            for path in (ROOT / base).glob("*/tests/**/*.py"):
                text = path.read_text()
                if any(token in text for token in ("assets/", "poc测试", "V2.0测试")):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_production_image_installs_the_workspace_without_path_bootstrap(self):
        app = (ROOT / "deploy/app/Dockerfile").read_text()
        self.assertIn("ghcr.io/astral-sh/uv:0.9.26", app)
        self.assertIn("uv sync --frozen --no-dev --no-editable", app)
        self.assertIn("cd /;", app)
        self.assertIn("import api, product, audit.engine, worker", app)
        # Only hash-checked prefetch may precede the authoritative frozen sync.
        prefetch = "uv pip install --python /app/.venv/bin/python --require-hashes --no-deps -r /tmp/ppap-requirements.txt"
        export = "uv export --quiet --frozen --no-dev --no-editable --no-emit-workspace"
        self.assertIn(export, app)
        self.assertIn(prefetch, app)
        self.assertLess(app.index(export), app.index(prefetch))
        dependencies = "uv sync --frozen --no-dev --no-editable --no-install-workspace"
        workspace = "uv sync --frozen --no-dev --no-editable;"
        self.assertLess(app.index(prefetch), app.index(dependencies))
        self.assertLess(app.index(dependencies), app.index("COPY packages /app/packages"))
        self.assertLess(app.index("COPY apps/api /app/apps/api"), app.index(workspace))
        self.assertLess(app.index("COPY apps/worker /app/apps/worker"), app.index(workspace))
        # Both install layers must keep downloaded archives outside the image.
        installs = [step for step in app.split("\nRUN ")[1:] if "uv sync" in step]
        self.assertTrue(installs)
        self.assertTrue(all(step.startswith("--mount=type=cache,target=/root/.cache/uv") for step in installs))
        self.assertNotIn("pip install", app.replace(prefetch, ""))
        self.assertNotIn("PYTHONPATH", app)
        psw = (ROOT / "tests/corpus/Dockerfile.psw").read_text()
        self.assertIn("uv sync --project /app --frozen --no-editable", psw)
        self.assertNotIn("pip", psw)
        self.assertNotIn("--inexact", psw)
        bootstrap = re.compile(r"(?m)ENV\s+PYTHONPATH|^(?!\s*#).*[\"']PYTHONPATH[\"']\s*:")
        for path in _repository_files():
            relative = path.relative_to(ROOT)
            # Production code and deployment configuration own installed
            # imports. Research runners have separate frozen helper roots and
            # are excluded from images by the native container-context tests.
            if relative.parts[0] not in {"deploy", "packages", "apps"}:
                continue
            if relative.parts[0] in PRODUCTION_ROOTS and "src" not in relative.parts:
                continue
            if any(part.startswith(".") or part == "__pycache__" or part == "node_modules" for part in path.parts):
                continue
            if path.suffix not in {".py", ".yml", ".yaml"} and "Dockerfile" not in path.name:
                continue
            if path.resolve() == GUEST_ENV.resolve():
                continue
            if bootstrap.search(path.read_text()):
                self.fail(f"PYTHONPATH bootstrap in {path.relative_to(ROOT)}")
        guest = GUEST_ENV.read_text()
        self.assertIn("Guest interpreter boundary", guest)
        self.assertIn('"PYTHONPATH"', guest)

    def test_migration_order_has_one_filename_authority(self):
        runner = (ROOT / "migrations/apply.sh").read_text()
        self.assertFalse((ROOT / "migrations/apply.sql").exists())
        self.assertIn("LC_ALL=C", runner)
        self.assertIn('"$MIGRATIONS_DIR"/[0-9][0-9][0-9][0-9]_*.sql', runner)
        self.assertIn("ON_ERROR_STOP=1", runner)
        self.assertIn('PGUSER="$PPAP_PG_MIGRATOR_USER"', runner)
        self.assertIn('PGPASSWORD="$PPAP_PG_MIGRATOR_PASSWORD"', runner)

        configs = (
            _compose_config(ROOT / "deploy/isolated-dev/compose.yaml"),
            _compose_config(
                ROOT / "deploy/compose.yaml",
                ROOT / "deploy/compose.env.example",
            ),
        )
        for config in configs:
            command = config["services"]["init-db"].get("command")
            command_text = "\n".join(command) if isinstance(command, list) else str(command or "")
            self.assertIn("sh /migrations/apply.sh", command_text)
            self.assertNotIn('PGUSER="$PPAP_PG_MIGRATOR_USER"', command_text)
            self.assertNotIn('PGPASSWORD="$PPAP_PG_MIGRATOR_PASSWORD"', command_text)
            self.assertNotIn("apply.sql", command_text)

    def test_physical_owner_test_directories_belong_to_workspace_members(self):
        members = _member_dirs()
        found = set()
        for base in PRODUCTION_ROOTS:
            for tests in (ROOT / base).glob("*/tests"):
                if any(tests.rglob("test_*.py")):
                    found.add(tests.parent)
        self.assertTrue(found)
        self.assertTrue(found.issubset(members))
