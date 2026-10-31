"""Exercise test startup with production and isolated-dev port boundaries."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("suite", "postgres_binding", "expected_code"),
    [
        ("tests/integration/runtime", "", 0),
        ("tests/e2e", "127.0.0.1:54321", 0),
        ("tests/e2e", "", 1),
        ("tests/e2e", "0.0.0.0:54321", 1),
    ],
)
def test_compose_test_database_port_boundary(tmp_path, suite, postgres_binding, expected_code):
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *'port api 8080') echo '127.0.0.1:18080' ;;
  *'port web 4173') echo '127.0.0.1:14173' ;;
  *'port postgres 5432')
    echo queried > "$PORT_QUERY_LOG"
    printf '%s\\n' "$TEST_POSTGRES_BINDING"
    ;;
  *'up -d'*) ;;
  *) exit 99 ;;
esac
"""
    )
    uv = tmp_path / "uv"
    uv.write_text('#!/usr/bin/env bash\nprintf "%s" "${PGPORT:-unset}" > "$PYTEST_PORT_LOG"\n')
    docker.chmod(0o755)
    uv.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "PGPORT"}
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        TEST_POSTGRES_BINDING=postgres_binding,
        PORT_QUERY_LOG=str(tmp_path / "query"),
        PYTEST_PORT_LOG=str(tmp_path / "pytest-port"),
        PPAP_E2E_WORKERS="0",
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bash", str(root / "scripts/run-compose-tests.sh"), suite],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert (tmp_path / "query").exists() == (suite == "tests/e2e")
    if expected_code == 0:
        assert (tmp_path / "pytest-port").read_text() == ("54321" if suite == "tests/e2e" else "unset")
    else:
        assert not (tmp_path / "pytest-port").exists()
