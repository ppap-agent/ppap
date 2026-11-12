"""Controller file source: frozen file_version_id → object bytes. Not a Worker RPC field."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from audit.storage_errors import ObjectMissing, ObjectTooLarge

from agent.client import DeepError
from agent.protocol import ExecuteRequest
from agent.run import FileSource


def authorize_execution(settings: Any, *, connect: Callable, open_store: Callable):
    """The caller may name a leased attempt, not select arbitrary objects or rules."""
    from agent.mapping import spec_from_work

    store = open_store(settings)

    def authorize(spec: ExecuteRequest) -> None:
        try:
            identities = tuple(str(UUID(value)) for value in (spec.round_id, spec.job_id, spec.tenant_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DeepError("deep_input_unauthorized") from exc
        with connect(settings, "worker") as conn:
            row = conn.execute(
                """SELECT j.payload FROM engine.jobs j
                   JOIN product.audit_rounds r ON r.id = j.audit_round_id AND r.id = %s AND r.tenant_id = j.tenant_id
                   WHERE j.id = %s AND j.tenant_id = %s AND j.attempt = %s
                     AND j.kind = 'audit' AND j.status = 'leased' AND j.lease_until > now()
                     AND j.payload->>'round_id' = r.id::text""",
                (*identities, spec.attempt),
            ).fetchone()
        if (
            not row
            or row[0].get("executor") != spec.executor
            or row[0].get("executor_revision") != spec.executor_revision
            or row[0].get("checker_snapshot_digest", "") != spec.checker_snapshot_digest
            or row[0].get("model_identity", {}) != spec.model_identity
        ):
            raise DeepError("deep_input_unauthorized")
        work = store.round_work(spec.round_id)
        if work is None or work.tenant_id != spec.tenant_id:
            raise DeepError("deep_input_unauthorized")
        expected = spec_from_work(work, spec.executor, spec.executor_revision, spec.protocol_revision, checker_snapshot_digest=spec.checker_snapshot_digest)
        if expected.files != spec.files or expected.initiating_user_id != spec.initiating_user_id:
            raise DeepError("deep_input_unauthorized")

    return authorize


def object_file_source(settings: Any, *, connect: Callable, objects: Any) -> FileSource:
    def load(file_version_id: str, max_bytes: int) -> bytes:
        try:
            ident = str(UUID(str(file_version_id)))
        except (ValueError, TypeError) as exc:
            raise DeepError("deep_input_missing") from exc
        with connect(settings, "worker") as conn:
            row = conn.execute(
                "SELECT object_key, status FROM product.file_versions WHERE id = %s",
                (ident,),
            ).fetchone()
        if not row or row[1] != "ready" or not row[0]:
            raise DeepError("deep_input_missing")
        try:
            return objects.get(row[0], max_bytes=max_bytes)
        except ObjectTooLarge as exc:
            raise DeepError("deep_budget_exhausted") from exc
        except ObjectMissing as exc:
            raise DeepError("deep_input_missing") from exc

    return load
