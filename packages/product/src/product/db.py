from __future__ import annotations

import psycopg

from product.settings import Settings


def connect(settings: Settings, role: str) -> psycopg.Connection:
    return psycopg.connect(settings.dsn(role), autocommit=True)  # type: ignore[arg-type]


def postgres_ready(settings: Settings) -> dict[str, str]:
    try:
        with connect(settings, "api") as conn:
            conn.execute("SELECT 1")
            present = conn.execute("SELECT to_regclass('engine.jobs')").fetchone()
        if not present or present[0] is None:
            return {"state": "unavailable"}
        return {"state": "ok"}
    except Exception:
        return {"state": "unavailable"}
