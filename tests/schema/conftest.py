"""Schema pytest fixtures. PostgreSQL remains the migration authority."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from product.files import ObjectExists, ObjectMissing

ROOT = Path(__file__).resolve().parents[2]


class MemoryObjects:
    """Schema-local object-storage boundary fake."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None:
        if key in self.data:
            raise ObjectExists()
        self.data[key] = body

    def get(self, key: str) -> bytes:
        if key not in self.data:
            raise ObjectMissing()
        return self.data[key]

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def sample_rule_body() -> dict:
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["测试客户"],
        "models": ["M1"],
        "parts": ["P1"],
        "rules": [
            {
                "id": "1",
                "category": "基本信息",
                "priority": "P0",
                "rule_to_check": "必须填写零件号",
                "type_of_rule": "table",
            }
        ],
    }


class SchemaTools:
    MIGRATIONS = ROOT / "migrations"
    COMPOSE = (
        "docker",
        "compose",
        "-f",
        str(ROOT / "deploy" / "isolated-dev" / "compose.yaml"),
    )

    def migration_files(self) -> list[Path]:
        return sorted(self.MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))

    def bootstrap_user(self) -> str:
        return os.environ.get("PPAP_PG_BOOTSTRAP_USER") or os.environ.get("PGUSER") or "ppap"

    def bootstrap_password(self) -> str:
        return os.environ.get("PPAP_PG_BOOTSTRAP_PASSWORD") or os.environ.get("PGPASSWORD") or "ppap_isolated_dev"

    def migrator_user(self) -> str:
        return os.environ.get("PPAP_PG_MIGRATOR_USER", "ppap_migrator")

    def migrator_password(self) -> str:
        return os.environ.get("PPAP_PG_MIGRATOR_PASSWORD", "ppap_isolated_dev")

    def sql_file_in_container(self, host_path: str) -> str:
        path = Path(host_path)
        path = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
        try:
            relative = path.relative_to(self.MIGRATIONS)
        except ValueError as exc:
            raise RuntimeError(f"schema SQL must live under migrations/: {host_path}") from exc
        return f"/migrations/{relative.as_posix()}"

    def container_psql_args(self, args: tuple[str, ...]) -> list[str]:
        mapped: list[str] = []
        expect_file = False
        for arg in args:
            if expect_file:
                mapped.append(self.sql_file_in_container(arg))
                expect_file = False
                continue
            mapped.append(arg)
            expect_file = arg == "-f"
        return mapped

    def psql(self, user: str, password: str, database: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        if os.environ.get("PPAP_LOCAL_POSTGRES"):
            return subprocess.run(
                ["psql", "-d", database, "-v", "ON_ERROR_STOP=1", *args],
                env={**os.environ, "PGUSER": user, "PGPASSWORD": password},
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
        command = [
            *self.COMPOSE,
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
            *self.container_psql_args(args),
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

    def apply(self, database: str, *, migrations_dir: str = "/migrations") -> subprocess.CompletedProcess[bytes]:
        if os.environ.get("PPAP_LOCAL_POSTGRES"):
            directory = str(self.MIGRATIONS) if migrations_dir == "/migrations" else migrations_dir
            return subprocess.run(
                ["sh", str(self.MIGRATIONS / "apply.sh")],
                env={
                    **os.environ,
                    "PPAP_PG_DATABASE": database,
                    "PPAP_PG_MIGRATOR_USER": self.migrator_user(),
                    "PPAP_PG_MIGRATOR_PASSWORD": self.migrator_password(),
                    "MIGRATIONS_DIR": directory,
                },
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
        command = [
            *self.COMPOSE,
            "exec",
            "-T",
            "-e",
            "PGHOST=127.0.0.1",
            "-e",
            "PGPORT=5432",
            "-e",
            f"PPAP_PG_MIGRATOR_USER={self.migrator_user()}",
            "-e",
            f"PPAP_PG_MIGRATOR_PASSWORD={self.migrator_password()}",
            "-e",
            f"PPAP_PG_DATABASE={database}",
            "-e",
            f"MIGRATIONS_DIR={migrations_dir}",
            "postgres",
            "sh",
            "/migrations/apply.sh",
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

    def container_sh(self, script: str) -> subprocess.CompletedProcess[bytes]:
        if os.environ.get("PPAP_LOCAL_POSTGRES"):
            return subprocess.run(["sh", "-c", script.replace("/migrations", str(self.MIGRATIONS))], cwd=ROOT, capture_output=True, check=False)
        command = [*self.COMPOSE, "exec", "-T", "postgres", "sh", "-c", script]
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

    def require(self, proc: subprocess.CompletedProcess[bytes], action: str) -> None:
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or b"").decode(errors="replace")[-4000:]
            raise RuntimeError(f"{action}: {detail}")

    def require_postgres(self) -> None:
        probe = self.psql(self.bootstrap_user(), self.bootstrap_password(), "postgres", "-Atc", "SELECT 1")
        if probe.returncode == 0:
            return
        detail = (probe.stderr or probe.stdout or b"").decode(errors="replace").strip()[-2000:]
        message = "postgres container is not reachable with container-native psql"
        if detail:
            message = f"{message}: {detail}"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    def applied(self, database: str) -> list[str]:
        proc = self.psql(
            self.migrator_user(),
            self.migrator_password(),
            database,
            "-Atc",
            "SELECT version FROM product.schema_migrations ORDER BY version",
        )
        self.require(proc, "list schema_migrations")
        text = (proc.stdout or b"").decode().strip()
        return [line for line in text.splitlines() if line]


@pytest.fixture
def schema_tools():
    return SchemaTools()


@pytest.fixture
def memory_objects_type():
    return MemoryObjects


@pytest.fixture
def sample_rule_body_factory():
    return sample_rule_body
