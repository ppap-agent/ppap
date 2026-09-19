"""Create application roles and database on an empty PostgreSQL cluster."""
from __future__ import annotations

from typing import Mapping

import psycopg
from psycopg import sql


def bootstrap_roles(env: Mapping[str, str] | None = None) -> None:
    from ppap_core.settings import Settings

    settings = Settings()
    env = env or {}
    secret = env.get("PPAP_PG_API_PASSWORD", settings.pg_api_password)
    roles = {
        env.get("PPAP_PG_MIGRATOR_USER", settings.pg_migrator_user): env.get(
            "PPAP_PG_MIGRATOR_PASSWORD", settings.pg_migrator_password
        ),
        env.get("PPAP_PG_API_USER", settings.pg_api_user): env.get("PPAP_PG_API_PASSWORD", secret),
        env.get("PPAP_PG_WORKER_USER", settings.pg_worker_user): env.get(
            "PPAP_PG_WORKER_PASSWORD", settings.pg_worker_password
        ),
    }
    user = env.get("PPAP_PG_BOOTSTRAP_USER", settings.pg_bootstrap_user)
    password = env.get("PPAP_PG_BOOTSTRAP_PASSWORD", settings.pg_bootstrap_password)
    host = env.get("PPAP_PG_HOST", settings.pg_host)
    port = env.get("PPAP_PG_PORT", str(settings.pg_port))
    database = env.get("PPAP_PG_DATABASE", settings.pg_database)
    migrator = env.get("PPAP_PG_MIGRATOR_USER", settings.pg_migrator_user)
    with psycopg.connect(f"postgresql://{user}:{password}@{host}:{port}/postgres", autocommit=True) as conn:
        for role, role_password in roles.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if exists:
                conn.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_password))
                )
            else:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_password))
                )
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(migrator)))
