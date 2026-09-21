from __future__ import annotations

import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path

from ppap_api.store import AuthStore
from ppap_core.audits import (
    ANALYSIS_DEEP,
    KIND_PRESENCE,
    TARGET,
    evaluate_round,
    findings_from_engine_results,
    presence_finding,
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


def _rule_input(item) -> RuleInput:
    if not item.rule_version_id or item.rule_body is None:
        raise ValueError("rule_required")
    body = item.rule_body
    builtin_rules = []
    if body.get("kind") != KIND_PRESENCE:
        from ppap_excel_audit_engine.routing import resolve_engine_agent_class
        from ppap_excel_audit_engine.checkers.rule_declarations import discover_declared_rules

        filename = item.filename
        if Path(filename).suffix.lower() == ".doc":
            filename = str(Path(filename).with_suffix(".docx"))
        agent_cls = resolve_engine_agent_class(
            PpapDocumentType.from_string(body.get("type") or ""), filename,
            body.get("rule_profile") or "v2",
        )
        # PDF delegates execute the corresponding Excel checker's declarations.
        agent_cls = getattr(agent_cls, "TARGET_EXCEL_AGENT_CLASS", None) or agent_cls
        declarations = discover_declared_rules(agent_cls) if agent_cls else ()
        if not declarations:
            raise ValueError("builtin_rules_unavailable")
        builtin_rules = [
            {"id": str(index), "category": description, "priority": "P0",
             "rule_to_check": description, "type_of_rule": "text"}
            for index, (_method, description) in enumerate(declarations, 1)
        ]
    return RuleInput(
        rule_id=item.rule_version_id,
        rule_profile=body.get("rule_profile") or "v2",
        type=body.get("type") or "",
        rules=builtin_rules,
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
    inferred = PpapDocumentType.infer_from_signals(filename=audit.file_metadata.get("original_filename", ""), minimum_score=8, min_margin=4)
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
    if not targets:
        raise ValueError("target_required")
    rules_by_version = {item.file_version_id: _rule_input(item) for item in targets}

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
            type_key = rules_by_version[item.file_version_id].type if item.role == TARGET else _doc_type_key(audit)
            if type_key:
                by_doc_type.setdefault(type_key, []).append(audit)
            if item.role != TARGET:
                if type_key:
                    reference_notes.append(unused_reference_note(item.filename))
                else:
                    reference_notes.append(unused_reference_note(item.filename, "未能识别文档类型，未作为待审完整检查"))

        group_context = GroupContextInput(
            task_group_id=work.round_id,
            by_audit_id=by_audit_id,
            by_doc_type=by_doc_type,
            rules_by_id={rule.rule_id: rule for rule in rules_by_version.values()},
        )
        findings: list[dict] = []
        checked: list[str] = []
        locator_gap = False
        for target in targets:
            if target.rule_kind() == KIND_PRESENCE:
                findings.append(presence_finding(target))
                checked.append(target.filename)
                continue
            audit = audits_by_version[target.file_version_id]
            results = run_audit_engine(audit, rules_by_version[target.file_version_id], group_context=group_context)
            mapped = findings_from_engine_results(results, target=target)
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


def process_rule_parse(store, claimed, ident, ai=None) -> dict:
    from ppap_core.files import READY
    from ppap_core.rules import RuleParseError, parse_rule_file

    stopped = threading.Event()

    def renew():
        while not stopped.wait(10):
            try:
                if not store.renew_job_lease(claimed, ident):
                    return
            except Exception:
                emit("rule_parse_lease_unknown", job_id=claimed.id, attempt=claimed.attempt)
                return

    renewal = threading.Thread(target=renew, daemon=True)
    renewal.start()
    try:
        try:
            payload = claimed.payload
            version = store.get_version(payload["source_file_id"], payload["source_file_version_id"])
            if version is None or version.tenant_id != claimed.tenant_id or version.status != READY:
                raise RuleParseError("file_not_ready")
            if claimed.kind == "file_analysis":
                from ppap_core.file_analysis import analyze_file

                result = analyze_file(version.original_filename, store.version_bytes(version), store.list_rules(claimed.tenant_id), ai=ai)
                result["source_file_version_id"] = version.id
            else:
                parsed = parse_rule_file(version.original_filename, store.version_bytes(version), document_type=payload["document_type"], ai=ai)
                result = {"title": Path(version.original_filename).stem, "body": parsed, "source_file_version_id": version.id}
            outcome = "succeeded"
        except RuleParseError as exc:
            result, outcome = {"error": str(exc)}, "failed"
        except Exception:
            result, outcome = {"error": "file_analysis_failed" if claimed.kind == "file_analysis" else "rule_parse_failed"}, "failed"
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
        if claimed.kind in {"rule_parse", "file_analysis"}:
            return process_rule_parse(store, claimed, ident, rule_ai)
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
            result, outcome = run_deep_round(store, claimed, work)
            accepted = store.complete_job(claimed, ident, result, outcome)
            emit(
                "job_completed" if accepted else "job_result_discarded",
                outcome=outcome,
                note=result.get("note") or "deep_audit",
            )
            return result
        runner = lambda current: engine_runner(current, store)
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


def run_deep_round(store: AuthStore, claimed, work) -> tuple[dict, str]:
    """Deep execution boundary. Never falls back to the in-repo Standard engine."""
    from ppap_core.audits import DEEP_PROTOCOL_REVISION, EXECUTOR_DEEP, execution_error_result, frozen_executor
    from ppap_deep.client import DeepClient, DeepError
    from ppap_deep.mapping import product_result, spec_from_work

    settings = getattr(store, "settings", None)
    payload = claimed.payload if isinstance(claimed.payload, dict) else {}
    executor = payload.get("executor")
    revision = payload.get("executor_revision")
    _expected_executor, expected_revision = frozen_executor(
        ANALYSIS_DEEP,
        getattr(settings, "deep_executor_revision", "") if settings else "",
    )
    if work.analysis_mode != ANALYSIS_DEEP or executor != EXECUTOR_DEEP:
        return execution_error_result("deep_executor_mismatch"), "failed"
    if revision != expected_revision:
        return execution_error_result("executor_revision_mismatch"), "failed"
    socket_path = str(getattr(settings, "deep_controller_socket", "") if settings else "").strip()
    if not socket_path:
        return execution_error_result("deep_runtime_unavailable"), "failed"
    try:
        spec = spec_from_work(work, executor, revision, DEEP_PROTOCOL_REVISION)
        response = DeepClient(socket_path).execute(spec)
        result = product_result(response, work)
        result["note"] = result.get("note") or "deep_audit"
        return result, "succeeded"
    except DeepError as exc:
        return execution_error_result(exc.code), "failed"
    except Exception:
        return execution_error_result("deep_execution_failed"), "failed"


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
