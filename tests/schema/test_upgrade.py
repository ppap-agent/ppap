"""Upgrade an empty database and the previous schema to latest via psql."""
from __future__ import annotations

import os
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "isolated-dev" / "compose.yaml"


def migration_files() -> list[Path]:
    return sorted(path for path in (ROOT / "migrations").glob("*.sql") if path.name[:4].isdigit())


def latest_version() -> str:
    files = migration_files()
    if not files:
        raise RuntimeError("no SQL migrations found")
    return files[-1].name


def previous_version() -> str:
    files = migration_files()
    if len(files) < 2:
        raise RuntimeError("need at least two migrations to upgrade from a previous schema")
    return files[-2].name


def _compose_exec(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "exec", "-T", *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _psql(database: str, *args: str) -> None:
    proc = _compose_exec("postgres", "psql", "-U", "ppap", "-d", database, "-v", "ON_ERROR_STOP=1", *args)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or b"").decode(errors="replace")[-4000:])


def _psql_migrator(database: str, *args: str) -> None:
    proc = _compose_exec(
        "postgres",
        "psql",
        "-U",
        "ppap_migrator",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        *args,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or b"").decode(errors="replace")[-4000:])


def _applied(database: str) -> list[str]:
    proc = _compose_exec(
        "postgres",
        "psql",
        "-U",
        "ppap_migrator",
        "-d",
        database,
        "-Atc",
        "SELECT version FROM product.schema_migrations ORDER BY version",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or b"").decode(errors="replace")[-4000:])
    text = (proc.stdout or b"").decode().strip()
    return [line for line in text.splitlines() if line]


class SchemaUpgradeTests(unittest.TestCase):
    def test_migration_files_are_forward_only(self):
        versions = [path.name for path in migration_files()]
        self.assertGreaterEqual(len(versions), 2)
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(latest_version(), versions[-1])
        self.assertEqual(previous_version(), versions[-2])
        self.assertTrue(all(name.endswith(".sql") for name in versions))
        apply = (ROOT / "migrations" / "apply.sql").read_text()
        for name in versions:
            self.assertIn(f"\\ir {name}", apply)

    def test_empty_and_previous_schema_reach_latest(self):
        probe = _compose_exec("postgres", "pg_isready", "-U", "ppap")
        if probe.returncode != 0:
            self.skipTest("isolated-dev postgres is not running")
        versions = [path.name for path in migration_files()]
        empty = "ppap_schema_empty_" + uuid.uuid4().hex[:8]
        previous = "ppap_schema_prev_" + uuid.uuid4().hex[:8]
        try:
            for name in (empty, previous):
                _psql("postgres", "-c", f"CREATE DATABASE {name} OWNER ppap_migrator")
            _psql_migrator(empty, "-f", "/migrations/apply.sql")
            self.assertEqual(_applied(empty), versions)
            _psql_migrator(empty, "-f", "/migrations/apply.sql")
            self.assertEqual(_applied(empty), versions)
            for name in versions[:-1]:
                _psql_migrator(previous, "-f", f"/migrations/{name}")
            self.assertEqual(_applied(previous), versions[:-1])
            _psql_migrator(previous, "-f", f"/migrations/{versions[-1]}")
            self.assertEqual(_applied(previous), versions)
            tables = _compose_exec(
                "postgres",
                "psql",
                "-U",
                "ppap_migrator",
                "-d",
                empty,
                "-Atc",
                "SELECT tablename FROM pg_tables WHERE schemaname IN ('product', 'engine')",
            )
            if tables.returncode != 0:
                raise RuntimeError((tables.stderr or b"").decode(errors="replace"))
            names = set((tables.stdout or b"").decode().split())
            self.assertIn("audit_rounds", names)
            self.assertIn("usage_events", names)
            self.assertIn("jobs", names)
        finally:
            for name in (empty, previous):
                _compose_exec(
                    "postgres",
                    "psql",
                    "-U",
                    "ppap",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    f"DROP DATABASE IF EXISTS {name} WITH (FORCE)",
                )


if __name__ == "__main__":
    unittest.main()
