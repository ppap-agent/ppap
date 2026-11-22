"""Object authorization: everyone in the object's enterprise reads it, only its creator changes it.

A missing, malformed, foreign or template object gets the same 403, so nothing tells whether it exists.
Each route keeps its own sign-in check (401) and any route-specific check before or after this one.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from product.identity import Principal, can_modify, can_read


def object_denied(store, principal: Principal, object_id: str, *, modify: bool = False, kind: str | None = None) -> JSONResponse | None:
    resource = store.resource(object_id)
    if resource is None or (kind is not None and resource.kind != kind) or not (can_modify if modify else can_read)(principal, resource):
        return JSONResponse({"error": "access_denied"}, status_code=403)
    return None
