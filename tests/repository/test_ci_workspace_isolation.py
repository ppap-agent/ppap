"""Guard CI checkout isolation across the co-located self-hosted runner instances."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# GitHub assigns at most one job to a runner at a time. Different runner
# owners need separate tracked files, environments and generated artifacts.
CHECKOUTS = (
    ("web", "repository"),
    ("repository", "distribution_closure"),
    ("distribution_closure", "python_modules"),
    ("python_modules", "schema"),
    ("schema", "corpus"),
    ("corpus", "integration"),
    ("integration", "ci_gate"),
)


def test_all_owner_jobs_use_a_bounded_runner_checkout():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    for job, following_job in CHECKOUTS:
        section = workflow.split(f"  {job}:\n", 1)[1].split(f"  {following_job}:\n", 1)[0]
        checkout = ".ci-checkouts/${{ runner.name }}"
        assert f"path: {checkout}" in section, job
        assert f"working-directory: ${{{{ github.workspace }}}}/{checkout}" in section, job
        assert section.count("persist-credentials: false") == 1, job
        assert section.count("uses: actions/checkout@") == 1, job
        assert "cache-dependency-glob: uv.lock" in section, job
        assert section.count("run: bash scripts/ci_assert_worktree.sh") == 1, job
        assert "uses: ./.github/actions/assert-docker" not in section, job
        for step in re.split(r"(?m)^      - ", section)[1:]:
            if re.search(r"(?m)^(?:run:|        run:)", step):
                assert f"working-directory: ${{{{ github.workspace }}}}/{checkout}" in step, job
    assert "  ci_gate:\n" in workflow


def test_node_paths_and_preview_stay_inside_unique_checkout():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    web = workflow.split("  web:\n", 1)[1].split("  repository:\n", 1)[0]
    repository = workflow.split("  repository:\n", 1)[1].split("  distribution_closure:\n", 1)[0]
    checkout = ".ci-checkouts/${{ runner.name }}"
    assert f"node-version-file: {checkout}/.node-version" in web
    assert f"working-directory: ${{{{ github.workspace }}}}/{checkout}/apps/web" in web
    assert web.count("/apps/web") == 2
    assert "COREPACK_HOME=$RUNNER_TEMP/corepack/${GITHUB_RUN_ID}/${GITHUB_JOB}" in web
    repository_node_version = checkout + "/.node-version"
    assert f"node-version-file: {repository_node_version}" in repository


def test_preflight_runs_from_each_isolated_checkout():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    script = (ROOT / "scripts/ci_assert_docker.sh").read_text()
    assert "set -euo pipefail" in script
    assert "docker info --format" in script
    assert "docker compose version" in script
    assert "docker buildx version" in script
    assert workflow.count("run: bash scripts/ci_assert_docker.sh") == 6
    assert "uses: ./.github/actions/assert-docker" not in workflow


def test_other_workflows_cannot_clean_a_sibling_runners_checkout():
    for name in ("v3-poc-live.yml", "insforge-lifecycle.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()
        assert "path: .ci-checkouts/${{ runner.name }}" in workflow
        assert "working-directory: ${{ github.workspace }}/.ci-checkouts/${{ runner.name }}" in workflow
        assert "run: bash scripts/ci_assert_worktree.sh" in workflow
        assert "uses: ./.github/actions/assert-docker" not in workflow


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
