"""Browser CI must exercise the same isolated Core as accepted product audits."""

import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = "deploy/isolated-dev/deep-live.yaml"


def test_core_overlay_owns_only_project_scoped_socket_and_real_readiness():
    overlay = yaml.safe_load((ROOT / OVERLAY).read_text())
    assert overlay["volumes"] == {"deep-socket": None}
    services = overlay["services"]
    deep = services["deep"]
    assert deep["environment"]["PPAP_SERVICE"] == "deep"
    assert deep["healthcheck"]["test"] == ["CMD", "python", "-m", "agent.readiness"]
    for name in ("api", "worker", "deep"):
        assert services[name]["volumes"] == ["deep-socket:/run/ppap-deep"]
        assert services[name]["environment"]["PPAP_DEEP_CONTROLLER_SOCKET"] == "/run/ppap-deep/controller.sock"
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["deep"]["condition"] == "service_healthy"
    assert "docker.sock" not in (ROOT / OVERLAY).read_text()


def test_native_compose_resolves_one_core_identity_and_keeps_production_separate():
    # Native Compose remains the merge/interpolation authority. This needs the
    # same Docker capability already required by the repository CI owner.
    sha = "a" * 40
    env = {**os.environ, "PPAP_BUILD_SHA": sha}
    resolved = json.loads(
        subprocess.check_output(
            ["docker", "compose", "-p", "core-contract", "-f", "deploy/isolated-dev/compose.yaml", "-f", OVERLAY, "config", "--format", "json"],
            cwd=ROOT,
            env=env,
            text=True,
        )
    )
    services = resolved["services"]
    assert services["deep"]["image"] == "ppap-deep:" + sha
    assert resolved["volumes"]["deep-socket"]["name"] == "core-contract_deep-socket"
    for key in ("PPAP_DEEP_EXECUTOR_REVISION", "PPAP_DEEP_SANDBOX_PROFILE", "PPAP_BUILD_SHA"):
        assert len({services[name]["environment"][key] for name in ("api", "worker", "deep")}) == 1
    for name in ("api", "worker", "deep"):
        mounted = next(row for row in services[name]["volumes"] if row["target"] == "/run/ppap-deep")
        assert mounted["type"] == "volume" and mounted["source"] == "deep-socket"
    assert "ports" not in services["deep"]
    assert "ports" not in services["silo"]
