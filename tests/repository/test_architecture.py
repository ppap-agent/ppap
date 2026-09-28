"""PPAP layout invariants that uv, pytest, and actionlint do not enforce.

Native tools own their own semantics: uv installs the workspace, pytest
collects with importlib, docker compose config renders Compose, bake checks
the build graph, actionlint checks workflow syntax, nginx -t checks nginx,
and kustomize plus kubeconform check the TKE manifests. This module only
rejects structures those tools still accept.
"""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("packages", "services")
ROOT_TEST_GROUPS = {"repository", "schema", "corpus", "integration", "e2e", "live"}
GUEST_ENV = ROOT / "packages/ppap_deep/src/ppap_deep/isolation/container.py"
FORBIDDEN_IMPORT = re.compile(
    r"^(?:from|import)\s+(?:tests(?:\.|\s|$)|test_|store_fixture\b|sample_rule\b|memory_objects\b|conftest\b)"
)
SYS_PATH = re.compile(r"sys\.path\.(?:insert|append|extend)\s*\(")


def _pyproject(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _member_dirs() -> set[Path]:
    found = set()
    for base in PRODUCTION_ROOTS:
        for project in (ROOT / base).glob("*/pyproject.toml"):
            found.add(project.parent)
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
    return {
        name: {
            dependency
            for spec in project.get("dependencies", [])
            if (dependency := _dependency_name(spec)) in names
        }
        for name, project in projects.items()
    }


class ArchitectureTests(unittest.TestCase):
    def test_workspace_members_match_physical_owners(self):
        root = _pyproject(ROOT / "pyproject.toml")
        self.assertFalse(root["tool"]["uv"]["package"])
        self.assertNotIn("setuptools", root.get("tool", {}))
        self.assertNotIn("[tool.setuptools]", (ROOT / "pyproject.toml").read_text())
        self.assertFalse((ROOT / "requirements.txt").exists())
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
            self.assertFalse((member / import_name).exists())
        self.assertNotIn("optional-dependencies", root.get("project", {}))
        dev = root["dependency-groups"]["dev"]
        self.assertIn("ppap-corpus-contracts", dev)
        self.assertNotIn("ppap-corpus-contracts", root["project"]["dependencies"])
        self.assertNotIn("pytest", root["dependency-groups"]["e2e"])

    def test_production_dependency_graph_is_acyclic_and_owner_directed(self):
        graph = _production_graph()
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                cycle = visiting[visiting.index(name):] + [name]
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

        self.assertNotIn("ppap-excel-audit-engine", graph["ppap-core"])
        self.assertNotIn("ppap-api", graph["ppap-worker"])
        self.assertIn("ppap-product-store", graph["ppap-api"])
        self.assertIn("ppap-product-store", graph["ppap-worker"])
        self.assertIn("ppap-application", graph["ppap-api"])
        self.assertIn("ppap-application", graph["ppap-worker"])

        core_source = "\n".join(
            path.read_text()
            for path in (ROOT / "packages/ppap_core/src/ppap_core").glob("*.py")
        )
        worker_source = "\n".join(
            path.read_text()
            for path in (ROOT / "services/worker/src/ppap_worker").glob("*.py")
        )
        store_source = "\n".join(
            path.read_text()
            for path in (ROOT / "packages/ppap_product_store/src/ppap_product_store").glob("*.py")
        )
        self.assertNotIn("ppap_excel_audit_engine", core_source)
        self.assertNotIn("ppap_api", worker_source)
        self.assertNotIn("ppap_excel_audit_engine", store_source)
        self.assertNotIn("ppap_deep", store_source)

    def test_pytest_importlib_is_the_only_import_authority(self):
        options = _pyproject(ROOT / "pyproject.toml")["tool"]["pytest"]["ini_options"]
        self.assertIn("--import-mode=importlib", options["addopts"])
        self.assertNotIn("pythonpath", options)
        self.assertFalse((ROOT / "tests" / "pathsetup.py").exists())
        self.assertFalse((ROOT / "tests" / "common").exists())
        self.assertFalse((ROOT / "tests" / "utils").exists())

    def test_root_tests_are_system_groups_only(self):
        groups = {
            path.name
            for path in (ROOT / "tests").iterdir()
            if path.is_dir() and path.name != "__pycache__"
        }
        self.assertEqual(groups, ROOT_TEST_GROUPS)

    def test_tests_do_not_import_each_other_or_mutate_sys_path(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text()
            rel = path.relative_to(ROOT)
            if SYS_PATH.search(text):
                offenders.append(f"sys.path mutation in {rel}")
            if "tests" not in rel.parts or path.name.startswith("conftest"):
                continue
            if "__init__.py" in rel.parts and "tests" in rel.parts:
                offenders.append(f"tests package marker {rel}")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if FORBIDDEN_IMPORT.match(stripped):
                    offenders.append(f"{rel}: {stripped}")
        self.assertEqual(offenders, [])

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
        self.assertNotIn("pip install", app)
        self.assertNotIn("PYTHONPATH", app)
        psw = (ROOT / "tests/corpus/Dockerfile.psw").read_text()
        self.assertIn("uv sync --project /app --frozen --no-editable", psw)
        self.assertNotIn("pip", psw)
        self.assertNotIn("--inexact", psw)
        bootstrap = re.compile(r"(?m)ENV\s+PYTHONPATH|^(?!\s*#).*[\"']PYTHONPATH[\"']\s*:")
        for path in ROOT.rglob("*"):
            if not path.is_file():
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

    def test_owner_ci_discovers_physical_test_directories(self):
        # actionlint checks workflow syntax. It does not know that each
        # physical owner must be its own pytest process.
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        self.assertIn('Path("packages")', workflow)
        self.assertIn('Path("services")', workflow)
        self.assertIn('glob("*/tests")', workflow)
        self.assertIn("matrix.path", workflow)
        self.assertNotIn("services/api/tests services/worker/tests", workflow)
        self.assertNotIn("packages/ppap_core/tests packages/ppap_deep/tests", workflow)
