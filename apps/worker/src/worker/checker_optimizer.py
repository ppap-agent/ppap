"""Durable checker development through the same isolated runtime as Deep Audit.

The worker sends identities, not user code, expected labels, credentials or paths.
The trusted controller loads frozen inputs and stores the result before replying.
"""

from __future__ import annotations

import hashlib
import json
import threading

from agent.checker_development import DevelopmentOutcome, DevelopmentRequest, FrozenDevelopmentInput, OracleSample, effective_rule_digest
from agent.checker_program import group_context_for_spec, rule_for_file, verified_related_bytes
from agent.checker_spec import SuiteCheckerProgram
from agent.client import DEEP_REQUEST_TIMEOUT, DeepClient, DeepError, ModelNotStarted
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from agent.protocol import DevelopmentEnvelope, ExecuteRequest, FrozenFile
from audit.audits import DEEP_PROTOCOL_REVISION
from audit.checker_core import RuleBinding, checker_digest, runtime_revision
from audit.standard_suites import library_revision
from documents.model_access import ModelUnavailable
from product.logs import emit
from product.store.checker_development import CheckerDevelopmentStore, _receipt, principal_for_operation


def materialize_request(development, row):
    """Holdout bytes live only in this host process until fixed evaluation starts."""
    store, frozen = development.store, row["frozen"]
    if frozen["executor_revision"] != runtime_revision(store.settings.deep_execution_revision(frozen["model_identity"]), library_revision()):
        raise DeepError("executor_revision_mismatch")
    suite = development.suites.resolve_suite(str(row["suite_revision_id"]), str(row["tenant_id"]))
    if suite.revision.resolved_digest != frozen["suite_digest"]:
        raise DeepError("deep_integrity_mismatch")
    for key, ident in frozen["related_revision_ids"].items():
        related = development.suites.resolve_suite(ident, str(row["tenant_id"]))
        if related.revision.resolved_digest != frozen["related_suite_digests"][key]:
            raise DeepError("deep_integrity_mismatch")
    samples, specs = [], {}
    # One bounded request may repeat references. Count distinct original bytes.
    blobs = {}
    remaining = MAX_INPUT_TOTAL
    from product.checker_samples import feedback_cases

    groups = feedback_cases(frozen["samples"])
    for members in groups:
        sample = members[0]
        target_body = next(item["rule_body"] for item in sample["files"] if item["role"] == "target")
        clause_ids = {str(clause["id"]) for clause in target_body["rules"]}
        applicable = [item for item in members if item["rule_clause_id"] in clause_ids]
        if not applicable:
            continue  # Deleted clauses are not current results or regression expectations.
        files = [FrozenFile.model_validate(item) for item in sample["files"]]
        if len(files) > MAX_INPUT_FILES:
            raise DeepError("deep_budget_exhausted")
        spec = ExecuteRequest(
            protocol_revision=DEEP_PROTOCOL_REVISION,
            round_id=str(row["operation_id"]),
            files=files,
            executor="ppap-deep",
            executor_revision=frozen["executor_revision"],
            model_identity=frozen["model_identity"],
            tenant_id=str(row["tenant_id"]),
            job_id=str(row["job_id"]),
            initiating_user_id=str(row["initiating_user_id"]),
        )
        sample_blobs = {}
        for item in files:
            if item.file_version_id not in blobs:
                version = store.get_version(item.file_id, item.file_version_id)
                if version is None or version.tenant_id != str(row["tenant_id"]) or version.status != "ready" or version.sha256 != item.sha256:
                    raise DeepError("deep_input_unauthorized")
                raw = store.objects.get(version.object_key, max_bytes=remaining)
                if len(raw) > remaining or hashlib.sha256(raw).hexdigest() != item.sha256:
                    raise DeepError("deep_integrity_mismatch")
                remaining -= len(raw)
                blobs[item.file_version_id] = raw
            raw = blobs[item.file_version_id]
            if hashlib.sha256(raw).hexdigest() != item.sha256:
                raise DeepError("deep_integrity_mismatch")
            sample_blobs[item.file_version_id] = raw
        context = group_context_for_spec(spec)
        if context is None:
            raise DeepError("deep_input_invalid")
        target = next(item for item in files if item.role == "target")
        inputs = FrozenDevelopmentInput(
            context.by_audit_id[target.file_version_id],
            context.rules_by_id[target.file_version_id],
            sample_blobs[target.file_version_id],
            "target",
            context,
            verified_related_bytes(spec, sample_blobs),
        )
        expected_digests = {effective_rule_digest(rule_for_file(target.model_copy(update={"rule_body": item["expected_rule_body"]}))) for item in applicable}
        expected_digest = next(iter(expected_digests)) if len(expected_digests) == 1 else "mixed-old-rule-bases"
        selected_clauses = frozenset(item["rule_clause_id"] for item in applicable if item["id"] in frozen["selected_feedback_ids"])
        samples.append(
            OracleSample(
                sample["id"],
                sample.get("case_ordinal", sample["ordinal"]),
                inputs,
                {item["rule_clause_id"]: item["expected_status"] for item in applicable},
                sample["confirmed_by"],
                expected_digest,
                repair_selected=bool(selected_clauses),
                repair_clause_ids=selected_clauses,
                feedback_ids=tuple(item["id"] for item in members),
            )
        )
        specs[sample["id"]] = spec
    target_case_id = next(group[0]["id"] for group in groups if any(item["id"] == frozen["target_sample_id"] for item in group))
    target_sample = next(item for item in samples if item.case_id == target_case_id)
    _require_selected_source(suite)
    current_training_cases = {sample.case_id for sample in samples if not sample.holdout and sample.expectations_current}
    current_training = {item["id"] for group in groups if group[0]["id"] in current_training_cases for item in group}
    feedback = "\n".join(sample["rationale"] for sample in frozen["samples"] if sample["id"] in current_training and sample["id"] in frozen["selected_feedback_ids"])
    program_files = {path: data for path, data in suite.files.items() if path in {"checker.py", "resources.py"}}
    prior = SuiteCheckerProgram(
        input_digest=target_sample.inputs.digest,
        executor_revision=frozen["executor_revision"],
        binding=RuleBinding(suite.revision.suite_key, checker_digest(program_files), json.loads(suite.files["rules.json"])["checker_binding"]),
        files=program_files,
    )
    request = DevelopmentRequest(
        target=target_sample.inputs,
        executor_revision=frozen["executor_revision"],
        initiating_user_id=str(row["initiating_user_id"]),
        suite_revision_id=str(row["suite_revision_id"]),
        suite_digest=frozen["suite_digest"],
        prior=prior,
        fixed=None if frozen["consume_model"] else prior,
        allow_model_calls=frozen["consume_model"],
        feedback=feedback[:8000] or None,
        rule_changes=tuple(frozen.get("rule_changes", [])),
        oracle_revision=frozen["oracle_revision"],
        oracle=tuple(samples),
        related_revision_ids=tuple(frozen["related_revision_ids"].values()),
        library_revision=frozen["library_revision"],
        related_suite_digests=frozen["related_suite_digests"],
        source_files=dict(suite.files),
    )
    # Reject any overlap before the paid dispatch boundary, not inside the guest.
    from agent.checker_development import _validate

    _validate(request)
    return request, specs[target_sample.case_id]


def _require_selected_source(suite) -> None:
    """The selected revision is the only source. Missing or unbound bytes stop before dispatch."""
    from product.suites import REQUIRED_PATHS, SUITE_PATHS

    files = suite.files
    if not REQUIRED_PATHS <= files.keys() or not set(files) <= SUITE_PATHS:
        raise DeepError("checker_input_invalid")
    try:
        rules = json.loads(files["rules.json"])
        decoded = [body.decode("utf-8") for body in files.values()]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise DeepError("checker_input_invalid") from None
    if not decoded or not isinstance(rules, dict) or not isinstance(rules.get("rules"), list):
        raise DeepError("checker_input_invalid")
    binding = rules.get("checker_binding")
    if not isinstance(binding, dict) or not binding:
        raise DeepError("checker_input_invalid")


def development_handler(store, runtime):
    """Compose once in the controller; no persistent second agent or retry loop."""
    development = CheckerDevelopmentStore(store)

    def handle(identity: DevelopmentEnvelope, context):
        row = development.operation_for_dispatch(identity)
        if row["state"] != "queued":
            return _receipt(row)
        dispatched = False
        finished = threading.Event()
        monitor = None
        try:
            context.raise_if_cancelled()
            selection = store.model_selection(str(row["tenant_id"]))
            if selection.identity != row["frozen"]["model_identity"]:
                raise ModelUnavailable("model_configuration_changed")
            if row["frozen"]["consume_model"]:
                selection.access("coding")
            request, spec = materialize_request(development, row)
            spec = spec.model_copy(update={"attempt": identity.attempt})
            context.raise_if_cancelled()
            if development.dispatch(identity) is None:
                return development.read(principal_for_operation(row), identity.operation_id)
            dispatched = True

            def watch_fence():
                while not finished.wait(0.5):
                    try:
                        if development.active(identity):
                            continue
                    except Exception:
                        pass
                    context.cancelled.set()
                    return

            monitor = threading.Thread(target=watch_fence, daemon=True)
            monitor.start()
            outcome = runtime.develop_and_run(request, spec=spec, context=context)
        except ModelNotStarted as exc:
            # Only the trusted pre-start runtime rejection proves no send.
            # A generic unavailable error during execution can follow paid work.
            outcome = DevelopmentOutcome(None, [], [], {"model_requests": 0, "input_tokens": 0, "output_tokens": 0}, error_code=exc.code, usage_source="controlled")
        except (DeepError, ModelUnavailable, KeyError, ValueError) as exc:
            code = exc.code if isinstance(exc, DeepError) else "model_unavailable" if isinstance(exc, ModelUnavailable) else "checker_input_invalid"
            outcome = DevelopmentOutcome(None, [], [], {}, error_code=code, usage_uncertain=dispatched and row["frozen"]["consume_model"])
        except Exception:
            # Failure after entering the model boundary cannot grant a retry.
            outcome = DevelopmentOutcome(None, [], [], {}, error_code="checker_execution_failed", usage_uncertain=dispatched and row["frozen"]["consume_model"])
        finally:
            finished.set()
            if monitor:
                monitor.join(timeout=1)
        development.finish(identity, outcome)
        return development.read(principal_for_operation(row), identity.operation_id)

    return handle


def process_checker_optimization(store, claimed, ident):
    from worker import audit_lease

    request = DevelopmentEnvelope(op="develop", operation_id=claimed.payload["operation_id"], job_id=claimed.id, tenant_id=claimed.tenant_id, attempt=claimed.attempt, lease_owner=ident)
    development = CheckerDevelopmentStore(store)
    try:
        with audit_lease(store, claimed, ident) as cancelled:
            receipt = DeepClient(store.settings.deep_controller_socket, timeout=DEEP_REQUEST_TIMEOUT).develop(request, cancelled=cancelled)
        return receipt
    except DeepError as exc:
        # A transport error is not evidence that the controller never consumed
        # the provider. Read back; do not mark failed or enqueue another attempt.
        try:
            row = development.operation_for_dispatch(request)
            receipt = development.read(principal_for_operation(row), request.operation_id)
        except (KeyError, ValueError):
            receipt = {"operation_id": request.operation_id, "state": "unknown", "failure": "execution_outcome_unknown"}
        emit("checker_development_transport_lost", job_id=claimed.id, attempt=claimed.attempt, error_type=exc.code)
        return receipt
