"""CI sharding must preserve discovery, coverage and target isolation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.ci_shards import Target, discover_distributions, discover_integration, discover_modules, partition, partition_integration, with_postgres

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


def test_product_contract_shards_cover_every_file_once_and_stay_stable():
    files = discover_integration()
    shards = partition_integration(files, 3)
    assert all(shards)
    assert sorted(path for group in shards for path in group) == files
    assert shards == partition_integration(files, 3)


def test_reject_invalid_partition_sizes_and_duplicate_targets():
    with pytest.raises(ValueError):
        partition([Target("api", False)], 0)
    with pytest.raises(ValueError):
        partition([Target("api", False), Target("api", True)], 3)


def test_postgres_fixture_cleans_up_after_failed_test():
    failure = subprocess.CalledProcessError(1, ["uv", "run", "pytest"])
    with (
        patch("scripts.ci_shards.run", side_effect=[None, None, failure]) as execute,
        patch("scripts.ci_shards.subprocess.check_output", return_value="0.0.0.0:45678"),
        patch("scripts.ci_shards.subprocess.run") as direct,
    ):
        direct.return_value.returncode = 0
        with pytest.raises(subprocess.CalledProcessError):
            with_postgres(["uv", "run", "pytest"], "ppap-ci-test")
    assert execute.call_count == 3
    assert execute.call_args.kwargs["env"]["PGPORT"] == "45678"
    assert any(call.args[0][-3:] == ["down", "-v", "--remove-orphans"] for call in direct.call_args_list)


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
