"""The model scope of one tenant's operation. Secrets never enter frozen product inputs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from documents.model_access import ModelPurpose, ModelUnavailable, model_access_scope

# Operations accepted before tenant BYOK froze a personal identity without a source.
RETIRED_IDENTITY = "model_contract_retired"


@contextmanager
def tenant_model_scope(store, *, tenant_id: str, expected_identity: dict[str, str] | None = None) -> Iterator[None]:
    """Use the tenant's key, else the platform models, and only as frozen at acceptance.

    Resolve again on each call so a key rotation or removal takes effect. A changed
    selection fails the call instead of running a frozen operation on another model.
    """
    frozen = dict(expected_identity) if expected_identity is not None else None

    def resolve(purpose: ModelPurpose):
        if frozen is not None and "source" not in frozen:
            raise ModelUnavailable(RETIRED_IDENTITY)
        selection = store.model_selection(tenant_id)
        if frozen is not None and selection.identity != frozen:
            raise ModelUnavailable("model_configuration_changed")
        return selection.access(purpose)

    with model_access_scope(resolve):
        yield
