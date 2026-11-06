"""Composition roots for application capabilities and persistence."""

from __future__ import annotations

from .store import AuthStore


def build_product_store(settings, *, objects=None, role: str = "api") -> AuthStore:
    """Compose persistence; adopted Suite snapshots own execution bindings."""
    return AuthStore(
        settings,
        objects=objects,
        role=role,
    )
