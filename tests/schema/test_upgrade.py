"""Upgrade an empty database and the previous schema to latest via container psql."""
from __future__ import annotations

import os
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
COMPOSE = (
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "isolated-dev" / "compose.yaml"),
)


def migration_files() -> list[Path]:
    return sorted(path for path in MIGRATIONS.glob("*.sql") if path.name[:4].isdigit())


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


def _bootstrap_user() -> str:
    return os.environ.get("PPAP_PG_BOOTSTRAP_USER") or os.environ.get("PGUSER") or "ppap"


def _bootstrap_password() -> str:
    return os.environ.get("PPAP_PG_BOOTSTRAP_PASSWORD") or os.environ.get("PGPASSWORD") or "ppap_isolated_dev"


def _migrator_user() -> str:
    return os.environ.get("PPAP_PG_MIGRATOR_USER", "ppap_migrator")


def _migrator_password() -> str:
    return os.environ.get("PPAP_PG_MIGRATOR_PASSWORD", "ppap_isolated_dev")


def _sql_file_in_container(host_path: str) -> str:
    path = Path(host_path)
    path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    try:
        relative = path.relative_to(MIGRATIONS)
    except ValueError as exc:
        raise RuntimeError(f"schema SQL must live under migrations/: {host_path}") from exc
    return f"/migrations/{relative.as_posix()}"


def _container_psql_args(args: tuple[str, ...]) -> list[str]:
    mapped: list[str] = []
    expect_file = False
    for arg in args:
        if expect_file:
            mapped.append(_sql_file_in_container(arg))
            expect_file = False
            continue
        mapped.append(arg)
        expect_file = arg == "-f"
    return mapped


def _psql(user: str, password: str, database: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = [
        *COMPOSE,
        "exec",
        "-T",
        "-e",
        "PGHOST=127.0.0.1",
        "-e",
        "PGPORT=5432",
        "-e",
        f"PGUSER={user}",
        "-e",
        f"PGPASSWORD={password}",
        "postgres",
        "psql",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        *_container_psql_args(args),
    ]
    try:
        return subprocess.run(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, stdout=b"", stderr=str(exc).encode())


def _require(proc: subprocess.CompletedProcess[bytes], action: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{action}: {(proc.stderr or proc.stdout or b'').decode(errors='replace')[-4000:]}")


def _require_postgres(test: unittest.TestCase) -> None:
    probe = _psql(_bootstrap_user(), _bootstrap_password(), "postgres", "-Atc", "SELECT 1")
    if probe.returncode == 0:
        return
    detail = (probe.stderr or probe.stdout or b"").decode(errors="replace").strip()[-2000:]
    message = "postgres container is not reachable with container-native psql"
    if detail:
        message = f"{message}: {detail}"
    if os.environ.get("CI"):
        test.fail(message)
    test.skipTest(message)


def _applied(database: str) -> list[str]:
    proc = _psql(
        _migrator_user(),
        _migrator_password(),
        database,
        "-Atc",
        "SELECT version FROM product.schema_migrations ORDER BY version",
    )
    _require(proc, "list schema_migrations")
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
        apply = (MIGRATIONS / "apply.sql").read_text()
        for name in versions:
            self.assertIn(f"\\ir {name}", apply)
        self.assertEqual(_sql_file_in_container(str(MIGRATIONS / "apply.sql")), "/migrations/apply.sql")
        self.assertEqual(
            _container_psql_args(("-f", str(MIGRATIONS / "apply.sql"), "-Atc", "SELECT 1")),
            ["-f", "/migrations/apply.sql", "-Atc", "SELECT 1"],
        )

    def test_empty_and_previous_schema_reach_latest(self):
        _require_postgres(self)
        versions = [path.name for path in migration_files()]
        empty = "ppap_schema_empty_" + uuid.uuid4().hex[:8]
        previous = "ppap_schema_prev_" + uuid.uuid4().hex[:8]
        try:
            for name in (empty, previous):
                _require(
                    _psql(
                        _bootstrap_user(),
                        _bootstrap_password(),
                        "postgres",
                        "-c",
                        f"CREATE DATABASE {name} OWNER {_migrator_user()}",
                    ),
                    f"create {name}",
                )
            apply = str(MIGRATIONS / "apply.sql")
            _require(
                _psql(_migrator_user(), _migrator_password(), empty, "-f", apply),
                "apply empty",
            )
            self.assertEqual(_applied(empty), versions)
            _require(
                _psql(_migrator_user(), _migrator_password(), empty, "-f", apply),
                "re-apply empty",
            )
            self.assertEqual(_applied(empty), versions)
            for name in versions[:-1]:
                _require(
                    _psql(
                        _migrator_user(),
                        _migrator_password(),
                        previous,
                        "-f",
                        str(MIGRATIONS / name),
                    ),
                    f"apply {name}",
                )
            self.assertEqual(_applied(previous), versions[:-1])
            _require(
                _psql(
                    _migrator_user(),
                    _migrator_password(),
                    previous,
                    "-f",
                    str(MIGRATIONS / versions[-1]),
                ),
                "apply latest onto previous",
            )
            self.assertEqual(_applied(previous), versions)
            tables = _psql(
                _migrator_user(),
                _migrator_password(),
                empty,
                "-Atc",
                "SELECT tablename FROM pg_tables WHERE schemaname IN ('product', 'engine')",
            )
            _require(tables, "list tables")
            names = set((tables.stdout or b"").decode().split())
            self.assertIn("audit_rounds", names)
            self.assertIn("usage_events", names)
            self.assertIn("jobs", names)
        finally:
            for name in (empty, previous):
                _psql(
                    _bootstrap_user(),
                    _bootstrap_password(),
                    "postgres",
                    "-c",
                    f"DROP DATABASE IF EXISTS {name} WITH (FORCE)",
                )


if __name__ == "__main__":
    unittest.main()
