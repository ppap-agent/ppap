"""The CI heartbeat must not turn failed or stalled jobs into successes."""

import subprocess
import sys

import pytest

from scripts.ci_heartbeat import run_with_heartbeat


def test_heartbeat_keeps_successful_subprocess_result(tmp_path, capfd):
    run_with_heartbeat(
        [sys.executable, "-c", "import time; time.sleep(.12)"],
        cwd=tmp_path,
        interval=0.04,
        timeout=2,
    )
    assert "CI heartbeat:" in capfd.readouterr().out


def test_heartbeat_propagates_nonzero_exit(tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_with_heartbeat([sys.executable, "-c", "raise SystemExit(37)"], cwd=tmp_path, timeout=2)
    assert error.value.returncode == 37


def test_heartbeat_fails_closed_when_deadline_expires(tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_with_heartbeat(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            interval=0.04,
            timeout=0.12,
        )
    assert error.value.returncode == 124
