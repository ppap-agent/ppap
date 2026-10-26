"""Guard CI checkout isolation across the co-located self-hosted runner instances."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ".ci-checkouts/${{ runner.name }}"
OWNER = "${{ github.workspace }}/" + CHECKOUT


def workspace_jobs():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        for name, job in workflow["jobs"].items():
            steps = job.get("steps", [])
            if any(step.get("uses", "").startswith("actions/checkout@") or "working-directory" in step for step in steps):
                yield f"{path.name}:{name}", steps


def assert_owned(path, root=OWNER):
    assert isinstance(path, str) and (path == root or path.startswith(root + "/")), path
    assert ".." not in path[len(root) :].split("/"), path


def test_all_owner_jobs_use_a_bounded_runner_checkout():
    jobs = list(workspace_jobs())
    assert jobs, "No workspace jobs found"
    for name, steps in jobs:
        assert any(step.get("uses", "").startswith("actions/checkout@") for step in steps), name
        assert any(step.get("run") == "bash scripts/ci_assert_worktree.sh" for step in steps), name
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["path"] == CHECKOUT, name
                assert step["with"]["persist-credentials"] is False, name
            if "run" in step:
                assert_owned(step.get("working-directory"))


def test_node_paths_and_preview_stay_inside_unique_checkout():
    for _name, steps in workspace_jobs():
        corepack_cache = [line for step in steps for line in step.get("run", "").splitlines() if "COREPACK_HOME=" in line]
        if any("corepack enable" in step.get("run", "") for step in steps):
            assert corepack_cache
        for line in corepack_cache:
            assert all(part in line for part in ("$RUNNER_TEMP/", "${GITHUB_RUN_ID}", "${GITHUB_JOB}"))
        for step in steps:
            uses, inputs = step.get("uses", ""), step.get("with", {})
            if uses.startswith("actions/setup-node@"):
                assert_owned(inputs["node-version-file"], CHECKOUT)
            if uses.startswith("astral-sh/setup-uv@"):
                assert_owned(inputs["working-directory"])
                if "cache-dependency-glob" in inputs:
                    assert inputs["cache-dependency-glob"] == "uv.lock"


def test_docker_preflight_requires_supported_runtime_tools():
    script = (ROOT / "scripts/ci_assert_docker.sh").read_text()
    assert "set -euo pipefail" in script
    assert "docker info --format" in script
    assert "docker compose version" in script
    assert "docker buildx version" in script


def test_worktree_guard_accepts_only_the_current_runners_physical_directory(tmp_path):
    workspace = tmp_path / "workspace"
    owner = workspace / ".ci-checkouts/cursor"
    neighbor = workspace / ".ci-checkouts/cursor-ppap"
    for directory in (owner, neighbor):
        directory.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(directory)], check=True)
    script = ROOT / "scripts/ci_assert_worktree.sh"

    def run(directory, name, run_id):
        return subprocess.run(
            ["bash", str(script)],
            cwd=directory,
            env={**os.environ, "GITHUB_WORKSPACE": str(workspace), "RUNNER_NAME": name, "GITHUB_RUN_ID": run_id},
            capture_output=True,
            text=True,
        ).returncode

    assert run(owner, "cursor", "old-run") == 0
    assert run(owner, "cursor", "new-run") == 0
    assert run(neighbor, "cursor-ppap", "new-run") == 0
    assert run(owner, "cursor-ppap", "new-run") != 0
    assert run(workspace, "cursor", "new-run") != 0
    alias = workspace / ".ci-checkouts/alias"
    alias.symlink_to(owner, target_is_directory=True)
    assert run(alias, "alias", "new-run") != 0
