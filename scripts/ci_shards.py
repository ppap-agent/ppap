#!/usr/bin/env python3
"""Run complete CI owner/clean-wheel suites in fewer isolated GitHub jobs.

The module suite retains one pytest process per owner.  The distribution suite
retains one clean wheel environment per member, as supplied by
validate_distribution.py.  Service owners get separate PostgreSQL fixtures.
Discovery is automatic: adding a new owner or production distribution doesn't
require maintaining a hard-coded GitHub Actions matrix.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.ci_heartbeat import run_with_heartbeat
from scripts.validate_distribution import production_members

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = "deploy/isolated-dev/compose.yaml"
DEFAULT_SHARDS = 3


@dataclass(frozen=True)
class Target:
    name: str
    postgres: bool
    tests: str | None = None


def discover_modules() -> list[Target]:
    targets = []
    for tests in sorted((ROOT / "packages").glob("*/tests")):
        if any(tests.rglob("test_*.py")):
            targets.append(Target(tests.parent.name, False, tests.relative_to(ROOT).as_posix()))
    for name in ("api", "worker", "mcp-client"):
        tests = ROOT / "apps" / name / "tests"
        if any(tests.rglob("test_*.py")):
            targets.append(Target(name, name in {"api", "worker"}, tests.relative_to(ROOT).as_posix()))
    corpus_tests = ROOT / "tests/corpus/contracts/tests"
    if any(corpus_tests.rglob("test_*.py")):
        targets.append(Target("corpus-contracts", False, corpus_tests.relative_to(ROOT).as_posix()))
    if not targets:
        raise RuntimeError("no Python owner test directories discovered")
    return targets


def discover_distributions() -> list[Target]:
    members = production_members()
    if not members:
        raise RuntimeError("no production distributions discovered")
    return [Target(name, member.postgres) for name, member in sorted(members.items())]


def partition(targets: list[Target], shard_count: int) -> list[list[Target]]:
    """Keep the slow API suite isolated; balance other owners by stable order."""
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    if len({target.name for target in targets}) != len(targets):
        raise ValueError("duplicate CI targets")
    shards: list[list[Target]] = [[] for _ in range(shard_count)]
    if shard_count == 1:
        shards[0].extend(targets)
    else:
        dedicated = [target for target in targets if target.name in {"api", "ppap-api"}]
        remaining = [target for target in targets if target not in dedicated]
        shards[0].extend(dedicated)
        for index, target in enumerate(remaining):
            shards[(index % (shard_count - 1)) + 1 if dedicated else index % shard_count].append(target)
    if {target.name for shard in shards for target in shard} != {target.name for target in targets}:
        raise RuntimeError("CI partition dropped a target")
    return shards


def discover_integration() -> list[Path]:
    files = sorted(path.relative_to(ROOT) for path in (ROOT / "tests/integration/product").rglob("test_*.py"))
    if not files:
        raise RuntimeError("no product contract tests discovered")
    return files


def partition_integration(files: list[Path], shard_count: int) -> list[list[Path]]:
    """Balance by source size, the stable proxy available without run history."""
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    shards: list[list[Path]] = [[] for _ in range(shard_count)]
    loads = [0] * shard_count
    for path in sorted(files, key=lambda item: ((ROOT / item).stat().st_size, str(item)), reverse=True):
        index = loads.index(min(loads))
        shards[index].append(path)
        loads[index] += (ROOT / path).stat().st_size
    return shards


def run_integration(shard: int, shard_count: int) -> None:
    """One PostgreSQL per shard: these files share a database and cannot join an xdist pool."""
    files = partition_integration(discover_integration(), shard_count)[shard]
    project = f"ppap-ci-i-{shard}-{os.environ.get('GITHUB_RUN_ID', 'local')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    report = ROOT / "ci-report"
    report.mkdir(exist_ok=True)
    command = ["uv", "run", "--no-sync", "pytest", *map(str, files), "-vv", "--durations=25", "-o", "faulthandler_timeout=120", f"--junitxml=ci-report/product-{shard}.xml"]
    with_postgres(command, project)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", shlex.join(command), flush=True)
    run_with_heartbeat(command, cwd=ROOT, env=env, timeout=1800)


def with_postgres(command: list[str], project: str) -> None:
    """Give each service target its own database, even within one shard."""
    compose = ["docker", "compose", "-p", project, "-f", COMPOSE_FILE]
    env = {**os.environ, "PPAP_TEST_POSTGRES_PORT": "0"}
    try:
        run([*compose, "up", "-d", "postgres", "--wait", "--wait-timeout", "120"], env=env)
        binding = subprocess.check_output([*compose, "port", "postgres", "5432"], cwd=ROOT, env=env, text=True).strip()
        port = binding.rsplit(":", 1)[-1]
        if not port.isdigit():
            raise RuntimeError(f"invalid PostgreSQL port binding: {binding!r}")
        run([*compose, "run", "--rm", "init-db"], env=env)
        run(command, env={**env, "PGPORT": port, "COMPOSE_PROJECT_NAME": project})
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        for diagnostic in ([*compose, "ps", "-a"], [*compose, "logs", "--no-color", "--timestamps", "postgres", "init-db"]):
            try:
                subprocess.run(diagnostic, cwd=ROOT, env=env, check=False)
            except OSError as error:
                print(f"::warning::PostgreSQL diagnostics failed for {project}: {error}", flush=True)
        raise
    finally:
        # As in the old per-member job, diagnostics and teardown must not
        # hide the original test failure. A failed teardown is still logged.
        try:
            cleanup = subprocess.run([*compose, "down", "-v", "--remove-orphans"], cwd=ROOT, env=env, check=False)
            if cleanup.returncode != 0:
                print(f"::warning::PostgreSQL cleanup failed for {project} (exit {cleanup.returncode})", flush=True)
        except OSError as error:
            print(f"::warning::PostgreSQL cleanup could not run for {project}: {error}", flush=True)


def run_target(target: Target, suite: str, project: str) -> None:
    if suite == "modules":
        if target.tests is None:
            raise RuntimeError(f"missing tests path for {target.name}")
        command = ["uv", "run", "--no-sync", "pytest", target.tests]
    else:
        command = [sys.executable, "scripts/validate_distribution.py", "--member", target.name]
    if target.postgres:
        with_postgres(command, project)
    else:
        run(command)
    if suite == "distributions" and target.name == "product":
        # Preserve the original missing-dependency negative control.
        run([sys.executable, "scripts/validate_distribution.py", "--prove-missing-dependency"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=["plan", "modules", "distributions", "integration"])
    parser.add_argument("--shard", type=int)
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    args = parser.parse_args()
    if args.shards < 1:
        parser.error("--shards must be positive")

    suites = ("modules", "distributions") if args.suite == "plan" else (args.suite,)
    if args.suite != "plan" and (args.shard is None or not 0 <= args.shard < args.shards):
        parser.error("provide --shard in [0, --shards)")

    if args.suite == "integration":
        run_integration(args.shard, args.shards)
        return

    for suite in suites:
        targets = discover_modules() if suite == "modules" else discover_distributions()
        groups = partition(targets, args.shards)
        print(f"{suite}: {len(targets)} targets in {len(groups)} shards", flush=True)
        for index, group in enumerate(groups):
            print(f"  shard {index}: {', '.join(item.name for item in group) or '(empty)'}", flush=True)
        if args.suite == "plan":
            continue

        failures = []
        for index, target in enumerate(groups[args.shard]):
            project = f"ppap-ci-{suite[0]}-{args.shard}-{index}-{os.environ.get('GITHUB_RUN_ID', 'local')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
            print(f"\n=== {suite}: {target.name} ===", flush=True)
            try:
                run_target(target, suite, project)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                print(f"::error title={suite}/{target.name}::{error}", flush=True)
                failures.append(target.name)
        if failures:
            parser.exit(1, f"{suite} shard {args.shard} FAILED: {', '.join(failures)}\n")
        print(f"{suite} shard {args.shard}: all {len(groups[args.shard])} targets passed", flush=True)


if __name__ == "__main__":
    main()
