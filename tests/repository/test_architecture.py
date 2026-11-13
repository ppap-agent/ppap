"""PPAP layout invariants that uv, pytest, and actionlint do not enforce.

Native tools own their own semantics: uv installs the workspace, pytest
collects with importlib, Docker Compose renders Compose, Buildx Bake checks
the build graph, actionlint/GitHub Actions own workflow syntax and execution,
nginx checks nginx, and Kustomize plus kubeconform check Kubernetes objects.
This module only rejects repository-owned structures those tools still accept;
it does not parse workflow/config source to proxy native behavior.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("packages", "apps")
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
                    self.assertFalse(bool(services[service]["environment"].get("PPAP_MODEL_CODING_API_KEY")), "Deep provider credential belongs only to Controller")
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
        members = {ROOT / path for path in root["tool"]["uv"]["workspace"]["members"]}
        self.assertEqual(members, _member_dirs())
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
        dev = root["dependency-groups"]["dev"]
        self.assertIn("corpus-contracts", dev)
        self.assertNotIn("corpus-contracts", root["project"]["dependencies"])

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

    def test_physical_owner_test_directories_belong_to_workspace_members(self):
        members = _member_dirs()
        found = set()
        for base in PRODUCTION_ROOTS:
            for tests in (ROOT / base).glob("*/tests"):
                if any(tests.rglob("test_*.py")):
                    found.add(tests.parent)
        self.assertTrue(found)
        self.assertTrue(found.issubset(members))
