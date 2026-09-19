from __future__ import annotations

import os
import signal
import socket
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from ppap_api.store import AuthStore
from ppap_core.audits import KIND_ENGINE, evaluate_round, findings_from_engine_results
from ppap_core.db import connect
from ppap_core.settings import load_settings

STOP = False


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def worker_id(explicit: str) -> str:
    return explicit or f"{socket.gethostname()}-{os.getpid()}"


def run_claimed_job(kind: str, payload: dict) -> dict:
    """Execute one claimed job with in-repo checkers. Round jobs are completed by process_claimed."""
    if kind != "audit":
        return {"note": "claimed; execution not implemented"}
    if payload.get("round_id"):
        return {"note": "round_audit", "round_id": payload.get("round_id")}
    from ppap_excel_audit_engine.app_runtime import run_audit_engine

    results = run_audit_engine(payload.get("audit") or payload, payload.get("rule") or {})
    return {
        "note": "in-repo engine",
        "results": [result.model_dump() for result in results],
    }


def engine_runner(work, store: AuthStore):
    from ppap_excel_audit_engine.app_runtime import run_audit_engine

    targets = [item for item in work.inputs if item.role == "target"]
    if not targets:
        raise ValueError("target_required")
    first = targets[0]
    version = store.get_version(first.file_id, first.file_version_id)
    if version is None:
        raise ValueError("missing_file")
    data = store.version_bytes(version)
    suffix = Path(first.filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        body = work.rule_body or {}
        audit = SimpleNamespace(
            audit_id=work.round_id,
            file_url=path,
            customer=None,
            model=None,
            part=None,
            content=None,
            file_metadata={"original_filename": first.filename},
            project_id="default",
        )
        rule = SimpleNamespace(
            rule_id=str(body.get("rule_id") or work.round_id),
            rule_profile=body.get("rule_profile") or "v2",
            type=body.get("type") or "",
            rules=list(body.get("rules") or []),
            customers=list(body.get("customers") or []),
            models=list(body.get("models") or []),
            parts=list(body.get("parts") or []),
        )
        results = run_audit_engine(audit, rule)
        mapped = findings_from_engine_results(results)
        mapped["note"] = "in-repo engine"
        return mapped
    finally:
        Path(path).unlink(missing_ok=True)


def process_claimed(store: AuthStore, claimed, ident: str) -> dict:
    if claimed.kind != "audit" or not claimed.round_id:
        diagnostic = run_claimed_job(claimed.kind, claimed.payload or {})
        store.complete_job(claimed, ident, {**diagnostic, "findings": [], "coverage": {}}, "succeeded")
        return diagnostic
    work = store.round_work(claimed.round_id)
    if work is None:
        result = {"note": "missing_round", "findings": [], "coverage": {}}
        store.complete_job(claimed, ident, result, "failed")
        return result
    runner = (lambda current: engine_runner(current, store)) if work.rule_kind() == KIND_ENGINE else None
    try:
        result = evaluate_round(work, engine_runner=runner)
        result["note"] = result.get("note") or "round_audit"
        store.complete_job(claimed, ident, result, "succeeded")
        return result
    except Exception:
        from ppap_core.audits import execution_error_result

        result = execution_error_result("engine_error")
        result["note"] = "round_audit_error"
        store.complete_job(claimed, ident, result, "succeeded")
        return result


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = load_settings()
    ident = worker_id(settings.worker_id)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = AuthStore(settings, role="worker")
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
            claimed = store.claim_job(ident)
            if claimed:
                process_claimed(store, claimed, ident)
                continue
            time.sleep(1)


if __name__ == "__main__":
    run()
