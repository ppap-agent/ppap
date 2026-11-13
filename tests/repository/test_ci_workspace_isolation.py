"""GitHub-hosted jobs use ephemeral workspaces and explicit capability checks."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflows():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        yield path, yaml.safe_load(path.read_text())


def test_hosted_jobs_use_native_checkout_without_shared_runner_paths():
    for _, workflow in workflows():
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses, inputs = step.get("uses", ""), step.get("with", {})
                if uses.startswith("actions/checkout@"):
                    assert "path" not in inputs
                    assert inputs["persist-credentials"] is False
                if "COREPACK_HOME=" in step.get("run", ""):
                    assert "$RUNNER_TEMP/" in step["run"]
