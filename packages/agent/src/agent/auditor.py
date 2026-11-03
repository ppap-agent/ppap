"""Product versions -> real semantic auditor -> validated product findings."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput
from audit.protocol import ControllerFault
from audit.storage_errors import ObjectExists, ObjectMissing, ObjectTooLarge

from agent.checker_spec import CheckerArtifact, CheckerJobSpec, CheckerProgramBundle
from agent.client import DeepError
from agent.execution.budget import DEEP_TOTAL_SECONDS
from agent.execution.secure_files import read_json
from agent.protocol import ExecuteRequest, ExecuteResponse
from agent.run import ExecutionContext
from agent.tools import ParserLimitError

MAX_BUNDLE_BYTES = 16 * 1024 * 1024
FAULTS = {
    "INPUT_INTEGRITY_ERROR": "deep_integrity_mismatch",
    "PARSER_LIMIT": "deep_parser_limit",
    "MODEL_TIMEOUT": "deep_timeout",
    "MODEL_PROTOCOL_ERROR": "deep_model_protocol",
    "OUTPUT_VALIDATION_ERROR": "deep_output_invalid",
    "EVIDENCE_VALIDATION_ERROR": "deep_output_invalid",
    "RESOURCE_LIMIT": "deep_budget_exhausted",
    "CANCELLED": "deep_cancelled",
    "ISOLATION_UNAVAILABLE": "deep_runtime_unavailable",
    "CONTROLLER_UNAVAILABLE": "deep_runtime_unavailable",
}


class Objects(Protocol):
    def get(self, key: str, *, max_bytes: int | None = None) -> bytes: ...
    def put_exclusive(self, key: str, body: bytes, content_type: str) -> None: ...


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class SemanticRuntime:
    """One adapter, existing storage and queue; no second task or artifact registry."""

    def __init__(self, runtime, objects: Objects, *, timeout: float = DEEP_TOTAL_SECONDS):
        self.runtime, self.objects, self.timeout = runtime, objects, timeout

    def ready(self) -> bool:
        return self.runtime.ready()

    def _put(self, key: str, value: object) -> None:
        try:
            self.objects.put_exclusive(key, encoded(value), "application/json")
        except ObjectExists:
            pass

    def execute(self, spec: ExecuteRequest, files: dict[str, bytes], *, context: ExecutionContext) -> ExecuteResponse:
        try:
            if spec.resume_attempt is not None:
                resumed = self._resume_fixed_checker(spec, files, context)
                if resumed is not None:
                    return resumed
            else:
                stored = self._stored_checker_response(spec, files, context)
                if stored is not None:
                    return stored
                if getattr(self.runtime, "adapts_checkers", False):
                    return self._adapt_round(spec, files, context)
            raise DeepError("deep_output_invalid")
        except DeepError:
            raise
        except ControllerFault as exc:
            raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
        except TimeoutError as exc:
            raise DeepError("deep_timeout") from exc
        except ParserLimitError as exc:
            raise DeepError("deep_parser_limit") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise DeepError("deep_output_invalid") from exc

    def _attempt_prefix(self, spec: ExecuteRequest) -> str:
        tenant = hashlib.sha256(spec.tenant_id.encode()).hexdigest()
        job = hashlib.sha256(spec.job_id.encode()).hexdigest()
        return f"deep/{tenant}/{spec.executor_revision}/rounds/{spec.round_id}/{job}-{spec.attempt}"

    def _checker_recovery_key(self, spec: ExecuteRequest) -> str:
        return self._attempt_prefix(spec) + "/checker-recovery.json"

    def _checker_usage_key(self, spec: ExecuteRequest) -> str:
        return self._attempt_prefix(spec) + "/checker-usage.json"

    def _resume_fixed_checker(self, spec: ExecuteRequest, files: dict[str, bytes], context: ExecutionContext) -> ExecuteResponse | None:
        """Finish a checker attempt from its fixed program. RulePlan attempts return None."""
        source = spec.model_copy(update={"attempt": spec.resume_attempt, "resume_attempt": None})
        prior = self._read_checker_recovery(source)
        if prior is not None:
            if prior.uncertain or prior.job_id != spec.job_id or prior.attempt != spec.resume_attempt:
                raise DeepError("deep_model_protocol")
            raise DeepError(prior.code)
        replayed = self._stored_checker_response(source, files, context, partial=True)
        if replayed is None:
            return None
        recorded = self._read_checker_usage(source)
        if recorded is None:
            return replayed
        return replayed.model_copy(update={"usage": recorded.as_usage(), "usage_source": recorded.usage_source})

    def _remember_checker_usage(self, spec: ExecuteRequest, response: ExecuteResponse) -> None:
        from agent.checker_program import CheckerUsage

        names = ("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests")
        record = CheckerUsage(
            job_id=spec.job_id,
            attempt=spec.attempt,
            usage_source=response.usage_source,
            **{key: int(response.usage.get(key, 0) or 0) for key in names},
        )
        self._put(self._checker_usage_key(spec), record.model_dump(mode="json"))

    def _read_checker_usage(self, spec: ExecuteRequest):
        from agent.checker_program import CheckerUsage

        try:
            raw = self.objects.get(self._checker_usage_key(spec), max_bytes=65536)
        except (ObjectMissing, ObjectTooLarge):
            return None
        return CheckerUsage.model_validate(json.loads(raw))

    def _read_checker_recovery(self, spec: ExecuteRequest):
        from agent.checker_program import CheckerRecovery

        try:
            raw = self.objects.get(self._checker_recovery_key(spec), max_bytes=65536)
        except (ObjectMissing, ObjectTooLarge):
            return None
        return CheckerRecovery.model_validate(json.loads(raw))

    def _reject_unconfirmed_prior_attempt(self, spec: ExecuteRequest) -> None:
        """A later claim must not start model work after unknown checker usage."""
        if spec.attempt <= 1:
            return
        previous = spec.model_copy(update={"attempt": spec.attempt - 1})
        try:
            raw = self.objects.get(self._checker_recovery_key(previous), max_bytes=65536)
        except ObjectMissing:
            return
        except ObjectTooLarge:
            self._remember_checker_failure(spec, "deep_model_protocol")
            raise DeepError("deep_model_protocol") from None
        try:
            record = json.loads(raw)
            from agent.checker_program import CheckerRecovery

            earlier = CheckerRecovery.model_validate(record)
        except (ValueError, TypeError):
            self._remember_checker_failure(spec, "deep_model_protocol")
            raise DeepError("deep_model_protocol") from None
        if earlier.uncertain or earlier.job_id != spec.job_id or earlier.attempt != previous.attempt:
            self._remember_checker_failure(spec, "deep_model_protocol")
            raise DeepError("deep_model_protocol")

    def _remember_checker_failure(self, spec: ExecuteRequest, code: str) -> None:
        from agent.checker_program import CheckerRecovery

        uncertain = code in {"deep_model_protocol", "deep_timeout", "deep_execution_failed", "deep_runtime_unavailable", "deep_budget_exhausted"}
        if not uncertain:
            return
        self._put(self._checker_recovery_key(spec), CheckerRecovery(job_id=spec.job_id, attempt=spec.attempt, uncertain=True, code=code).model_dump(mode="json"))

    def _load_fixed_artifact(
        self,
        spec: ExecuteRequest,
        audit_file: AuditInput,
        rule: RuleInput,
        file_bytes: bytes,
        *,
        role: str,
        group_context: GroupContextInput | None,
    ) -> CheckerArtifact | None:
        from agent.checker_program import input_digest

        digest = input_digest(
            audit_file,
            rule,
            file_sha256=hashlib.sha256(file_bytes).hexdigest(),
            role=role,
            group_context=group_context,
        )
        try:
            stored = self.objects.get(f"{self._attempt_prefix(spec)}/checkers/{digest}.json", max_bytes=MAX_BUNDLE_BYTES)
        except (ObjectMissing, ObjectTooLarge):
            return None
        artifact = CheckerArtifact.model_validate(json.loads(stored))
        if artifact.input_digest != digest or artifact.executor_revision != spec.executor_revision:
            raise DeepError("deep_output_identity_mismatch")
        return artifact

    def _read_start_artifact(self, spec: ExecuteRequest, key: str) -> CheckerArtifact | None:
        try:
            stored = self.objects.get(key, max_bytes=MAX_BUNDLE_BYTES)
        except (ObjectMissing, ObjectTooLarge):
            return None
        try:
            artifact = CheckerArtifact.model_validate(json.loads(stored))
        except ValueError:
            return None
        if artifact.executor_revision != spec.executor_revision or artifact.entrypoint != "checker:create_audit":
            return None
        return artifact

    def _read_latest_start(self, spec: ExecuteRequest, prefix: str, legacy_key: str) -> CheckerArtifact | None:
        """Return the newest readable start program. A missing generation ends the scan."""
        from agent.checker_program import MAX_START_GENERATIONS, start_generation_key

        latest = self._read_start_artifact(spec, legacy_key)
        for generation in range(1, MAX_START_GENERATIONS + 1):
            key = start_generation_key(prefix, generation)
            try:
                stored = self.objects.get(key, max_bytes=MAX_BUNDLE_BYTES)
            except ObjectMissing:
                break
            except ObjectTooLarge:
                continue
            try:
                artifact = CheckerArtifact.model_validate(json.loads(stored))
            except ValueError:
                continue
            if artifact.executor_revision != spec.executor_revision or artifact.entrypoint != "checker:create_audit":
                continue
            latest = artifact
        return latest

    def _append_start(self, prefix: str, payload: object) -> None:
        """Save the next start program. put_exclusive cannot replace an existing object."""
        from agent.checker_program import MAX_START_GENERATIONS, start_generation_key

        body = encoded(payload)
        for generation in range(1, MAX_START_GENERATIONS + 1):
            key = start_generation_key(prefix, generation)
            try:
                self.objects.get(key, max_bytes=MAX_BUNDLE_BYTES)
            except ObjectMissing:
                try:
                    self.objects.put_exclusive(key, body, "application/json")
                except ObjectExists:
                    continue
                return
            except ObjectTooLarge:
                continue

    def _load_checker_start(self, spec: ExecuteRequest, rule: RuleInput) -> CheckerArtifact | None:
        """A previous program may start a new rule or file. It is not executed as that input's program."""
        from agent.checker_program import checker_start_key, checker_start_prefix, checker_type_start_key, checker_type_start_prefix

        exact = self._read_latest_start(spec, checker_start_prefix(spec.tenant_id, spec.executor_revision, rule), checker_start_key(spec.tenant_id, spec.executor_revision, rule))
        if exact is not None:
            return exact
        return self._read_latest_start(
            spec,
            checker_type_start_prefix(spec.tenant_id, spec.executor_revision, rule),
            checker_type_start_key(spec.tenant_id, spec.executor_revision, rule),
        )

    def _adapt_round(self, spec: ExecuteRequest, files: dict[str, bytes], context: ExecutionContext) -> ExecuteResponse:
        from agent.checker_program import group_context_for_spec, verified_related_bytes
        from agent.mapping import checker_response

        self._reject_unconfirmed_prior_attempt(spec)
        prior = self._read_checker_recovery(spec)
        if prior is not None and (prior.uncertain or prior.job_id != spec.job_id or prior.attempt != spec.attempt):
            raise DeepError("deep_model_protocol")
        targets = [item for item in spec.files if item.role == "target"]
        if not targets:
            raise DeepError("deep_input_invalid")
        group_context = group_context_for_spec(spec)
        related_bytes = verified_related_bytes(spec, files)
        executed: list[tuple] = []
        published = False
        usage = dict.fromkeys(("input_tokens", "output_tokens", "tool_calls", "completed_tool_calls", "compactions", "model_requests"), 0)

        def add_usage(bundle) -> None:
            for name in usage:
                usage[name] += int(getattr(bundle, name, 0) or 0)

        for item in targets:
            raw = files.get(item.file_version_id)
            body = item.rule_body or {}
            clauses = body.get("rules")
            if raw is None or hashlib.sha256(raw).hexdigest() != item.sha256:
                raise DeepError("deep_integrity_mismatch")
            if not isinstance(clauses, list) or not clauses:
                raise DeepError("deep_rules_empty")
            raw_appendix = body.get("appendix")
            from agent.checker_program import scoped_rule

            rule = scoped_rule(
                RuleInput(
                    rule_id=item.rule_version_id or item.file_version_id,
                    rule_profile=str(body.get("rule_profile") or "v3"),
                    type=body.get("type"),
                    rules=list(clauses),
                    appendix=[entry for entry in raw_appendix if isinstance(entry, dict)] if isinstance(raw_appendix, list) else [],
                ),
                file_version_id=item.file_version_id,
            )
            audit_file = AuditInput(
                audit_id=item.file_version_id,
                file_url=item.filename,
                file_metadata={"file_version_id": item.file_version_id, "sha256": item.sha256, "role": item.role},
                project_id=spec.tenant_id or "default",
            )
            fixed: CheckerArtifact | None = None
            try:
                fixed = self._load_fixed_artifact(spec, audit_file, rule, raw, role=item.role, group_context=group_context)
                prior_program = None
                if fixed is None:
                    from agent.checker_program import checker_program_key, input_digest

                    current = input_digest(
                        audit_file,
                        rule,
                        file_sha256=hashlib.sha256(raw).hexdigest(),
                        role=item.role,
                        group_context=group_context,
                    )
                    remembered = self._read_start_artifact(spec, checker_program_key(spec.tenant_id, spec.executor_revision, current))
                    if remembered is not None and remembered.input_digest != current:
                        raise DeepError("deep_output_identity_mismatch")
                    prior_program = None if remembered is not None else self._load_checker_start(spec, rule)
                    candidate = remembered or prior_program
                    if candidate is not None and candidate.input_digest == current:
                        fixed = candidate
                        self._put(f"{self._attempt_prefix(spec)}/checkers/{current}.json", fixed.model_dump(mode="json"))
                        prior_program = None
                if fixed is None:
                    adapted = self._checker_job(
                        spec,
                        audit_file,
                        rule,
                        raw,
                        role=item.role,
                        context=context,
                        group_context=group_context,
                        related_bytes=related_bytes,
                        prior=prior_program,
                        operation="adapt-checker",
                    )
                    if adapted.artifact is None:
                        raise DeepError("deep_output_invalid")
                    fixed = adapted.artifact
                    add_usage(adapted)
                executed_bundle = self._checker_job(
                    spec,
                    audit_file,
                    rule,
                    raw,
                    role=item.role,
                    context=context,
                    group_context=group_context,
                    related_bytes=related_bytes,
                    operation="execute-checker",
                    artifact=fixed,
                )
                add_usage(executed_bundle)
                results = [AuditResultItem.model_validate(item) for item in executed_bundle.results]
                published = True
            except DeepError as exc:
                self._remember_checker_failure(spec, exc.code)
                if exc.code != "deep_output_invalid":
                    raise
                # A fixed program that fails execution is an explicit non-pass for that target.
                # No fixed program is not a result. Publish it only beside another target that did run.
                if fixed is not None:
                    published = True
                results = []
            executed.append((item, results))
        if not published:
            raise DeepError("deep_output_invalid")
        source = getattr(self.runtime, "usage_source", "unknown")
        usage_source: Literal["unknown", "provider", "controlled"] = "controlled" if source == "controlled" else "provider" if source == "provider" else "unknown"
        response = checker_response(spec, executed, usage=usage, usage_source=usage_source)
        self._remember_checker_usage(spec, response)
        return response

    def _stored_checker_response(self, spec: ExecuteRequest, files: dict[str, bytes], context: ExecutionContext, *, partial: bool = False) -> ExecuteResponse | None:
        from agent.checker_program import group_context_for_spec, input_digest, verified_related_bytes
        from agent.mapping import checker_response

        if self.runtime.engine_revision() != spec.executor_revision:
            return None
        targets = [item for item in spec.files if item.role == "target"]
        if not targets:
            return None
        group_context = group_context_for_spec(spec)
        prepared: list[tuple] = []
        found = False
        for item in targets:
            raw = files.get(item.file_version_id)
            body = item.rule_body or {}
            clauses = body.get("rules")
            if raw is None or hashlib.sha256(raw).hexdigest() != item.sha256:
                if partial:
                    raise DeepError("deep_integrity_mismatch")
                return None
            if not isinstance(clauses, list) or not clauses:
                if partial:
                    raise DeepError("deep_rules_empty")
                return None
            raw_appendix = body.get("appendix")
            from agent.checker_program import scoped_rule

            rule = scoped_rule(
                RuleInput(
                    rule_id=item.rule_version_id or item.file_version_id,
                    rule_profile=str(body.get("rule_profile") or "v3"),
                    type=body.get("type"),
                    rules=list(clauses),
                    appendix=[entry for entry in raw_appendix if isinstance(entry, dict)] if isinstance(raw_appendix, list) else [],
                ),
                file_version_id=item.file_version_id,
            )
            audit_file = AuditInput(
                audit_id=item.file_version_id,
                file_url=item.filename,
                file_metadata={"file_version_id": item.file_version_id, "sha256": item.sha256, "role": item.role},
                project_id=spec.tenant_id or "default",
            )
            digest = input_digest(audit_file, rule, file_sha256=item.sha256, role=item.role, group_context=group_context)
            try:
                stored = self.objects.get(f"{self._attempt_prefix(spec)}/checkers/{digest}.json", max_bytes=MAX_BUNDLE_BYTES)
            except (ObjectMissing, ObjectTooLarge):
                if not partial:
                    return None
                prepared.append((item, None, audit_file, rule, raw))
                continue
            artifact = CheckerArtifact.model_validate(json.loads(stored))
            if artifact.input_digest != digest or artifact.executor_revision != spec.executor_revision:
                raise DeepError("deep_output_identity_mismatch")
            found = True
            prepared.append((item, artifact, audit_file, rule, raw))
        if not found:
            return None
        related_bytes = verified_related_bytes(spec, files)
        executed: list[tuple] = []
        for item, artifact, audit_file, rule, raw in prepared:
            if artifact is None:
                executed.append((item, []))
                continue
            try:
                results = self.execute_checker(
                    spec,
                    artifact,
                    audit_file,
                    rule,
                    raw,
                    role=item.role,
                    context=context,
                    group_context=group_context,
                    related_bytes=related_bytes,
                )
            except DeepError as exc:
                if exc.code not in {"deep_output_invalid", "deep_timeout", "deep_execution_failed", "deep_budget_exhausted"}:
                    raise
                results = []
            executed.append((item, results))
        return checker_response(spec, executed)

    def adapt_checker(
        self,
        spec: ExecuteRequest,
        audit_file: AuditInput,
        rule: RuleInput,
        file_bytes: bytes,
        *,
        role: str,
        context: ExecutionContext,
        group_context: GroupContextInput | None = None,
        prior: CheckerArtifact | None = None,
        related_bytes: dict[str, bytes] | None = None,
    ) -> CheckerArtifact:
        bundle = self._checker_job(
            spec,
            audit_file,
            rule,
            file_bytes,
            role=role,
            context=context,
            group_context=group_context,
            prior=prior,
            operation="adapt-checker",
            related_bytes=related_bytes,
        )
        if bundle.artifact is None:
            raise DeepError("deep_output_invalid")
        return bundle.artifact

    def execute_checker(
        self,
        spec: ExecuteRequest,
        artifact: CheckerArtifact,
        audit_file: AuditInput,
        rule: RuleInput,
        file_bytes: bytes,
        *,
        role: str,
        context: ExecutionContext,
        group_context: GroupContextInput | None = None,
        related_bytes: dict[str, bytes] | None = None,
    ) -> list[AuditResultItem]:
        bundle = self._checker_job(
            spec,
            audit_file,
            rule,
            file_bytes,
            role=role,
            context=context,
            group_context=group_context,
            operation="execute-checker",
            artifact=artifact,
            related_bytes=related_bytes,
        )
        return [AuditResultItem.model_validate(item) for item in bundle.results]

    def _checker_job(
        self,
        spec: ExecuteRequest,
        audit_file: AuditInput,
        rule: RuleInput,
        file_bytes: bytes,
        *,
        role: str,
        context: ExecutionContext,
        group_context: GroupContextInput | None,
        operation: Literal["adapt-checker", "execute-checker"],
        prior: CheckerArtifact | None = None,
        artifact: CheckerArtifact | None = None,
        related_bytes: dict[str, bytes] | None = None,
    ) -> CheckerProgramBundle:
        from agent.checker_program import CheckerManifest, input_digest, scoped_rule, write_manifest
        from agent.execution.checker_guest import CheckerProgramError
        from agent.execution.guest_model import ModelProtocolError

        revision = self.runtime.engine_revision()
        if spec.executor_revision != revision:
            raise DeepError("executor_revision_mismatch")
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        version_id = str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id)
        rule = scoped_rule(rule, file_version_id=version_id)
        digest = input_digest(audit_file, rule, file_sha256=file_sha256, role=role, group_context=group_context)
        if artifact is not None and (artifact.input_digest != digest or artifact.executor_revision != revision):
            raise DeepError("deep_output_identity_mismatch")
        job = CheckerJobSpec(execution_id=UUID(int=0), operation=operation, input_digest=digest, executor_revision=revision)
        job = job.model_copy(update={"execution_id": job.expected_execution_id()})
        version_id = str((audit_file.file_metadata or {}).get("file_version_id") or "")
        from agent.checker_program import staged_document_name

        original_name = str((audit_file.file_metadata or {}).get("original_filename") or audit_file.file_url or "")
        manifest = CheckerManifest(
            operation=operation,
            spec=job,
            document=staged_document_name(original_name),
            audit_id=audit_file.audit_id,
            rule=rule.model_dump(mode="json"),
            file_sha256=file_sha256,
            file_version_id=version_id,
            role=role,
            group_context=group_context.model_dump(mode="json") if group_context is not None else None,
            prior_files={name: content.decode("utf-8") for name, content in prior.files.items()} if prior is not None else None,
            artifact=artifact,
        )
        artifact_prefix = self._attempt_prefix(spec)
        deadline = time.monotonic() + self.timeout
        with tempfile.TemporaryDirectory(prefix=f"{operation}-", dir=None) as root_name:
            root = Path(root_name)
            inputs, work = root / "inputs", root / "work"
            work.mkdir()
            try:
                write_manifest(inputs, manifest, file_bytes, related_bytes=related_bytes)
            except CheckerProgramError as exc:
                raise DeepError("deep_integrity_mismatch") from exc
            context.raise_if_cancelled()
            try:
                self.runtime.run(job, inputs, work, deadline, context.cancelled, on_progress=None)
            except DeepError:
                raise
            except ControllerFault as exc:
                raise DeepError(FAULTS.get(exc.code, "deep_execution_failed")) from exc
            except TimeoutError as exc:
                raise DeepError("deep_timeout") from exc
            except ModelProtocolError as exc:
                raise DeepError("deep_model_protocol") from exc
            except CheckerProgramError as exc:
                raise DeepError("deep_output_invalid") from exc
            try:
                bundle = CheckerProgramBundle.model_validate(read_json(work, ("result", "bundle.json"), MAX_BUNDLE_BYTES))
            except (OSError, ValueError) as exc:
                raise DeepError("deep_output_invalid") from exc
        if bundle.input_digest != digest or bundle.executor_revision != revision or bundle.operation != operation:
            raise DeepError("deep_output_identity_mismatch")
        if operation == "adapt-checker":
            if bundle.artifact is None:
                raise DeepError("deep_output_invalid")
            from agent.checker_program import checker_program_key, checker_start_prefix, checker_type_start_prefix

            payload = bundle.artifact.model_dump(mode="json")
            self._put(f"{artifact_prefix}/checkers/{digest}.json", payload)
            self._put(checker_program_key(spec.tenant_id, spec.executor_revision, digest), payload)
            self._append_start(checker_start_prefix(spec.tenant_id, spec.executor_revision, rule), payload)
            self._append_start(checker_type_start_prefix(spec.tenant_id, spec.executor_revision, rule), payload)
            if bundle.observations:
                from agent.checker_program import store_observation

                store_observation(self.objects, f"{artifact_prefix}/checkers/{digest}.trials.json", bundle.observations)
        return bundle
