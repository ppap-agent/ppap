from __future__ import annotations

import os
import signal
import socket
import tempfile
import time
from pathlib import Path

from ppap_api.store import AuthStore
from ppap_core.audits import (
    KIND_ENGINE,
    TARGET,
    evaluate_round,
    findings_from_engine_results,
    unused_reference_note,
)
from ppap_core.contracts import AuditInput, GroupContextInput, RuleInput
from ppap_core.db import connect
from ppap_core.logs import bind, configure as configure_logs, emit, reset
from ppap_core.settings import load_settings
from ppap_core.types import PpapDocumentType

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


def _rule_input(work) -> RuleInput:
    body = work.rule_body or {}
    return RuleInput(
        rule_id=str(body.get("rule_id") or work.round_id),
        rule_profile=body.get("rule_profile") or "v2",
        type=body.get("type") or "",
        rules=list(body.get("rules") or []),
        customers=list(body.get("customers") or []),
        models=list(body.get("models") or []),
        parts=list(body.get("parts") or []),
    )


def _audit_input(item, path: str) -> AuditInput:
    return AuditInput(
        audit_id=item.file_version_id,
        file_url=path,
        file_metadata={"original_filename": item.filename, "role": item.role},
        project_id="default",
    )


def _doc_type_key(audit: AuditInput) -> str | None:
    from ppap_excel_audit_engine.contract_mapper import infer_document_type

    inferred = infer_document_type(audit)
    if inferred is None or inferred is PpapDocumentType.UNKNOWN:
        return None
    return inferred.get_type_name()


def _write_temp(item, data: bytes) -> str:
    suffix = Path(item.filename).suffix or ".bin"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(data)
        return handle.name
    finally:
        handle.close()


def engine_runner(work, store: AuthStore):
    from ppap_excel_audit_engine.app_runtime import run_audit_engine

    targets = [item for item in work.inputs if item.role == TARGET]
    references = [item for item in work.inputs if item.role != TARGET]
    if not targets:
        raise ValueError("target_required")

    temps: list[str] = []
    try:
        audits_by_version: dict[str, AuditInput] = {}
        by_audit_id: dict[str, AuditInput] = {}
        by_doc_type: dict[str, list[AuditInput]] = {}
        reference_notes: list[str] = []
        for item in work.inputs:
            version = store.get_version(item.file_id, item.file_version_id)
            if version is None:
                if item.role == TARGET:
                    raise ValueError("missing_file")
                reference_notes.append(unused_reference_note(item.filename, "无法加载"))
                continue
            data = store.version_bytes(version)
            path = _write_temp(item, data)
            temps.append(path)
            audit = _audit_input(item, path)
            audits_by_version[item.file_version_id] = audit
            by_audit_id[item.file_version_id] = audit
            type_key = _doc_type_key(audit)
            if type_key:
                by_doc_type.setdefault(type_key, []).append(audit)
            if item.role != TARGET:
                if type_key:
                    reference_notes.append(unused_reference_note(item.filename))
                else:
                    reference_notes.append(unused_reference_note(item.filename, "未能识别文档类型，未作为待审完整检查"))

        rule = _rule_input(work)
        group_context = GroupContextInput(
            task_group_id=work.round_id,
            by_audit_id=by_audit_id,
            by_doc_type=by_doc_type,
            rules_by_id={rule.rule_id: rule},
        )
        findings: list[dict] = []
        checked: list[str] = []
        locator_gap = False
        for target in targets:
            audit = audits_by_version[target.file_version_id]
            results = run_audit_engine(audit, rule, group_context=group_context)
            mapped = findings_from_engine_results(results, item_key_prefix=target.file_version_id)
            findings.extend(mapped["findings"])
            checked.append(target.filename)
            locator_gap = locator_gap or bool((mapped.get("coverage") or {}).get("locator_gap"))
        if not findings:
            from ppap_core.audits import execution_error_result

            return execution_error_result("empty_engine_result")
        return {
            "findings": findings,
            "coverage": {
                "checked": checked,
                "unchecked": reference_notes,
                "locator_gap": locator_gap,
            },
            "note": "in-repo engine",
        }
    finally:
        for path in temps:
            Path(path).unlink(missing_ok=True)


def process_claimed(store: AuthStore, claimed, ident: str) -> dict:
    tokens = bind(
        job_id=claimed.id,
        attempt=claimed.attempt,
        round_id=claimed.round_id,
        tenant_id=claimed.tenant_id,
    )
    emit("job_claimed", kind=claimed.kind)
    try:
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
        runner = (lambda current: engine_runner(current, store)) if work.rule_kind() == KIND_ENGINE else None
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


def run() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = load_settings()
    configure_logs(settings)
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
