"""Live backup/restore/rollback against a `python tools/dev.py deploy` stack."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]

from tools.dev import cmd_backup, cmd_restore, cmd_rollback, latest_backup, _compose_run
from tools.paths import git_sha


def _sha() -> str:
    return os.environ.get("PPAP_BUILD_SHA") or git_sha() or "unknown"


def _psql_scalar(sql: str) -> str:
    proc = _compose_run(
        _sha(),
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        f'psql -U "$POSTGRES_USER" -d ppap -Atc "{sql}"',
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode(errors="replace"))
    return (proc.stdout or b"").decode().strip()


def _plant_canary() -> None:
    proc = _compose_run(
        _sha(),
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d ppap -v ON_ERROR_STOP=1 -c "CREATE TABLE ppap_backup_canary (marker text); INSERT INTO ppap_backup_canary VALUES (\'restore-me\');"',
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode(errors="replace"))
    proc = _compose_run(
        _sha(),
        "exec",
        "-T",
        "silo",
        "sh",
        "-c",
        "echo restore-me > /data/ppap-backup-canary",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or b"").decode(errors="replace"))


def _canary_present() -> bool:
    table = _psql_scalar("SELECT to_regclass('public.ppap_backup_canary')")
    proc = _compose_run(_sha(), "exec", "-T", "silo", "sh", "-c", "test -f /data/ppap-backup-canary")
    return table == "ppap_backup_canary" or proc.returncode == 0


class DeployBackupTests(unittest.TestCase):
    def test_backup_restore_and_rollback_drop_post_backup_state(self):
        self.assertEqual(cmd_backup(), 0)
        backup = latest_backup()
        _plant_canary()
        self.assertTrue(_canary_present())
        self.assertEqual(cmd_restore([str(backup)]), 0)
        self.assertFalse(_canary_present())
        _plant_canary()
        self.assertTrue(_canary_present())
        self.assertEqual(cmd_rollback(), 0)
        self.assertFalse(_canary_present())
        import json
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=10) as response:
            body = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
            self.assertEqual(body["status"], "ready")
            self.assertEqual(body["postgres"]["state"], "ok")
            self.assertEqual(body["object_storage"]["state"], "ok")
