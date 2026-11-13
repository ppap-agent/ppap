"""Recognize legacy carrier programs. A carrier head never takes new admission or publish.

Normal seed no longer installs historical carriers. A head that already points at
one stays in place until a caller names a verified directory revision.
"""

from __future__ import annotations

from typing import Any

_DIRECTORY_IMPORT = b"from audit.standard_library import"
_OFFICIAL_TENANT = "00000000-0000-0000-0000-000000000000"


def refuse_related_carrier(store, conn, tenant_id: str | None, related_keys) -> None:
    """A related current head that is a carrier blocks publish. Do not select another revision."""
    from product.store.suite_releases import _selected_or_none

    lookup = tenant_id or _OFFICIAL_TENANT
    for key in related_keys:
        selected = _selected_or_none(conn, tenant_id, key)
        if selected is None:
            continue
        suite = store.suites.resolve_suite(str(selected["row"]["id"]), lookup, conn=conn)
        if legacy_carrier_program(suite.files):
            raise ValueError("suite_version_confirmation_required")


def legacy_carrier_program(files: Any) -> bool:
    """True when the frozen program dispatches through the historical class loader."""
    source = files.get("checker.py", b"") if hasattr(files, "get") else b""
    return isinstance(source, bytes) and _DIRECTORY_IMPORT in source
