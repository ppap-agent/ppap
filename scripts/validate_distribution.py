#!/usr/bin/env python3
"""Validate one workspace distribution outside the root workspace environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_ONLY_MEMBERS = {"corpus-contracts"}
TEST_RUNNERS = {"pytest", "pytest-asyncio"}


@dataclass(frozen=True)
class Member:
    name: str
    path: Path
    import_name: str
    tests: Path
    postgres: bool
    dependencies: tuple[str, ...]


def dependency_name(spec: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", spec)
    if match is None:
        raise ValueError(f"invalid dependency spec: {spec}")
    return match.group(0).lower().replace("_", "-")


def load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def workspace_members() -> dict[str, Member]:
    root = load_toml(ROOT / "pyproject.toml")
    rows: dict[str, Member] = {}
    for relative in root["tool"]["uv"]["workspace"]["members"]:
        path = ROOT / relative
        project = load_toml(path / "pyproject.toml")
        name = project["project"]["name"]
        packages = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
        if len(packages) != 1:
            raise ValueError(f"{name}: expected one wheel package")
        import_name = Path(packages[0]).name
        rows[name] = Member(
            name=name,
            path=path,
            import_name=import_name,
            tests=path / "tests",
            postgres=relative in {"apps/api", "apps/worker"},
            dependencies=tuple(dependency_name(spec) for spec in project["project"].get("dependencies", [])),
        )
    return rows


def production_members() -> dict[str, Member]:
    members = workspace_members()
    root = load_toml(ROOT / "pyproject.toml")
    dev = {dependency_name(spec) for specs in root.get("dependency-groups", {}).values() for spec in specs}
    missing = DEV_ONLY_MEMBERS - dev
    if missing:
        raise ValueError(f"dev-only members are not declared in dependency groups: {sorted(missing)}")
    return {name: member for name, member in members.items() if name not in DEV_ONLY_MEMBERS}


def closure(target: str, members: dict[str, Member]) -> list[str]:
    if target not in members:
        raise ValueError(f"unknown workspace member: {target}")
    ordered: list[str] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            cycle = visiting[visiting.index(name) :] + [name]
            raise ValueError("workspace dependency cycle: " + " -> ".join(cycle))
        if name in visited:
            return
        visiting.append(name)
        for dependency in members[name].dependencies:
            if dependency in members:
                visit(dependency)
        visiting.pop()
        visited.add(name)
        ordered.append(name)

    visit(target)
    return ordered


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(command: list[str], *, cwd: Path | str | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def lock_constraints(destination: Path) -> Path:
    lock = load_toml(ROOT / "uv.lock")
    versions: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        source = package.get("source") or {}
        version = package.get("version")
        if "registry" not in source or not version:
            continue
        versions.setdefault(package["name"], set()).add(version)
    lines = [f"{name}=={next(iter(found))}" for name, found in sorted(versions.items()) if len(found) == 1]
    path = destination / "constraints.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_runner_requirements() -> list[str]:
    dev = load_toml(ROOT / "pyproject.toml")["dependency-groups"]["dev"]
    return [spec for spec in dev if dependency_name(spec) in TEST_RUNNERS]


def build_closure(target: str, destination: Path) -> tuple[list[str], dict[str, Path]]:
    members = workspace_members()
    names = closure(target, members)
    wheelhouse = destination / "wheelhouse"
    wheelhouse.mkdir()
    wheels: dict[str, Path] = {}
    for name in names:
        before = set(wheelhouse.glob("*.whl"))
        run(
            ["uv", "build", "--wheel", "--out-dir", str(wheelhouse), str(members[name].path)],
            cwd=ROOT,
            env=clean_env(),
        )
        created = set(wheelhouse.glob("*.whl")) - before
        if len(created) != 1:
            raise RuntimeError(f"{name}: expected one new wheel, got {sorted(str(path) for path in created)}")
        wheels[name] = created.pop()
    return names, wheels


def assert_wheel_install(target: str) -> None:
    members = workspace_members()
    member = members[target]
    with tempfile.TemporaryDirectory(prefix=f"ppap-dist-{target}-") as raw:
        work = Path(raw)
        names, wheels = build_closure(target, work)
        constraints = lock_constraints(work)
        venv = work / "venv"
        run(["uv", "venv", "--python", "3.12", str(venv)], cwd=work, env=clean_env())
        python = venv / "bin" / "python"
        install = [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            *[str(wheels[name]) for name in names],
            *test_runner_requirements(),
        ]
        run(install, cwd=work, env=clean_env())

        editable_probe = (
            "import importlib.metadata as m,json,sys;"
            "names=json.loads(sys.argv[1]);"
            "bad=[];"
            "[(bad.append(n) if ((raw:=m.distribution(n).read_text('direct_url.json')) "
            "and json.loads(raw).get('dir_info',{}).get('editable')) else None) for n in names];"
            "assert not bad, f'editable distributions: {bad}'"
        )
        run([str(python), "-c", editable_probe, json.dumps(names)], cwd=work, env=clean_env())

        import_probe = (
            "import importlib,pathlib,sys;"
            "m=importlib.import_module(sys.argv[1]);"
            "p=pathlib.Path(m.__file__).resolve();"
            "root=pathlib.Path(sys.argv[2]).resolve();"
            "print(p);"
            "assert p.is_relative_to(root), f'{p} is outside clean venv {root}'"
        )
        run([str(python), "-c", import_probe, member.import_name, str(venv)], cwd="/tmp", env=clean_env())

        if member.tests.is_dir() and any(member.tests.rglob("test_*.py")):
            run(
                [
                    str(python),
                    "-m",
                    "pytest",
                    "--rootdir",
                    str(ROOT),
                    "-c",
                    str(ROOT / "pyproject.toml"),
                    str(member.tests),
                ],
                cwd=work,
                env=clean_env(),
            )


def discover() -> None:
    rows = [
        {
            "id": name.replace("_", "-"),
            "name": name,
            "postgres": member.postgres,
        }
        for name, member in sorted(production_members().items())
    ]
    if not rows:
        raise RuntimeError("no production distributions discovered")
    print(json.dumps(rows, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--discover", action="store_true")
    group.add_argument("--member")
    args = parser.parse_args()
    if args.discover:
        discover()
    else:
        if args.member not in production_members():
            raise SystemExit(f"not a production member: {args.member}")
        assert_wheel_install(args.member)


if __name__ == "__main__":
    main()
