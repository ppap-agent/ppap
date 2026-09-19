"""Forward-only SQL migrations for product and engine schemas."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS product.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def migration_files() -> list[Path]:
    return sorted(path for path in MIGRATIONS.glob("*.sql") if path.name[:4].isdigit())


def migration_versions() -> list[str]:
    return [path.name for path in migration_files()]


def previous_version() -> str:
    versions = migration_versions()
    if len(versions) < 2:
        raise RuntimeError("need at least two migrations to upgrade from a previous schema")
    return versions[-2]


def latest_version() -> str:
    versions = migration_versions()
    if not versions:
        raise RuntimeError("no SQL migrations found")
    return versions[-1]


def apply_migrations(conn, *, stop_before: str | None = None) -> list[str]:
    conn.execute("CREATE SCHEMA IF NOT EXISTS product")
    conn.execute(SCHEMA_MIGRATIONS_SQL)
    applied = {row[0] for row in conn.execute("SELECT version FROM product.schema_migrations")}
    newly: list[str] = []
    for path in migration_files():
        if stop_before and path.name == stop_before:
            break
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("INSERT INTO product.schema_migrations (version) VALUES (%s)", (path.name,))
        newly.append(path.name)
    return newly
