"""One accepted audit, one guest process, explicit frozen clause entrypoints.

This is process/lifecycle isolation, not a hostile-code security sandbox. Object
and tenant authorization belong to the host accepting the ExecutionSnapshot.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any
from uuid import uuid4

from audit.checker_core import ExecutionSnapshot, FrozenTarget, checker_digest, clause_ids, validate_results
from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput, UnavailableSourceError
from audit.engine.rule_engine.context import GuardStop
from audit.engine.runtime import bind_runtime
from audit.standard_library import AuthoredCheckerError

from agent.execution.checker_runtime import CheckerRuntime

MAX_RESULT_BYTES = 16 * 1024 * 1024


class AuditRunError(RuntimeError):
    """A failed or interrupted run has no publishable partial success."""


class ClauseTimeout(BaseException):
    """Escape legacy catch-all Exception fallbacks without retrying a check."""


@dataclass
class CheckerContext:
    snapshot: ExecutionSnapshot
    target: FrozenTarget
    clause_id: str
    audit_file: AuditInput
    rule: RuleInput
    group_context: GroupContextInput
    sources: Mapping[str, bytes]
    paths: Mapping[str, Path]
    runtime: CheckerRuntime
    cache: dict[Any, Any]
    on_close: Callable[[Any], None]


@contextlib.contextmanager
def _clause_deadline(seconds: float):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid_clause_timeout")
    old = signal.getsignal(signal.SIGALRM)

    def expired(_signal, _frame):
        raise ClauseTimeout("checker_clause_timeout")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _load_program(files: Mapping[str, bytes], directory: Path) -> tuple[dict[str, ModuleType], dict[str, ModuleType | None]]:
    if set(files) - {"checker.py", "resources.py"} or "checker.py" not in files:
        raise AuditRunError("invalid_checker_program_files")
    directory.mkdir()
    for name, data in files.items():
        (directory / name).write_bytes(data)
    modules: dict[str, ModuleType] = {}
    saved: dict[str, ModuleType | None] = {stem: sys.modules.pop(stem, None) for stem in ("resources", "checker")}
    try:
        for stem in ("resources", "checker"):
            name = stem + ".py"
            if name not in files:
                continue
            # Preserve only a temporary local import alias; caller restores it
            # before any other Suite is loaded.
            spec = importlib.util.spec_from_file_location(f"ppap_suite_{uuid4().hex}", directory / name)
            assert spec is not None
            module = importlib.util.module_from_spec(spec)
            saved[spec.name] = sys.modules.get(spec.name)
            sys.modules[spec.name] = module
            sys.modules[stem] = module
            exec(compile(files[name], str(directory / name), "exec"), module.__dict__)
            modules[stem] = module
        return modules, saved
    except BaseException:
        _restore_modules(saved)
        raise


def _restore_modules(saved: Mapping[str, ModuleType | None]) -> None:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _activate_modules(modules: Mapping[str, ModuleType]) -> dict[str, ModuleType | None]:
    saved = {stem: sys.modules.pop(stem, None) for stem in ("resources", "checker")}
    for stem, module in modules.items():
        saved[module.__name__] = sys.modules.get(module.__name__)
        sys.modules[stem] = sys.modules[module.__name__] = module
    return saved


def _error(clause_id: str, status: str, code: str) -> AuditResultItem:
    return AuditResultItem(classification="规则审核", item=clause_id, priority="P0", status=status, suggestions=code, clause_id=clause_id)


def _evidence_references(result: AuditResultItem | dict[str, Any], target_id: str) -> dict[str, Any]:
    data = result.model_dump(mode="json") if isinstance(result, AuditResultItem) else dict(result)
    if "evidence_file_version_ids" in data:
        return data
    versions = {target_id}
    evidence = data.get("evidence") or {}
    for source in evidence.get("sources", ()):
        locator = source.get("locator", "")
        if isinstance(locator, str) and locator.startswith("{"):
            try:
                parsed = json.loads(locator)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("file_version_id"):
                versions.add(str(parsed["file_version_id"]))
    data["evidence_file_version_ids"] = sorted(versions)
    return data


def run_audit(
    snapshot: ExecutionSnapshot,
    sources: Mapping[str, bytes],
    programs: Mapping[str, Mapping[str, bytes]],
    *,
    runtime: CheckerRuntime | None = None,
    clause_timeout: float = 120,
    unavailable_sources: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Execute in the caller's guest; never spawn per target or per clause."""
    if not math.isfinite(clause_timeout) or clause_timeout <= 0:
        raise ValueError("invalid_clause_timeout")
    snapshot.validate_sources(sources, unavailable=unavailable_sources)
    expected = {target.binding.checker_digest for target in snapshot.targets}
    if set(programs) != expected or any(checker_digest(files) != digest for digest, files in programs.items()):
        raise AuditRunError("checker_program_digest_mismatch")
    runtime = runtime or CheckerRuntime(allow_model_tools=True)
    cache: dict[Any, Any] = {}
    loaded_programs: dict[str, dict[str, ModuleType]] = {}
    closeables: list[Any] = []
    findings, coverage, diagnostics = [], [], []
    with tempfile.TemporaryDirectory(prefix="ppap-audit-guest-") as work:
        root = Path(work)
        paths: dict[str, Path] = {}
        audits: dict[str, AuditInput] = {}
        rules = {
            source.file_version_id: RuleInput.model_validate({**source.to_dict()["rules"], "rule_id": source.rule_revision_id, "appendix": source.to_dict()["appendix"]})
            for source in snapshot.inputs
            if source.rules is not None
        }
        rules.update(
            {
                target.file_version_id: RuleInput.model_validate({**target.to_dict()["rules"], "rule_id": target.suite_revision_id, "appendix": target.to_dict()["appendix"]})
                for target in snapshot.targets
            }
        )
        for index, source in enumerate(snapshot.inputs):
            if source.file_version_id in unavailable_sources:
                continue
            # Display names never become writable paths or lookup authority.
            path = root / f"source-{index}{Path(source.filename).suffix.lower()}"
            path.write_bytes(sources[source.file_version_id])
            path.chmod(0o400)
            paths[source.file_version_id] = path
            audits[source.file_version_id] = AuditInput(
                audit_id=source.file_version_id,
                file_url=str(path),
                project_id=source.project_id,
                customer=source.customer,
                model=source.model,
                part=source.part,
                file_metadata={"file_version_id": source.file_version_id, "sha256": source.sha256, "role": source.role, "original_filename": source.filename},
            )
        by_type: dict[str, list[AuditInput]] = {}
        for source in snapshot.inputs:
            if source.document_type and source.file_version_id in audits:
                by_type.setdefault(source.document_type, []).append(audits[source.file_version_id])
        group = GroupContextInput(
            task_group_id=snapshot.round_id,
            by_audit_id=audits,
            by_doc_type=by_type,
            rules_by_id=rules,
            unavailable_files={source.file_version_id: source.document_type for source in snapshot.inputs if source.file_version_id in unavailable_sources},
        )
        try:
            from audit.usage import get_token_tracker, track_model_usage
            from documents.model_access import ModelUnavailable, model_access_scope, model_access_scoped
            from documents.ocr.paddle.reader import paddle_page_scope

            tracker = get_token_tracker()
            previous_request = tracker.get_current_request_id()
            request_id = tracker.start_request_tracking()
            model_scope = contextlib.nullcontext() if model_access_scoped() else model_access_scope(None)
            with model_scope, bind_runtime(runtime), paddle_page_scope(), track_model_usage():
                for index, target in enumerate(snapshot.targets):
                    if target.file_version_id in unavailable_sources:
                        results = [_evidence_references(_error(clause, "ERR", "checker_input_unavailable"), target.file_version_id) for clause in target.binding.entrypoints]
                        validated = validate_results(results, target=target, inputs=snapshot.inputs)
                        findings.extend(validated["findings"])
                        coverage.extend(validated["coverage"])
                        continue
                    if not target.binding.entrypoints:
                        # An unverified starter may contain arbitrary code but
                        # authorizes no execution, including import side effects.
                        validated = validate_results([], target=target, inputs=snapshot.inputs)
                        coverage.extend(validated["coverage"])
                        continue
                    digest = target.binding.checker_digest
                    if digest in loaded_programs:
                        modules = loaded_programs[digest]
                        saved = _activate_modules(modules)
                    else:
                        modules, saved = _load_program(programs[digest], root / f"program-{index}")
                        loaded_programs[digest] = modules
                    try:
                        entrypoints = {}
                        for entrypoint in set(target.binding.entrypoints.values()):
                            name, function = entrypoint.split(":", 1)
                            resolved = getattr(modules.get(name), function, None)
                            if not callable(resolved):
                                raise AuditRunError("checker_entrypoint_missing")
                            entrypoints[entrypoint] = resolved
                        results = []
                        for clause in clause_ids(target.rules):
                            selected_entrypoint = target.binding.entrypoints.get(clause)
                            if selected_entrypoint is None:
                                continue
                            context = CheckerContext(
                                snapshot,
                                target,
                                clause,
                                audits[target.file_version_id],
                                rules[target.file_version_id],
                                group,
                                MappingProxyType(dict(sources)),
                                MappingProxyType(paths),
                                runtime,
                                cache,
                                closeables.append,
                            )
                            try:
                                with _clause_deadline(clause_timeout):
                                    if selected_entrypoint == "checker:create_audit":
                                        from audit.standard_library import check_factory

                                        item = check_factory(context, entrypoints[selected_entrypoint])
                                    else:
                                        item = entrypoints[selected_entrypoint](context)
                                if item is None:
                                    item = _error(clause, "ERR", "checker_missing_result")
                            except ClauseTimeout:
                                item = _error(clause, "ERR", "checker_clause_timeout_external_usage_unknown")
                            except ModelUnavailable:
                                item = _error(clause, "UNABLE", "model_unavailable")
                            except UnavailableSourceError:
                                item = _error(clause, "UNABLE", "checker_reference_unavailable")
                            except GuardStop as exc:
                                item = _error(clause, str(exc.status), exc.message)
                            except AuthoredCheckerError as exc:
                                item = _error(clause, "ERR", str(exc))
                            except Exception as exc:
                                # Exception messages may contain originals or credentials.
                                item = _error(clause, "ERR", f"checker_failure:{type(exc).__name__}")
                            data = _evidence_references(item, target.file_version_id)
                            returned_clause = data.get("clause_id")
                            if isinstance(returned_clause, str) and returned_clause != clause and returned_clause in target.binding.entrypoints:
                                # A known clause is still not authority for this call.
                                # Drop the misattributed result, not the other clauses.
                                data = _evidence_references(_error(clause, "ERR", "checker_result_clause_mismatch"), target.file_version_id)
                            # Unknown clauses/references still reject the whole contract.
                            results.append(data)
                        diagnostics.extend(cache.pop(("authored-diagnostics", target.file_version_id), ()))
                        validated = validate_results(results, target=target, inputs=snapshot.inputs)
                        findings.extend(validated["findings"])
                        coverage.extend(validated["coverage"])
                    finally:
                        _restore_modules(saved)
            snapshot.validate_sources({identity: path.read_bytes() for identity, path in paths.items()}, unavailable=unavailable_sources)
            usage = tracker.end_request_tracking(request_id).to_dict()
            return {
                "input_digest": snapshot.input_digest,
                "findings": findings,
                "coverage": coverage,
                "diagnostics": diagnostics,
                "process_id": os.getpid(),
                "usage": usage,
                "unavailable_sources": sorted(unavailable_sources),
            }
        finally:
            if "request_id" in locals():
                tracker.cleanup_request(request_id)
                tracker.set_current_request_id(previous_request)
            for item in reversed(closeables):
                close = getattr(item, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
            runtime.close()


def _validate_output(snapshot: ExecutionSnapshot, result: dict[str, Any], *, unavailable_sources: frozenset[str] = frozenset()) -> dict[str, Any]:
    if result.get("input_digest") != snapshot.input_digest:
        raise AuditRunError("audit_output_identity_mismatch")
    if result.get("unavailable_sources", []) != sorted(unavailable_sources):
        raise AuditRunError("audit_output_availability_mismatch")
    known = {target.file_version_id for target in snapshot.targets}
    if any(item.get("file_version_id") not in known for item in result.get("findings", ())):
        raise AuditRunError("audit_output_target_mismatch")
    findings, coverage = [], []
    for target in snapshot.targets:
        checked = validate_results([item for item in result["findings"] if item["file_version_id"] == target.file_version_id], target=target, inputs=snapshot.inputs)
        if target.file_version_id in unavailable_sources and any(row["status"] != "ERR" or row["suggestions"] != "checker_input_unavailable" or row.get("evidence") for row in checked["findings"]):
            raise AuditRunError("audit_output_unavailable_source")
        if target.file_version_id not in unavailable_sources and any(set(row["evidence_file_version_ids"]) & unavailable_sources for row in checked["findings"]):
            raise AuditRunError("audit_output_unavailable_source")
        findings.extend(checked["findings"])
        coverage.extend(checked["coverage"])
    if result.get("coverage") != coverage:
        raise AuditRunError("audit_output_coverage_mismatch")
    return {**result, "findings": findings}


def run_audit_process(
    snapshot: ExecutionSnapshot,
    sources: Mapping[str, bytes],
    programs: Mapping[str, Mapping[str, bytes]],
    *,
    timeout: float = 600,
    clause_timeout: float = 120,
    cancelled: Callable[[], bool] = lambda: False,
    model_grant: dict[str, str] | None = None,
    allow_model_tools: bool = True,
    tool_env: Mapping[str, str] | None = None,
    unavailable_sources: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Local test harness only; production must use the existing ContainerRuntime.

    This helper is not a deployment or security boundary. Never retry an
    interrupted/non-idempotent guest.
    """
    if not math.isfinite(timeout) or not math.isfinite(clause_timeout) or timeout <= 0 or clause_timeout <= 0:
        raise ValueError("invalid_audit_timeout")
    snapshot.validate_sources(sources, unavailable=unavailable_sources)
    if cancelled():
        raise AuditRunError("audit_cancelled")
    if model_grant is not None:
        from agent.execution.guest_model import ModelGrant

        if not ModelGrant.model_validate(model_grant).matches(snapshot.model_identity):
            raise AuditRunError("audit_model_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix="ppap-audit-run-") as work:
        root = Path(work)
        payload = {
            "snapshot": snapshot.to_dict(),
            "sources": {key: base64.b64encode(value).decode() for key, value in sources.items()},
            "programs": {key: {name: base64.b64encode(data).decode() for name, data in files.items()} for key, files in programs.items()},
            "clause_timeout": clause_timeout,
            "allow_model_tools": allow_model_tools,
            "unavailable_sources": sorted(unavailable_sources),
        }
        request = root / "input.json"
        request.write_text(json.dumps(payload), encoding="utf-8")
        request.chmod(0o400)
        grant = root / "grant.json"
        grant.write_text(json.dumps(model_grant), encoding="utf-8")
        grant.chmod(0o400)
        output = root / "output.json"
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "VIRTUAL_ENV"}}
        env.update(tool_env or {})
        env.update(HOME=work, TMPDIR=work, PYTHONDONTWRITEBYTECODE="1")
        process = subprocess.Popen(
            [sys.executable, "-m", "agent.execution.audit_runtime", str(request), str(output), str(grant)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled():
                    raise AuditRunError("audit_cancelled_external_usage_unknown")
                if time.monotonic() >= deadline:
                    raise AuditRunError("audit_timeout_external_usage_unknown")
                time.sleep(0.02)
            if process.returncode != 0 or not output.is_file():
                raise AuditRunError("audit_guest_failed_external_usage_unknown")
            if output.stat().st_size > MAX_RESULT_BYTES:
                raise AuditRunError("audit_result_too_large")
            if cancelled():
                raise AuditRunError("audit_cancelled")
            return _validate_output(snapshot, json.loads(output.read_bytes()), unavailable_sources=unavailable_sources)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _main() -> None:
    request, output, credentials = map(Path, sys.argv[1:])
    payload = json.loads(request.read_bytes())
    grant = json.loads(credentials.read_bytes())
    # A missing grant must not use environment or deployment credentials.
    # Provider keys are never accepted by this entry.
    from documents.model_access import model_access_scope

    from agent.execution.guest_model import ModelGrant

    snapshot = ExecutionSnapshot.from_dict(payload["snapshot"])
    scope = model_access_scope(None)
    if grant is not None:
        from urllib.parse import urlsplit

        selected = ModelGrant.model_validate(grant)
        endpoint = urlsplit(selected.base_url)
        if endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or endpoint.scheme != "http":
            raise AuditRunError("invalid_execution_grant_endpoint")
        if not selected.matches(snapshot.model_identity):
            raise AuditRunError("audit_model_identity_mismatch")
        scope = selected.access_scope()
    with scope:
        result = run_audit(
            snapshot,
            {key: base64.b64decode(value, validate=True) for key, value in payload["sources"].items()},
            {key: {name: base64.b64decode(data, validate=True) for name, data in files.items()} for key, files in payload["programs"].items()},
            clause_timeout=payload["clause_timeout"],
            runtime=CheckerRuntime(allow_model_tools=payload.get("allow_model_tools", True)),
            unavailable_sources=frozenset(payload.get("unavailable_sources", ())),
        )
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _main()
