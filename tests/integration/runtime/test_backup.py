"""Live backup/restore against a docker compose production stack."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = ROOT / "deploy" / "compose.yaml"
COMPOSE_ENV = ROOT / "deploy" / "compose.env.example"


def _sha() -> str:
    forced = os.environ.get("PPAP_BUILD_SHA", "").strip()
    if forced:
        return forced
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _compose(*args: str, stdin: bytes | None = None, stdout=subprocess.PIPE) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["PPAP_BUILD_SHA"] = _sha()
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--env-file",
            str(COMPOSE_ENV),
            *args,
        ],
        cwd=str(ROOT),
        env=env,
        input=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
    )


def _require_ok(action: str, proc: subprocess.CompletedProcess[bytes]) -> None:
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode(errors="replace")[-4000:]
        raise RuntimeError(f"{action} failed: exit {proc.returncode}: {err}")


def _psql_scalar(sql: str) -> str:
    proc = _compose("exec", "-T", "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d ppap -Atc "{sql}"')
    _require_ok("psql", proc)
    return (proc.stdout or b"").decode().strip()


def _pause_app() -> None:
    _require_ok("stop app", _compose("stop", "api", "worker", "web"))


def _resume_app() -> None:
    _require_ok(
        "resume app",
        _compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "90", "api", "worker", "web"),
    )
    with urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=90) as response:
        body = json.loads(response.read().decode())
        if response.status != 200 or body.get("status") != "ready":
            raise RuntimeError(f"resume failed: {body!r}")


def _backup(dest: Path) -> None:
    dest.mkdir(parents=True)
    _pause_app()
    try:
        with (dest / "postgres.sql").open("wb") as fh:
            proc = _compose(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'pg_dump -U "$POSTGRES_USER" --no-owner --format=plain ppap',
                stdout=fh,
            )
        _require_ok("pg_dump", proc)
        with (dest / "objects.tar.gz").open("wb") as fh:
            proc = _compose(
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "tar",
                "init-db",
                "-C",
                "/data",
                "-czf",
                "-",
                ".",
                stdout=fh,
            )
        _require_ok("object tar", proc)
        (dest / "manifest.json").write_text(json.dumps({"postgres": "postgres.sql", "objects": "objects.tar.gz"}) + "\n")
    finally:
        _resume_app()


def _restore(backup: Path) -> None:
    _pause_app()
    try:
        terminate = (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'ppap' AND pid <> pg_backend_pid();"
        )
        _require_ok(
            "terminate",
            _compose("exec", "-T", "postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "{terminate}"'),
        )
        _require_ok(
            "drop",
            _compose(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ppap;"',
            ),
        )
        _require_ok(
            "create",
            _compose(
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE ppap OWNER ppap_migrator;"',
            ),
        )
        dump = (backup / "postgres.sql").read_bytes()
        proc = _compose(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'psql -U "$POSTGRES_USER" -d ppap -v ON_ERROR_STOP=1',
            stdin=dump,
        )
        _require_ok("restore postgres", proc)
        _require_ok(
            "wipe objects",
            _compose(
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "sh",
                "init-db",
                "-c",
                "rm -rf /data/.[!.]* /data/*",
            ),
        )
        objects = (backup / "objects.tar.gz").read_bytes()
        proc = _compose(
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "tar",
            "init-db",
            "-C",
            "/data",
            "-xzf",
            "-",
            stdin=objects,
        )
        _require_ok("restore objects", proc)
        _require_ok("restart silo", _compose("restart", "silo"))
    finally:
        _resume_app()


def _plant_canary() -> None:
    proc = _compose(
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d ppap -v ON_ERROR_STOP=1 -c "CREATE TABLE ppap_backup_canary (marker text); INSERT INTO ppap_backup_canary VALUES (\'restore-me\');"',
    )
    _require_ok("plant postgres canary", proc)
    proc = _compose("exec", "-T", "silo", "sh", "-c", "echo restore-me > /data/ppap-backup-canary")
    _require_ok("plant object canary", proc)


def _canary_present() -> bool:
    table = _psql_scalar("SELECT to_regclass('public.ppap_backup_canary')")
    proc = _compose("exec", "-T", "silo", "sh", "-c", "test -f /data/ppap-backup-canary")
    return table == "ppap_backup_canary" or proc.returncode == 0


class DeployBackupTests(unittest.TestCase):
    def test_backup_restore_drops_post_backup_state(self):
        tmp = Path(tempfile.mkdtemp(prefix="ppap-backup-"))
        try:
            first = tmp / "first"
            _backup(first)
            _plant_canary()
            self.assertTrue(_canary_present())
            _restore(first)
            self.assertFalse(_canary_present())
            _plant_canary()
            self.assertTrue(_canary_present())
            _restore(first)
            self.assertFalse(_canary_present())
            with urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=10) as response:
                body = json.loads(response.read().decode())
                self.assertEqual(response.status, 200)
                self.assertEqual(body["status"], "ready")
                self.assertEqual(body["postgres"]["state"], "ok")
                self.assertEqual(body["object_storage"]["state"], "ok")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
