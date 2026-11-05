from __future__ import annotations

import os
import signal
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from audit.audits import (
    ANALYSIS_DEEP,
    EXECUTOR_STANDARD,
    STANDARD_EXECUTOR_REVISION,
    evaluate_round,
)
from product import build_product_store, run_standard_round
from product.db import connect
from product.logs import bind, emit, reset
from product.logs import configure as configure_logs
from product.rules import FILE_PREPARATION_KINDS
from product.settings import get_settings
from product.standard_policy import StandardExecutionPolicy
from product.store import AuthStore

from worker.intake import analyze_file, parse_rule_file, validate_file

STOP = False


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def worker_id(explicit: str) -> str:
    return explicit or f"{socket.gethostname()}-{os.getpid()}"


def run_claimed_job(kind: str, payload: dict) -> dict:
    """Execute one claimed job with in-repo checkers. Round jobs are completed by process_claimed."""
    if kind != "audit":
        return {"error": "unsupported_job_kind", "note": "unsupported_job_kind"}
    if payload.get("round_id"):
        return {"note": "round_audit", "round_id": payload.get("round_id")}
    from audit.engine.app_runtime import run_audit_engine

    results = run_audit_engine(payload.get("audit") or payload, payload.get("rule") or {})
    return {
        "note": "in-repo engine",
        "results": [result.model_dump() for result in results],
    }


def engine_runner(work, store: AuthStore):
    return run_standard_round(store, work)


def process_file_job(store, claimed, ident, ai=None) -> dict:
    from product.files import READY
    from product.rules import RuleParseError

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
            if claimed.kind == "file_analysis" and payload.get("task_id") and not store.recognition_job_current(claimed):
                obsolete = {"error": "recognition_superseded"}
                accepted = store.complete_job(claimed, ident, obsolete, "failed")
                emit("job_completed" if accepted else "job_result_discarded", outcome="failed", note="recognition_superseded")
                return obsolete
            version = store.get_version(payload["source_file_id"], payload["source_file_version_id"])
            if version is None or version.tenant_id != claimed.tenant_id or version.status != READY:
                raise RuleParseError("file_not_ready")
            if claimed.kind == "file_validation":
                result = validate_file(version.original_filename, store.version_bytes(version), scope=payload.get("scope"))
            elif claimed.kind == "file_analysis":
                scoped = payload.get("matching_scope") == "customer_document_type"
                candidates = payload["candidate_rules"] if scoped else store.list_rules(claimed.tenant_id)
                result = (
                    dict(payload["confirmed_match"])
                    if payload.get("confirmed_match") and scoped
                    else analyze_file(
                        version.original_filename,
                        store.version_bytes(version),
                        candidates,
                        ai=ai,
                        customer_preference=payload.get("customer_preference"),
                        scoped=scoped,
                        reference_only=bool(payload.get("reference_only")),
                        validation_scope=payload.get("validation_scope"),
                    )
                )
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
    from product.model_access import personal_model_scope

    # Absent identity is an unavailable scope, never permission to use LLM_*.
    user_id = store.model_user_for_job(claimed) if hasattr(store, "model_user_for_job") else ""
    with personal_model_scope(store, tenant_id=claimed.tenant_id or "", initiating_user_id=user_id):
        return _process_claimed(store, claimed, ident, rule_ai=rule_ai)


def _process_claimed(store: AuthStore, claimed, ident: str, *, rule_ai=None) -> dict:
    tokens = bind(
        job_id=claimed.id,
        attempt=claimed.attempt,
        round_id=claimed.round_id,
        tenant_id=claimed.tenant_id,
    )
    emit("job_claimed", kind=claimed.kind)
    try:
        if claimed.kind in {"report_pdf", "report_zip"}:
            return process_report_job(store, claimed, ident)
        if claimed.kind == "task_precheck":
            from worker.prechecks import process_task_precheck

            return process_task_precheck(store, claimed, ident)
        if claimed.kind in FILE_PREPARATION_KINDS:
            return process_file_job(store, claimed, ident, rule_ai)
        if claimed.kind != "audit" or not claimed.round_id:
            diagnostic = run_claimed_job(claimed.kind, claimed.payload or {})
            outcome = "failed" if diagnostic.get("error") else "succeeded"
            accepted = store.complete_job(claimed, ident, {**diagnostic, "findings": [], "coverage": {}}, outcome)
            emit("job_completed" if accepted else "job_result_discarded", outcome=outcome, note=diagnostic.get("note"))
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

        # Legacy jobs retain their original label; no invented configuration is
        # backfilled. New jobs must match before reading original bytes or AI IO.
        payload = claimed.payload or {}
        if payload.get("execution_policy") is not None or payload.get("executor_revision") != STANDARD_EXECUTOR_REVISION:
            try:
                policy = StandardExecutionPolicy.model_validate(payload.get("execution_policy"))
                matched = payload.get("executor") == EXECUTOR_STANDARD and payload.get("executor_revision") == policy.engine_revision() and policy == store.settings.standard_execution_policy()
            except ValueError:
                matched = False
            if not matched:
                result = {"findings": [], "coverage": {}, "failure": {"code": "executor_revision_mismatch", "retryable": False}, "note": "executor_revision_mismatch"}
                accepted = store.complete_job(claimed, ident, result, "failed")
                emit("job_completed" if accepted else "job_result_discarded", outcome="failed", note="executor_revision_mismatch")
                return result

        def runner(current):
            return engine_runner(current, store)

        error_type = None
        try:
            result = evaluate_round(work, engine_runner=runner)
        except Exception:
            from audit.audits import execution_error_result

            result = execution_error_result("engine_error")
            result["note"] = "round_audit_error"
            error_type = "engine_error"
        else:
            result["note"] = result.get("note") or "round_audit"
        # A failed result transaction is not an engine failure. Let the caller
        # retain the unfinished attempt instead of writing a fabricated ERR.
        store.complete_job(claimed, ident, result, "succeeded")
        emit("job_completed", outcome="succeeded", note=result.get("note"), error_type=error_type)
        return result
    finally:
        reset(tokens)


def process_report_job(store, claimed, ident) -> dict:
    from documents.audit_pdf import render_audit_pdf

    try:
        generated_at = datetime.now(timezone.utc).isoformat()
        with audit_lease(store, claimed, ident) as cancelled:
            if claimed.kind == "report_zip":
                result = store.build_report_package(claimed, generated_at, cancelled)
            else:
                body = render_audit_pdf({**claimed.payload["report_input"], "generated_at": generated_at})
                if cancelled.is_set():
                    raise ValueError("report_lease_unknown")
                result = store.write_audit_report(claimed, body, generated_at)
        outcome = "failed" if result.get("error") else "succeeded"
    except Exception:
        result, outcome = {"error": claimed.kind + "_failed"}, "failed"
        if claimed.kind == "report_zip":
            result["outcomes"] = [{"reference": source["reference"], "state": "failed", "reason": "ZIP 工作失败；原报告保留。"} for source in claimed.payload["sources"]]
    accepted = store.complete_job(claimed, ident, result, outcome)
    emit("job_completed" if accepted else "job_result_discarded", outcome=outcome, note=claimed.kind)
    return result


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
    from agent.client import DEEP_REQUEST_TIMEOUT, DeepClient, DeepError
    from agent.failures import failure_result
    from agent.mapping import product_result, spec_from_work
    from agent.protocol import DeepProgress
    from audit.audits import DEEP_PROTOCOL_REVISION, EXECUTOR_DEEP, frozen_executor

    settings = getattr(store, "settings", None)
    payload = claimed.payload if isinstance(claimed.payload, dict) else {}
    executor = payload.get("executor")
    revision = payload.get("executor_revision")
    try:
        _expected_executor, expected_revision = frozen_executor(ANALYSIS_DEEP, settings.deep_execution_revision() if settings else "")
    except ValueError:
        return failure_result("executor_revision_mismatch"), "failed"
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
    # Engine/OCR clients read this same process-lifetime settings object.
    # Configuration changes require a new process, not a hot mutation.
    settings = get_settings()
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
