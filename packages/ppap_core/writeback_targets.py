"""Provider-neutral writeback/origin records. Never expose object_ref in public JSON."""
from __future__ import annotations


def normalize_external_ref(value) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid_writeback_target")
    provider = str(value.get("provider") or "").strip()
    kind = str(value.get("object_kind") or "").strip()
    ref = str(value.get("object_ref") or "").strip()
    if provider != "feishu" or not kind or not ref:
        raise ValueError("invalid_writeback_target")
    return {"provider": provider, "object_kind": kind, "object_ref": ref}


def public_target(row: dict | None) -> dict[str, object]:
    if not row:
        return {"available": False}
    return {
        "available": True,
        "provider": row.get("provider"),
        "object_kind": row.get("object_kind"),
    }
