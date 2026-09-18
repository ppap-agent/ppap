from __future__ import annotations

import os
import signal
import socket
import time

from psycopg.types.json import Jsonb

from ppap_core.db import connect
from ppap_core.settings import load_settings
from ppap_worker.execute import InputConflict, execute_claimed_job, fail_claimed_job

STOP = False


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def worker_id(explicit: str) -> str:
    return explicit or f"{socket.gethostname()}-{os.getpid()}"


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
            claimed = conn.execute(
                """
                UPDATE engine.jobs
                SET status = 'leased',
                    attempt = attempt + 1,
                    lease_owner = %s,
                    lease_until = now() + make_interval(secs => 900),
                    updated_at = now()
                WHERE id = (
                    SELECT id FROM engine.jobs
                    WHERE status = 'queued' AND (lease_until IS NULL OR lease_until < now())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, tenant_id, kind, payload, attempt
                """,
                (ident,),
            ).fetchone()
            if claimed:
                    conn.execute(
                        """
                        INSERT INTO engine.attempts (id, job_id, attempt, worker_id, diagnostic)
                        VALUES (gen_random_uuid(), %s, %s, %s, %s)
                        """,
                        (claimed[0], claimed[4], ident, Jsonb({"note": "claimed"})),
                    )
                    job = {
                        "id": claimed[0],
                        "tenant_id": str(claimed[1]),
                        "kind": claimed[2],
                        "payload": claimed[3],
                        "attempt": claimed[4],
                    }
                    if job["kind"] == "audit.execute":
                        try:
                            execute_claimed_job(settings, conn, job, ident)
                        except InputConflict as exc:
                            fail_claimed_job(conn, job, ident, str(exc) or "input_conflict")
                        except Exception:
                            fail_claimed_job(conn, job, ident, "execution_failed")
            time.sleep(1)


if __name__ == "__main__":
    run()
