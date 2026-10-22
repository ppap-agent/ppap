"""Controller file source: frozen file_version_id → object bytes. Not a Worker RPC field."""

from __future__ import annotations

from uuid import UUID

from ppap_core.db import connect
from ppap_core.files import ObjectMissing, ObjectTooLarge
from ppap_core.settings import Settings
from ppap_core.storage import S3Objects

from ppap_deep.client import DeepError
from ppap_deep.controller import FileSource
from ppap_deep.protocol import ExecuteRequest


def authorize_execution(settings: Settings):
    """The caller may name a leased attempt, not select arbitrary objects or rules."""
    from ppap_product_store.store import AuthStore

    from ppap_deep.mapping import spec_from_work

    store = AuthStore(settings, role="worker")

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
                     AND j.payload->>'round_id' = r.id::text AND r.analysis_mode = 'deep'""",
                (*identities, spec.attempt),
            ).fetchone()
        if not row or row[0].get("executor") != spec.executor or row[0].get("executor_revision") != spec.executor_revision:
            raise DeepError("deep_input_unauthorized")
        work = store.round_work(spec.round_id)
        if work is None or work.tenant_id != spec.tenant_id:
            raise DeepError("deep_input_unauthorized")
        expected = spec_from_work(work, spec.executor, spec.executor_revision, spec.protocol_revision)
        if expected.files != spec.files:
            raise DeepError("deep_input_unauthorized")

    return authorize


def object_file_source(settings: Settings) -> FileSource:
    objects = S3Objects(settings)

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
