"""Compose test startup pins only the API/Web bindings; no suite reads a host PostgreSQL port."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("suite", ["tests/integration/runtime", "tests/e2e"])
def test_compose_tests_publish_only_api_and_web_bindings(tmp_path, suite):
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *'port api 8080') echo '127.0.0.1:18080' ;;
  *'port web 4173') echo '127.0.0.1:14173' ;;
  *'up -d'*) ;;
  *) exit 99 ;;
esac
"""
    )
    uv = tmp_path / "uv"
    uv.write_text('#!/usr/bin/env bash\nprintf "%s %s %s" "${PGPORT:-unset}" "$PPAP_PUBLIC_API_URL" "$PPAP_WEB_URL" > "$PYTEST_ENV_LOG"\n')
    docker.chmod(0o755)
    uv.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "PGPORT"}
    env.update(PATH=f"{tmp_path}:{env['PATH']}", PYTEST_ENV_LOG=str(tmp_path / "pytest-env"), PPAP_E2E_WORKERS="0")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["bash", str(root / "scripts/run-compose-tests.sh"), suite], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "pytest-env").read_text() == "unset http://127.0.0.1:18080 http://127.0.0.1:14173"
