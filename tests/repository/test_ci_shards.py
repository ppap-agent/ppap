"""CI sharding must preserve discovery, coverage and target isolation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci_shards import Target, discover_distributions, discover_modules, partition, with_postgres

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("discover", [discover_modules, discover_distributions])
def test_shards_cover_every_discovered_target_exactly_once(discover):
    targets = discover()
    shards = partition(targets, 3)
    assert len(shards) == 3
    assert all(shards)
    assert sorted(target.name for group in shards for target in group) == sorted(target.name for target in targets)
    api = [target for target in targets if target.name in {"api", "ppap-api"}]
    assert len(api) == 1
    assert shards[0] == api


def test_python_discovery_keeps_dev_only_owner_and_service_postgres():
    targets = {target.name: target for target in discover_modules()}
    assert targets["corpus-contracts"].tests == "tests/corpus/contracts/tests"
    assert targets["api"].postgres and targets["worker"].postgres
    assert all(target.tests for target in targets.values())


def test_distribution_discovery_keeps_separate_clean_wheel_contract():
    targets = {target.name: target for target in discover_distributions()}
    assert "corpus-contracts" not in targets
    assert "product" in targets
    assert targets["api"].postgres and targets["worker"].postgres


def test_reject_invalid_partition_sizes_and_duplicate_targets():
    with pytest.raises(ValueError):
        partition([Target("api", False)], 0)
    with pytest.raises(ValueError):
        partition([Target("api", False), Target("api", True)], 3)


@pytest.mark.parametrize("silo", [False, True])
def test_service_fixture_lifetime(silo):
    # Retain the existing failed-PostgreSQL cleanup case; exercise the complete
    # successful product lifecycle, including Silo, through the same owner.
    failure = None if silo else subprocess.CalledProcessError(1, ["uv", "run", "pytest"])
    events = []

    def execute(command, *, env):
        events.append((command, env))
        if command == ["uv", "run", "pytest"] and failure:
            raise failure

    def direct(command, **kwargs):
        events.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0)

    with (
        patch("scripts.ci_shards.run", side_effect=execute),
        patch("scripts.ci_shards.subprocess.check_output", side_effect=["127.0.0.1:45678", "127.0.0.1:45679"]),
        patch("scripts.ci_shards.subprocess.run", side_effect=direct),
    ):
        if failure:
            with pytest.raises(subprocess.CalledProcessError):
                with_postgres(["uv", "run", "pytest"], "ppap-ci-test")
        else:
            with_postgres(["uv", "run", "pytest"], "ppap-ci-test", silo=True)
    assert events[1][0][-3:] == ["run", "--rm", "init-db"]
    command, env = events[2]
    assert command == ["uv", "run", "pytest"]
    assert env["PGPORT"] == "45678"
    assert events[-1][0][-3:] == ["down", "-v", "--remove-orphans"]
    if silo:
        assert events[0][0][-7:] == ["up", "-d", "postgres", "silo", "--wait", "--wait-timeout", "120"]
        assert env["PPAP_TEST_S3_ENDPOINT"] == "http://127.0.0.1:45679"
        assert len(events) == 4  # No network lifecycle operations between launch and child exit.


def test_ci_cli_module_invocation_discovers_all_targets():
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.ci_shards", "plan"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert f"modules: {len(discover_modules())} targets in 3 shards" in completed.stdout
    assert f"distributions: {len(discover_distributions())} targets in 3 shards" in completed.stdout
