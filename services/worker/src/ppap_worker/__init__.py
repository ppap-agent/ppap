from __future__ import annotations

import os
import signal
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ppap_application import analyze_file, build_product_store, parse_rule_file, run_standard_round, validate_file
from ppap_core.audits import (
    ANALYSIS_DEEP,
    evaluate_round,
)
from ppap_core.db import connect
from ppap_core.logs import bind, emit, reset
from ppap_core.logs import configure as configure_logs
from ppap_core.settings import load_settings
from ppap_product_store import AuthStore

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
    return run_standard_round(store, work)


def process_file_job(store, claimed, ident, ai=None) -> dict:
    from ppap_core.files import READY
    from ppap_core.rules import RuleParseError

    stopped = threading.Event()

    def renew():
        while not stopped.wait(10):
            try:
                if not store.renew_job_lease(claimed, ident):
                    return
            except Exception:
                emit("file_job_lease_unknown", job_id=claimed.id, attempt=claimed.attempt)
                return

    renewal = threading.Thread(target=renew, daemon=True)
    renewal.start()
    try:
        try:
            payload = claimed.payload
            version = store.get_version(payload["source_file_id"], payload["source_file_version_id"])
            if version is None or version.tenant_id != claimed.tenant_id or version.status != READY:
                raise RuleParseError("file_not_ready")
            if claimed.kind == "file_validation":
                result = validate_file(version.original_filename, store.version_bytes(version))
            elif claimed.kind == "file_analysis":
                result = analyze_file(version.original_filename, store.version_bytes(version), store.list_rules(claimed.tenant_id), ai=ai)
                result["source_file_version_id"] = version.id
            else:
                parsed = parse_rule_file(version.original_filename, store.version_bytes(version), document_type=payload["document_type"], ai=ai)
                result = {"title": Path(version.original_filename).stem, "body": parsed, "source_file_version_id": version.id}
            outcome = "succeeded"
        except RuleParseError as exc:
            result, outcome = {"error": str(exc)}, "failed"
        except Exception:
            result, outcome = {"error": f"{claimed.kind}_failed"}, "failed"
        accepted = store.complete_job(claimed, ident, result, outcome)
        emit("job_completed" if accepted else "job_result_discarded", outcome=outcome, note=claimed.kind)
        return result
    finally:
        stopped.set()
        renewal.join(timeout=1)


def process_claimed(store: AuthStore, claimed, ident: str, *, rule_ai=None) -> dict:
    tokens = bind(
        job_id=claimed.id,
        attempt=claimed.attempt,
        round_id=claimed.round_id,
        tenant_id=claimed.tenant_id,
    )
    emit("job_claimed", kind=claimed.kind)
    try:
        if claimed.kind in {"rule_parse", "file_analysis", "file_validation"}:
            return process_file_job(store, claimed, ident, rule_ai)
        if claimed.kind != "audit" or not claimed.round_id:
            diagnostic = run_claimed_job(claimed.kind, claimed.payload or {})
            store.complete_job(claimed, ident, {**diagnostic, "findings": [], "coverage": {}}, "succeeded")
            emit("job_completed", outcome="succeeded", note=diagnostic.get("note"))
            return diagnostic
        work = store.round_work(claimed.round_id)
        if work is None:
            result = {"note": "missing_round", "findings": [], "coverage": {}}
            store.complete_job(claimed, ident, result, "failed")
            emit("job_completed", outcome="failed", note="missing_round")
            return result
        if work.tenant_id:
            tokens.extend(bind(tenant_id=work.tenant_id))
        if work.analysis_mode == ANALYSIS_DEEP:
            result, outcome = run_deep_round(store, claimed, work, ident)
            accepted = store.complete_job(claimed, ident, result, outcome)
            emit(
                "job_completed" if accepted else "job_result_discarded",
                outcome=outcome,
                note=result.get("note") or "deep_audit",
            )
            return result

        def runner(current):
            return engine_runner(current, store)

        try:
            result = evaluate_round(work, engine_runner=runner)
            result["note"] = result.get("note") or "round_audit"
            store.complete_job(claimed, ident, result, "succeeded")
            emit("job_completed", outcome="succeeded", note=result.get("note"))
            return result
        except Exception:
            from ppap_core.audits import execution_error_result

            result = execution_error_result("engine_error")
            result["note"] = "round_audit_error"
            store.complete_job(claimed, ident, result, "succeeded")
            emit("job_completed", outcome="succeeded", note="round_audit_error", error_type="engine_error")
            return result
    finally:
        reset(tokens)


@contextmanager
def audit_lease(store, claimed, ident):
    """Keep ownership throughout model work; uncertainty stops paid work."""
    stopped, cancelled = threading.Event(), threading.Event()

    def renew():
        due = time.monotonic() + 10
        while not stopped.wait(0.5):
            if STOP:
                cancelled.set()
                return
            if time.monotonic() < due:
                continue
            try:
                if not store.renew_job_lease(claimed, ident):
                    cancelled.set()
                    return
            except Exception:
                cancelled.set()
                emit("audit_lease_unknown", job_id=claimed.id, attempt=claimed.attempt)
                return
            due = time.monotonic() + 10

    renewal = threading.Thread(target=renew, daemon=True)
    renewal.start()
    try:
        yield cancelled
    finally:
        stopped.set()
        renewal.join(timeout=1)


def run_deep_round(store: AuthStore, claimed, work, ident: str = "") -> tuple[dict, str]:
    """Deep execution boundary. Never falls back to the in-repo Standard engine."""
    from ppap_core.audits import DEEP_PROTOCOL_REVISION, EXECUTOR_DEEP, frozen_executor
    from ppap_deep.client import DEEP_REQUEST_TIMEOUT, DeepClient, DeepError
    from ppap_deep.failures import failure_result
    from ppap_deep.mapping import product_result, spec_from_work
    from ppap_deep.protocol import DeepProgress

    settings = getattr(store, "settings", None)
    payload = claimed.payload if isinstance(claimed.payload, dict) else {}
    executor = payload.get("executor")
    revision = payload.get("executor_revision")
    _expected_executor, expected_revision = frozen_executor(
        ANALYSIS_DEEP,
        getattr(settings, "deep_executor_revision", "") if settings else "",
    )
    if work.analysis_mode != ANALYSIS_DEEP or executor != EXECUTOR_DEEP:
        return failure_result("deep_executor_mismatch"), "failed"
    if revision != expected_revision:
        return failure_result("executor_revision_mismatch"), "failed"
    socket_path = str(getattr(settings, "deep_controller_socket", "") if settings else "").strip()
    if not socket_path:
        return failure_result("deep_runtime_unavailable"), "failed"
    try:
        spec = spec_from_work(work, executor, revision, DEEP_PROTOCOL_REVISION)
        spec = spec.model_copy(update={"job_id": claimed.id, "attempt": claimed.attempt})
        with audit_lease(store, claimed, ident) as cancelled:

            def progress(state):
                safe = DeepProgress.model_validate(state).model_dump(exclude_none=True)
                if not store.audit_progress(claimed, ident, safe):
                    cancelled.set()
                    raise DeepError("deep_lease_lost")

            response = DeepClient(socket_path, timeout=DEEP_REQUEST_TIMEOUT).execute(
                spec,
                cancelled=cancelled,
                on_progress=progress,
            )
            if cancelled.is_set():
                raise DeepError("deep_lease_lost")
        result = product_result(response, work)
        result["note"] = result.get("note") or "deep_audit"
        return result, "succeeded"
    except DeepError as exc:
        return failure_result(exc.code), "failed"
    except Exception:
        return failure_result("deep_execution_failed"), "failed"


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = load_settings()
    configure_logs(settings)
    ident = worker_id(settings.worker_id)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = build_product_store(settings, role="worker")
    while not STOP:
        try:
            with connect(settings, "worker") as conn:
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
        except Exception:
            time.sleep(2)
            continue
        if claimed:
            process_claimed(store, claimed, ident)
            continue
        time.sleep(1)


if __name__ == "__main__":
    run()
