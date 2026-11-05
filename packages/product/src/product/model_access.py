"""Personal model access composition. Secrets never enter frozen product inputs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from documents.model_access import model_access_scope


@contextmanager
def personal_model_scope(store, *, tenant_id: str, initiating_user_id: str) -> Iterator[None]:
    # Resolve again on each call so revoke/replace/membership removal is effective.
    with model_access_scope(lambda: store.personal_model_access(tenant_id, initiating_user_id)):
        yield
