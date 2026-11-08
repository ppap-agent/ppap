"""Controller-owned authorization and materialization for a leased precheck."""

from __future__ import annotations

import hashlib
import threading
from uuid import UUID

from agent.client import DeepError
from agent.execution.budget import MAX_INPUT_FILES, MAX_INPUT_TOTAL
from audit.checker_core import checker_digest
from audit.precheck_contract import customer_conflict, input_digest, validate_result
from audit.standard_suites import library_revision
from product.store.prechecks import precheck_snapshot, preparation_runtime_revision
from product.store.suites import SuiteStore


def precheck_handler(store, runtime):
    def load(identity):
        try:
            ids = tuple(str(UUID(value)) for value in (identity.job_id, identity.tenant_id))
        except ValueError:
            raise DeepError("deep_input_unauthorized") from None
        with store._conn() as conn:
            row = conn.execute(
                """SELECT j.payload FROM engine.jobs j JOIN product.audit_drafts d
                ON d.id::text=j.payload->>'task_id' AND d.tenant_id=j.tenant_id
                WHERE j.id=%s AND j.tenant_id=%s AND j.kind='task_precheck'
                  AND j.status='leased' AND j.attempt=%s AND j.lease_owner=%s AND j.lease_until>now()""",
                (*ids, identity.attempt, identity.lease_owner),
            ).fetchone()
        if not row or row[0].get("tenant_id") != ids[1]:
            raise DeepError("deep_input_unauthorized")
        return row[0]

    def handle(identity, context):
        payload = load(identity)
        if payload.get("library_revision") != library_revision() or payload.get("runtime_revision") != preparation_runtime_revision(store.settings):
            raise DeepError("executor_revision_mismatch")
        if payload.get("execution_policy") != store.settings.standard_execution_policy().model_dump(mode="json"):
            raise DeepError("executor_revision_mismatch")
        if customer_conflict(payload):
            from worker.prechecks import unavailable_result

            return unavailable_result(payload)
        snapshot = precheck_snapshot(payload)
        if snapshot is None:
            from worker.prechecks import unavailable_result

            return unavailable_result(payload)
        finished = threading.Event()

        def monitor():
            while not finished.wait(0.2):
                try:
                    if input_digest(load(identity)) == input_digest(payload):
                        continue
                except Exception:
                    pass
                context.cancelled.set()
                return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            context.raise_if_cancelled()
            if len(snapshot.inputs) > MAX_INPUT_FILES:
                raise DeepError("deep_budget_exhausted")
            sources, programs = {}, {}
            remaining = MAX_INPUT_TOTAL
            for source in snapshot.inputs:
                context.raise_if_cancelled()
                version = store.get_version(source.file_id, source.file_version_id)
                if version is None or version.tenant_id != payload["tenant_id"] or version.status != "ready" or version.sha256 != source.sha256:
                    raise DeepError("deep_input_unauthorized")
                raw = store.objects.get(version.object_key, max_bytes=remaining)
                if len(raw) > remaining or hashlib.sha256(raw).hexdigest() != source.sha256:
                    raise DeepError("deep_integrity_mismatch")
                remaining -= len(raw)
                sources[source.file_version_id] = raw
            suites = SuiteStore(store.settings, store.objects, role="worker")
            for target in snapshot.targets:
                suite = suites.resolve_suite(target.suite_revision_id, snapshot.tenant_id)
                code = {name: body for name, body in suite.files.items() if name in {"checker.py", "resources.py"}}
                if suite.revision.resolved_digest != target.suite_digest or checker_digest(code) != target.binding.checker_digest:
                    raise DeepError("deep_integrity_mismatch")
                programs[target.binding.checker_digest] = code
            if input_digest(load(identity)) != input_digest(payload):
                raise DeepError("deep_input_unauthorized")
            result = runtime.execute_precheck(payload, snapshot, sources, programs, context)
            context.raise_if_cancelled()
            if input_digest(load(identity)) != input_digest(payload):
                raise DeepError("deep_input_unauthorized")
            return validate_result(payload, result)
        except DeepError:
            raise
        except Exception:
            raise DeepError("precheck_execution_failed") from None
        finally:
            finished.set()
            watcher.join(timeout=1)

    return handle
