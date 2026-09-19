from __future__ import annotations

import os
import signal
import socket
import time

from psycopg.types.json import Jsonb

from ppap_core.db import connect
from ppap_core.settings import load_settings

STOP = False


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def worker_id(explicit: str) -> str:
    return explicit or f"{socket.gethostname()}-{os.getpid()}"


def run_claimed_job(kind: str, payload: dict) -> dict:
    """Execute one claimed job with in-repo checkers. Non-audit kinds stay no-ops."""
    if kind != "audit":
        return {"note": "claimed; execution not implemented"}
    from ppap_excel_audit_engine.app_runtime import run_audit_engine

    results = run_audit_engine(payload.get("audit") or payload, payload.get("rule") or {})
    return {
        "note": "in-repo engine",
        "results": [result.model_dump() for result in results],
    }


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = load_settings()
    ident = worker_id(settings.worker_id)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with connect(settings, "worker") as conn:
        while not STOP:
            conn.execute(
                """
                INSERT INTO engine.worker_heartbeats (worker_id, started_at, last_seen_at, git_sha)
                VALUES (%s, %s::timestamptz, now(), %s)
                ON CONFLICT (worker_id) DO UPDATE
                  SET last_seen_at = now(), git_sha = EXCLUDED.git_sha
                """,
                (ident, started, settings.git_sha),
            )
            row = conn.execute(
                """
                SELECT id, kind, payload FROM engine.jobs
                WHERE status = 'queued' AND (lease_until IS NULL OR lease_until < now())
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE engine.jobs
                    SET status = 'leased',
                        attempt = attempt + 1,
                        lease_owner = %s,
                        lease_until = now() + interval '30 seconds',
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (ident, row[0]),
                )
                diagnostic = run_claimed_job(row[1], row[2] or {})
                conn.execute(
                    """
                    INSERT INTO engine.attempts (id, job_id, attempt, worker_id, diagnostic)
                    SELECT gen_random_uuid(), id, attempt, %s, %s
                    FROM engine.jobs WHERE id = %s
                    """,
                    (ident, Jsonb(diagnostic), row[0]),
                )
            time.sleep(1)


if __name__ == "__main__":
    run()
