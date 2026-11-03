"""GitHub-hosted jobs use ephemeral workspaces and explicit capability checks."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflows():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        yield path, yaml.safe_load(path.read_text())


def test_all_actions_use_github_hosted_runners():
    jobs = [(path.name, name, job) for path, workflow in workflows() for name, job in workflow["jobs"].items()]
    assert jobs
    for workflow, name, job in jobs:
        assert job["runs-on"] == "ubuntu-24.04", (workflow, name)


def test_hosted_jobs_use_native_checkout_without_shared_runner_paths():
    for path, workflow in workflows():
        assert ".ci-checkouts" not in path.read_text()
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses, inputs = step.get("uses", ""), step.get("with", {})
                if uses.startswith("actions/checkout@"):
                    assert "path" not in inputs
                    assert inputs["persist-credentials"] is False
                if uses.startswith("actions/setup-node@"):
                    assert inputs["node-version-file"] == ".node-version"
                if uses.startswith("astral-sh/setup-uv@"):
                    assert inputs.get("working-directory", "${{ github.workspace }}") == "${{ github.workspace }}"
                if "COREPACK_HOME=" in step.get("run", ""):
                    assert "$RUNNER_TEMP/" in step["run"]


def test_docker_preflight_requires_supported_runtime_tools():
    script = (ROOT / "scripts/ci_assert_docker.sh").read_text()
    assert "set -euo pipefail" in script
    assert "docker info --format" in script
    assert "docker compose version" in script
    assert "docker buildx version" in script
