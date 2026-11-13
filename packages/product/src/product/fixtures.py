"""Isolated-dev fixtures for object-authorization tests. Not an S02 OAuth proof."""

from __future__ import annotations

from pathlib import Path

TENANT_A = "00000000-0000-4000-8000-00000000000a"
TENANT_B = "00000000-0000-4000-8000-00000000000b"
USER_ALICE = "00000000-0000-4000-8000-0000000000a1"
USER_BOB = "00000000-0000-4000-8000-0000000000b2"
USER_CAROL = "00000000-0000-4000-8000-0000000000c3"
RESOURCE_A = "00000000-0000-4000-8000-0000000000aa"
RESOURCE_B = "00000000-0000-4000-8000-0000000000bb"
RULE_A = "00000000-0000-4000-8000-0000000000a5"
RULE_B = "00000000-0000-4000-8000-0000000000b5"
APP_ID = "isolated-dev-feishu-app"
PRESENCE_RULE_BODY = {"kind": "presence"}

SUBJECTS = {
    "alice": {"user_id": USER_ALICE, "tenant_id": TENANT_A, "tenant_key": "isolated-dev-a", "subject": "ou_alice"},
    "bob": {"user_id": USER_BOB, "tenant_id": TENANT_A, "tenant_key": "isolated-dev-a", "subject": "ou_bob"},
    "carol": {"user_id": USER_CAROL, "tenant_id": TENANT_B, "tenant_key": "isolated-dev-b", "subject": "ou_carol"},
}


def test_database_name() -> str:
    """This test process's own database; tests never run in the isolated-dev `ppap` database."""
    import os

    return "ppap_test_" + os.environ.get("PYTEST_XDIST_WORKER", "main")


def create_test_database(host: str, port: int, migrations: Path) -> str:
    """Drop, recreate and migrate this process's test database from the repository's `migrations` directory.

    The caller names the directory: an installed package has no repository beside it.
    """
    import psycopg
    from psycopg import sql

    from product.settings import ISOLATED_DEV_SECRET

    files = sorted(migrations.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"no migrations in {migrations}; refusing to create an empty test database")
    name = test_database_name()
    with psycopg.connect(host=host, port=port, dbname="postgres", user="ppap", password=ISOLATED_DEV_SECRET, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER ppap_migrator").format(sql.Identifier(name)))
    with psycopg.connect(host=host, port=port, dbname=name, user="ppap_migrator", password=ISOLATED_DEV_SECRET, autocommit=True) as conn:
        for migration in files:
            conn.execute(migration.read_text(encoding="utf-8"))
    return name
