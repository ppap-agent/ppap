"""Personal model access composition. Secrets never enter frozen product inputs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from documents.model_access import ModelUnavailable, model_access_scope


@contextmanager
def personal_model_scope(store, *, tenant_id: str, initiating_user_id: str, expected_identity: dict[str, str] | None = None) -> Iterator[None]:
    # Resolve again on each call so revoke/replace/membership removal is effective.
    frozen = dict(expected_identity) if expected_identity is not None else None

    def resolve():
        access = store.personal_model_access(tenant_id, initiating_user_id)
        if frozen is not None and access.identity() != frozen:
            raise ModelUnavailable()
        return access

    with model_access_scope(resolve):
        yield
